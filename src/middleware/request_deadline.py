"""Inference deadline starts before admission, upload, authentication and cache."""

from __future__ import annotations

import asyncio

from starlette._utils import get_route_path
from starlette.datastructures import State
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.ingress import IngressClass, ingress_class
from src.metrics.request_work import request_deadline_expirations
from src.middleware.errors import anthropic_error_response
from src.models.errors import TimeoutError as ProxyTimeoutError
from src.request_deadline import RequestDeadline, bind_request_deadline, request_timeout_error
from src.router.runtime_generation import pin_routing_runtime_generation


class RequestDeadlineMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or ingress_class(get_route_path(scope), scope.get("method", ""))
            != IngressClass.INFERENCE
        ):
            await self.app(scope, receive, send)
            return
        application = scope["app"]
        state = State(scope.setdefault("state", {}))
        runtime = pin_routing_runtime_generation(application.state, state)
        deadline = RequestDeadline.after(runtime.failover_config.timeout)
        started = False

        async def send_response(message: Message) -> None:
            nonlocal started
            budget.deadline.require_remaining()
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        timer = asyncio.timeout_at(deadline.expires_at)
        with bind_request_deadline(deadline, reschedule=timer.reschedule) as budget:
            try:
                async with timer:
                    await self.app(scope, receive, send_response)
            except (TimeoutError, ProxyTimeoutError):
                if not timer.expired() and budget.deadline.remaining() > 0:
                    raise
                request_deadline_expirations.labels("started" if started else "not_started").inc()
                if started:
                    # ASGI must abort an incomplete response; never emit a success
                    # marker or attempt another response/provider after bytes were sent.
                    raise request_timeout_error() from None
                error = request_timeout_error()
                response = JSONResponse(
                    {
                        "error": {
                            "message": error.message,
                            "type": error.error_type,
                            "param": error.param,
                            "code": error.code,
                        }
                    },
                    status_code=error.status_code,
                )
                if get_route_path(scope).rstrip("/") in {"/messages", "/v1/messages"}:
                    response = anthropic_error_response(
                        status_code=error.status_code, message=error.message
                    )
                # Sending the timeout itself has a separate small transport grace.
                async with asyncio.timeout(1):
                    await response(scope, receive, send)
