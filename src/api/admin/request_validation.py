from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response


class BadRequestValidationRoute(APIRoute):
    """Map FastAPI request validation to an endpoint's established HTTP 400 contract."""

    def validation_error_detail(self, exc: RequestValidationError) -> object:
        return exc.errors()

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        route_handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=self.validation_error_detail(exc),
                ) from exc

        return handle


class PolicyBadRequestValidationRoute(BadRequestValidationRoute):
    """Keep policy input failures at 400 without echoing submitted policy content."""

    def validation_error_detail(self, exc: RequestValidationError) -> str:
        return "Invalid route policy request fields or types"
