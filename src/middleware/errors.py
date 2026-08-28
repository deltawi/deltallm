from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler as fastapi_http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.guardrails.exceptions import GuardrailViolationError
from src.models.errors import ApprovalRequiredError, InvalidRequestError, ProxyError, RateLimitError
from src.telemetry.request_failures import (
    maybe_log_proxy_error,
    maybe_log_request_validation_failure,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_MESSAGES_PATH = "/v1/messages"


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
    if isinstance(exc, ApprovalRequiredError) and exc.approval_request_id:
        payload["error"]["approval_request_id"] = exc.approval_request_id
    return payload


def proxy_error_response(exc: ProxyError) -> JSONResponse:
    """Build the canonical HTTP response for a gateway error."""
    headers = {}
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(exc, RateLimitError) and retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=exc.status_code, content=_serialize_error(exc), headers=headers)


def _anthropic_error_type(status_code: int) -> str:
    if status_code == 400:
        return "invalid_request_error"
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 404:
        return "not_found_error"
    if status_code == 413:
        return "request_too_large"
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 503:
        return "overloaded_error"
    return "api_error"


def anthropic_error_response(
    *,
    status_code: int,
    message: str,
    error_type: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Render one error envelope for every Anthropic Messages failure boundary."""

    return JSONResponse(
        status_code=status_code,
        content=anthropic_error_payload(
            status_code=status_code,
            message=message,
            error_type=error_type,
        ),
        headers=headers or {},
    )


def anthropic_error_payload(
    *, status_code: int, message: str, error_type: str | None = None
) -> dict[str, object]:
    return {
        "type": "error",
        "error": {
            "type": error_type or _anthropic_error_type(status_code),
            "message": message,
        },
    }


def anthropic_proxy_error_response(exc: ProxyError) -> JSONResponse:
    """Render a sanitized gateway failure in the Anthropic Messages dialect."""

    headers = {}
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(exc, RateLimitError) and retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return anthropic_error_response(
        status_code=exc.status_code,
        message=exc.message,
        headers=headers,
    )


def _uses_anthropic_error_dialect(request: Request) -> bool:
    return request.url.path.rstrip("/") == _ANTHROPIC_MESSAGES_PATH


def _proxy_error_response_for_request(request: Request, exc: ProxyError) -> JSONResponse:
    if _uses_anthropic_error_dialect(request):
        return anthropic_proxy_error_response(exc)
    return proxy_error_response(exc)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
        if _uses_anthropic_error_dialect(request):
            return anthropic_error_response(
                status_code=exc.status_code,
                message=str(exc.detail),
                headers=dict(exc.headers or {}),
            )
        return await fastapi_http_exception_handler(request, exc)

    @app.exception_handler(ProxyError)
    async def proxy_error_handler(request: Request, exc: ProxyError) -> JSONResponse:
        await maybe_log_proxy_error(request, exc)
        return _proxy_error_response_for_request(request, exc)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        await maybe_log_request_validation_failure(request, exc)
        if _uses_anthropic_error_dialect(request):
            return anthropic_proxy_error_response(InvalidRequestError(message="Invalid request"))
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled exception", extra={"error_type": type(exc).__name__})
        proxy_error = ProxyError()
        return _proxy_error_response_for_request(request, proxy_error)
