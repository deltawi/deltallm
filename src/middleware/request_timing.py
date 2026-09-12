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


class RequestTimingMiddleware:
    """Observe the complete HTTP body lifecycle, including streaming responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500
        response_kind = "nonstream"
        observed = False
        first_body = False
        completed_at: float | None = None
        route = _route_label(scope)
        active = request_in_flight.labels(route)
        active.inc()

        async def receive_with_timing() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                request_bytes.labels(route).inc(len(message.get("body", b"")))
            return message

        async def send_with_timing(message: Message) -> None:
            nonlocal observed, response_kind, status_code, first_body, completed_at
            if message["type"] == "http.response.start":
                status_code = int(message.get("status") or 500)
                headers = message.get("headers") or []
                if any(
                    key.lower() == b"content-type" and b"text/event-stream" in value.lower()
                    for key, value in headers
                ):
                    response_kind = "stream"
            await send(message)
            if message["type"] == "http.response.body":
                body_size = len(message.get("body", b""))
                response_bytes.labels(route).inc(body_size)
                if body_size and not first_body:
                    first_body = True
                    observe_request_phase(
                        route=route,
                        phase="response_first_body",
                        outcome=_outcome(status_code),
                        response_kind=response_kind,
                        latency_seconds=perf_counter() - started,
                    )
            if (
                not observed
                and message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                observed = True
                completed_at = perf_counter()
                active.dec()
                observe_request_phase(
                    route=route,
                    phase="response_total",
                    outcome=_outcome(status_code),
                    response_kind=response_kind,
                    latency_seconds=completed_at - started,
                )

        application_outcome = "success"
        try:
            await self.app(scope, receive_with_timing, send_with_timing)
        except BaseException as exc:
            application_outcome = (
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            )
            raise
        finally:
            finished = perf_counter()
            if not observed:
                active.dec()
                observe_request_phase(
                    route=route,
                    phase="response_total",
                    outcome="cancelled_or_error",
                    response_kind=response_kind,
                    latency_seconds=finished - started,
                )
            if completed_at is not None:
                observe_request_phase(
                    route=route,
                    phase="after_response",
                    outcome=application_outcome,
                    response_kind=response_kind,
                    latency_seconds=finished - completed_at,
                )
            observe_request_phase(
                route=route,
                phase="application_total",
                outcome=application_outcome,
                response_kind=response_kind,
                latency_seconds=finished - started,
            )


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
