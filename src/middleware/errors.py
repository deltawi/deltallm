from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.guardrails.exceptions import GuardrailViolationError
from src.models.errors import ProxyError, RateLimitError

logger = logging.getLogger(__name__)


def _serialize_error(exc: ProxyError) -> dict[str, object]:
    payload: dict[str, object] = {
        "error": {
            "message": exc.message,
            "type": exc.error_type,
            "param": getattr(exc, "param", None),
            "code": getattr(exc, "code", None),
        }
    }
    if isinstance(exc, GuardrailViolationError):
        payload["error"]["guardrail"] = exc.guardrail_name
    return payload


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProxyError)
    async def proxy_error_handler(_: Request, exc: ProxyError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimitError) and getattr(exc, "retry_after", None):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(status_code=exc.status_code, content=_serialize_error(exc), headers=headers)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", exc_info=exc)
        proxy_error = ProxyError()
        return JSONResponse(status_code=proxy_error.status_code, content=_serialize_error(proxy_error))
