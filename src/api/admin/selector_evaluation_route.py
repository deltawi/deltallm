from collections.abc import Awaitable, Callable
import asyncio

from fastapi import HTTPException, Request
from starlette.responses import Response

from src.api.admin.request_validation import PolicyBadRequestValidationRoute
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.services.selector_evaluation import MAX_EVALUATION_BYTES


class SelectorEvaluationRoute(PolicyBadRequestValidationRoute):
    """Authorize before bounded body buffering; never echo fixture validation input."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()
        authorize = require_admin_permission(Permission.CONFIG_READ)

        async def handle(request: Request) -> Response:
            await authorize(
                request, request.headers.get("authorization"), request.headers.get("x-master-key")
            )
            body = bytearray()
            try:
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > MAX_EVALUATION_BYTES:
                            raise HTTPException(
                                status_code=413, detail="Evaluation exceeds 256 KiB"
                            )
                        body.extend(chunk)
            except TimeoutError as exc:
                raise HTTPException(status_code=408, detail="Evaluation upload timed out") from exc
            request._body = bytes(body)
            return await handler(request)

        return handle
