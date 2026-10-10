"""Resolve one request ID before authentication, caches, and accounting."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.request_identity import resolve_request_id


class RequestIdentityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        supplied = [value for key, value in headers if key.lower() == b"x-request-id"]
        candidate = supplied[0].decode("latin-1") if len(supplied) == 1 else None
        request_id = resolve_request_id(candidate).encode("ascii")
        scope["headers"] = [
            (key, value) for key, value in headers if key.lower() != b"x-request-id"
        ] + [(b"x-request-id", request_id)]

        async def send_with_identity(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id)]
            await send(message)

        await self.app(scope, receive, send_with_identity)
