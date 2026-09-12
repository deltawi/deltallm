from __future__ import annotations

import asyncio
from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.metrics import observe_request_phase
from src.metrics.request_phases import (
    request_bytes,
    request_in_flight,
    request_route,
    response_bytes,
)


class _HTTPObservation:
    def __init__(self, route: str) -> None:
        self.route = route
        self.started = perf_counter()
        self.status_code = 500
        self.response_kind = "nonstream"
        self.first_body = False
        self.completed_at: float | None = None
        self.active = request_in_flight.labels(route)
        self.active.inc()

    def observe(self, phase: str, outcome: str, duration: float) -> None:
        observe_request_phase(
            route=self.route,
            phase=phase,
            outcome=outcome,
            response_kind=self.response_kind,
            latency_seconds=duration,
        )

    def response_started(self, message: Message) -> None:
        self.status_code = int(message.get("status") or 500)
        if any(
            key.lower() == b"content-type" and b"text/event-stream" in value.lower()
            for key, value in message.get("headers", [])
        ):
            self.response_kind = "stream"

    def body_sent(self, message: Message) -> None:
        if self.completed_at is not None:
            # Servers may silently discard sends after a disconnect.
            return
        size = len(message.get("body", b""))
        response_bytes.labels(self.route).inc(size)
        if size and not self.first_body:
            self.first_body = True
            self.observe(
                "response_first_body", _outcome(self.status_code), perf_counter() - self.started
            )
        if not message.get("more_body", False):
            self.finish(_outcome(self.status_code))

    def finish(self, outcome: str) -> None:
        if self.completed_at is not None:
            return
        self.completed_at = perf_counter()
        self.active.dec()
        self.observe("response_total", outcome, self.completed_at - self.started)

    def application_finished(self, outcome: str) -> None:
        finished = perf_counter()
        if self.completed_at is None:
            self.finish("cancelled_or_error")
        else:
            self.observe("after_response", outcome, finished - self.completed_at)
        self.observe("application_total", outcome, finished - self.started)


class RequestTimingMiddleware:
    """Observe the complete HTTP body lifecycle, including streaming responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        observation = _HTTPObservation(_route_label(scope))

        async def receive_with_timing() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                request_bytes.labels(observation.route).inc(len(message.get("body", b"")))
            elif message["type"] == "http.disconnect":
                observation.finish("disconnected")
            return message

        async def send_with_timing(message: Message) -> None:
            if message["type"] == "http.response.start":
                observation.response_started(message)
            try:
                await send(message)
            except OSError:
                observation.finish("disconnected")
                raise
            except asyncio.CancelledError:
                observation.finish("cancelled")
                raise
            if message["type"] == "http.response.body":
                observation.body_sent(message)

        application_outcome = "success"
        try:
            await self.app(scope, receive_with_timing, send_with_timing)
        except BaseException as exc:
            application_outcome = (
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            )
            raise
        finally:
            observation.application_finished(application_outcome)


def _route_label(scope: Scope) -> str:
    route = scope.get("route")
    path = str(getattr(route, "path", "") or scope.get("path") or "")
    return request_route(path)


def _outcome(status_code: int) -> str:
    if 200 <= status_code < 400:
        return "success"
    if status_code == 429:
        return "rate_limited"
    if 400 <= status_code < 500:
        return "client_error"
    return "server_error"
