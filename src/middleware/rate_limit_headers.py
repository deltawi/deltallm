from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.middleware.rate_limit import RateLimitState, build_rate_limit_headers


class RateLimitHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        state: RateLimitState | None = getattr(request.state, "_rate_limit_state", None)
        if state is None:
            return response

        headers = build_rate_limit_headers(state)

        if response.status_code == 429:
            headers["x-ratelimit-remaining-requests"] = "0"

        for key, value in headers.items():
            response.headers[key] = value

        return response
