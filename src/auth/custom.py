from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request, status

from src.models.responses import UserAPIKeyAuth

CustomAuthFunction = Callable[[str, Request], Awaitable[UserAPIKeyAuth | dict[str, Any]]]


class CustomAuthManager:
    """Support loading user-provided auth handlers from module paths."""

    def __init__(self, handler_path: str | None = None) -> None:
        self._handler: CustomAuthFunction | None = None
        if handler_path:
            self.register(handler_path)

    def register(self, handler_path: str) -> None:
        module_path, func_name = handler_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        handler = getattr(module, func_name)
        if not callable(handler):
            raise TypeError(f"Custom auth target is not callable: {handler_path}")
        self._handler = handler

    async def authenticate(self, api_key: str, request: Request) -> UserAPIKeyAuth:
        if self._handler is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Custom auth not configured",
            )

        result = await self._handler(api_key, request)
        if isinstance(result, UserAPIKeyAuth):
            return result
        if isinstance(result, dict):
            return UserAPIKeyAuth.model_validate(result)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Custom auth handler returned invalid result",
        )
