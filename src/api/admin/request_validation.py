from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response


class BadRequestValidationRoute(APIRoute):
    """Map FastAPI request validation to an endpoint's established HTTP 400 contract."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        route_handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=exc.errors(),
                ) from exc

        return handle
