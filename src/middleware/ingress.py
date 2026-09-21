"""Dependency-free HTTP admission with bounded upload buffering."""

from __future__ import annotations

import asyncio
from time import perf_counter

from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.concurrency import CapacityGateFull, CapacityGateTimedOut
from src.ingress import (
    IngressBodyLimit,
    IngressBufferFull,
    IngressClass,
    IngressRuntime,
    ingress_class,
)
from src.metrics.admission import (
    ingress_active,
    ingress_waiters,
    ingress_queue_seconds,
    ingress_rejections,
)
from src.middleware.errors import anthropic_error_response
from src.process_lifecycle import ProcessLifecycle


class _BufferedBody:
    def __init__(self, runtime: IngressRuntime, allocation: IngressClass) -> None:
        self.runtime = runtime
        self.allocation = allocation
        self.size = 0
        self.body: bytes | None = None

    async def read(self, receive: Receive) -> bool:
        buffer = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return False
            chunk = message.get("body", b"")
            if self.size + len(chunk) > self.runtime.limits.max_body_bytes:
                raise IngressBodyLimit
            self.runtime.reserve_bytes(len(chunk), self.allocation)
            self.size += len(chunk)
            buffer.extend(chunk)
            if not message.get("more_body", False):
                self.body = bytes(buffer)
                return True

    def replay(self, receive: Receive) -> Receive:
        async def wrapped() -> Message:
            if self.body is not None:
                body, self.body = self.body, None
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return wrapped

    def close(self) -> None:
        self.body = None
        self.runtime.release_bytes(self.size, self.allocation)
        self.size = 0


class IngressMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        application = scope.get("app")
        runtime: IngressRuntime | None = getattr(
            getattr(application, "state", None), "ingress_runtime", None
        )
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        allocation = ingress_class(get_route_path(scope), scope.get("method", ""))
        lifecycle: ProcessLifecycle | None = getattr(
            getattr(application, "state", None), "process_lifecycle", None
        )
        if lifecycle is not None and lifecycle.draining and allocation != IngressClass.HEALTH:
            await _reject(scope, receive, send, 503, "gateway_draining")
            return
        if runtime is None or not runtime.limits.enabled:
            await self.app(scope, receive, send)
            return
        gate = runtime.gate(allocation)
        started, outcome = perf_counter(), "admitted"
        ingress_waiters.labels(allocation.value).inc()
        try:
            await gate.acquire(timeout_seconds=runtime.limits.queue_timeout_ms / 1000)
        except (CapacityGateFull, CapacityGateTimedOut):
            outcome = "rejected"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            ingress_waiters.labels(allocation.value).dec()
            ingress_queue_seconds.labels(allocation.value, outcome).observe(
                perf_counter() - started
            )
        if outcome == "rejected":
            await _reject(scope, receive, send, 503, "gateway_ingress_full")
            return
        body = _BufferedBody(runtime, allocation)
        ingress_active.labels(allocation.value).inc()
        try:
            # A request queued before the signal has not yet been admitted.
            if lifecycle is not None and lifecycle.draining and allocation != IngressClass.HEALTH:
                await _reject(scope, receive, send, 503, "gateway_draining")
                return
            await self._admitted(
                scope, receive, send, runtime, body, health=allocation == IngressClass.HEALTH
            )
        finally:
            body.close()
            # BoundedCapacityGate never suspends while holding its condition
            # lock. This release cannot strand an untracked shield task.
            await gate.release()
            ingress_active.labels(allocation.value).dec()

    async def _admitted(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        runtime: IngressRuntime,
        body: _BufferedBody,
        *,
        health: bool,
    ) -> None:
        try:
            length = _content_length(scope)
        except ValueError:
            await _reject(scope, receive, send, 400, "invalid_content_length")
            return
        if length is not None and length > runtime.limits.max_body_bytes:
            await _reject(scope, receive, send, 413, "gateway_request_body_too_large")
            return
        if health:
            # Health/metrics do not consume request bodies; retain server read
            # backpressure and keep their allocation independent of uploads.
            await self.app(scope, receive, send)
            return
        try:
            async with asyncio.timeout(runtime.limits.body_timeout_seconds):
                connected = await body.read(receive)
        except IngressBodyLimit:
            await _reject(scope, receive, send, 413, "gateway_request_body_too_large")
            return
        except IngressBufferFull:
            await _reject(scope, receive, send, 503, "gateway_ingress_buffer_full")
            return
        except TimeoutError:
            await _reject(scope, receive, send, 408, "gateway_request_body_timeout")
            return
        if connected:
            if length is not None and body.size != length:
                await _reject(scope, receive, send, 400, "invalid_content_length")
                return
            # Keep capacity through the final response and application cleanup.
            # Disconnect must not free capacity while retained work is still live.
            await self.app(scope, body.replay(receive), send)


def _content_length(scope: Scope) -> int | None:
    values = [value for key, value in scope.get("headers", ()) if key.lower() == b"content-length"]
    if not values:
        return None
    if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 20:
        raise ValueError("invalid content length")
    return int(values[0])


async def _reject(scope: Scope, receive: Receive, send: Send, status: int, code: str) -> None:
    allocation = ingress_class(get_route_path(scope), scope.get("method", ""))
    ingress_rejections.labels(allocation.value, code).inc()
    headers = {"Retry-After": "1"} if status == 503 else {}
    if scope.get("http_version", "1.1").startswith("1."):
        headers["Connection"] = "close"
    response = JSONResponse(
        {
            "error": {
                "message": "Gateway request admission failed",
                "type": "gateway_error",
                "code": code,
            }
        },
        status_code=status,
        headers=headers,
    )
    if get_route_path(scope).rstrip("/") in {"/v1/messages", "/messages"}:
        response = anthropic_error_response(
            status_code=status, message="Gateway request admission failed", headers=headers
        )
    await response(scope, receive, send)
