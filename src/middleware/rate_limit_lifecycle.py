from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.middleware.rate_limit import _release_rate_limits


class RateLimitLeaseLifecycleMiddleware:
    """Own HTTP rate-limit leases until the response body is finished.

    ``BaseHTTPMiddleware.call_next`` returns as soon as response headers are
    available, which is too early for streaming max-parallel leases.  This
    pure ASGI middleware observes the final body frame instead and keeps a
    ``finally`` fallback for disconnects, cancellations, and failures that do
    not produce a complete response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = _scope_state(scope)
        state["_rate_limit_lifecycle_managed"] = True
        request = Request(scope, receive=receive)
        response_finished = False

        async def send_with_release(message: Message) -> None:
            nonlocal response_finished
            await send(message)
            if message["type"] != "http.response.body" or message.get("more_body", False):
                return
            response_finished = True
            await _release_rate_limits(request)

        try:
            await self.app(scope, receive, send_with_release)
        finally:
            if not response_finished:
                await _release_rate_limits(request)


def _scope_state(scope: Scope) -> MutableMapping[str, Any]:
    state = scope.get("state")
    if isinstance(state, MutableMapping):
        return state
    created: MutableMapping[str, Any] = {}
    scope["state"] = created
    return created
