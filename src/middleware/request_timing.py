from __future__ import annotations

from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.metrics import observe_request_phase


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

        async def send_with_timing(message: Message) -> None:
            nonlocal observed, response_kind, status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status") or 500)
                headers = message.get("headers") or []
                if any(
                    key.lower() == b"content-type" and b"text/event-stream" in value.lower()
                    for key, value in headers
                ):
                    response_kind = "stream"
            await send(message)
            if (
                not observed
                and message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                observed = True
                observe_request_phase(
                    route=_route_label(scope),
                    phase="response_total",
                    outcome=_outcome(status_code),
                    response_kind=response_kind,
                    latency_seconds=perf_counter() - started,
                )

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            if not observed:
                observe_request_phase(
                    route=_route_label(scope),
                    phase="response_total",
                    outcome="cancelled_or_error",
                    response_kind=response_kind,
                    latency_seconds=perf_counter() - started,
                )


def _route_label(scope: Scope) -> str:
    route = scope.get("route")
    path = str(getattr(route, "path", "") or scope.get("path") or "")
    if path in {"/v1/chat/completions", "/chat/completions"}:
        return "chat_completions"
    if path in {"/v1/responses", "/responses"}:
        return "responses"
    if path in {"/v1/embeddings", "/embeddings"}:
        return "embeddings"
    if path.startswith("/v1/audio/"):
        return "audio"
    if path.startswith("/v1/images"):
        return "images"
    if path == "/metrics":
        return "metrics"
    if path.startswith("/health"):
        return "health"
    return "other"


def _outcome(status_code: int) -> str:
    if 200 <= status_code < 400:
        return "success"
    if status_code == 429:
        return "rate_limited"
    if 400 <= status_code < 500:
        return "client_error"
    return "server_error"
