from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler as fastapi_http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from prisma.errors import RawQueryError

from src.db.catalog.managed_assets import ManagedAssetAudienceNotFoundError
from src.guardrails.exceptions import GuardrailViolationError
from src.models.errors import (
    ApprovalRequiredError,
    AuthenticationUnavailableError,
    InvalidRequestError,
    ProxyError,
    RateLimitError,
    ServiceUnavailableError,
)
from src.middleware.error_responses import (
    anthropic_error_payload as anthropic_error_payload,
    anthropic_error_response as anthropic_error_response,
)
from src.billing.spend.spend_operations import SpendPersistenceUnavailable
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
    if (
        isinstance(exc, (RateLimitError, AuthenticationUnavailableError, ServiceUnavailableError))
        and retry_after is not None
    ):
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=exc.status_code, content=_serialize_error(exc), headers=headers)


def anthropic_proxy_error_response(exc: ProxyError) -> JSONResponse:
    """Render a sanitized gateway failure in the Anthropic Messages dialect."""

    headers = {}
    retry_after = getattr(exc, "retry_after", None)
    if (
        isinstance(exc, (RateLimitError, AuthenticationUnavailableError, ServiceUnavailableError))
        and retry_after is not None
    ):
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
    @app.exception_handler(RawQueryError)
    async def budget_policy_error_handler(request: Request, exc: RawQueryError) -> JSONResponse:
        if (
            isinstance(exc.meta, dict)
            and exc.meta.get("code") == "55000"
            and str(exc) == "accounting_budget_policy_requires_drain"
        ):
            return JSONResponse(
                status_code=409,
                content={
                    "detail": (
                        "Pause affected inference and drain its permits before adding a new "
                        "hard-budget scope. Resolve uncertain usage first. "
                        "Older grants without scope data require a generation drain."
                    ),
                    "code": "budget_policy_requires_drain",
                },
            )
        if (
            isinstance(exc.meta, dict)
            and exc.meta.get("code") == "P0001"
            and str(exc) == "accounting_budget_policy_below_debits"
        ):
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "The hard budget cannot be lower than its existing spend and holds.",
                    "code": "budget_policy_below_debits",
                },
            )
        return await unhandled_error_handler(request, exc)

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
        try:
            await maybe_log_proxy_error(request, exc)
        except SpendPersistenceUnavailable as persistence_error:
            return _proxy_error_response_for_request(request, persistence_error)
        return _proxy_error_response_for_request(request, exc)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        try:
            await maybe_log_request_validation_failure(request, exc)
        except SpendPersistenceUnavailable as persistence_error:
            return _proxy_error_response_for_request(request, persistence_error)
        if _uses_anthropic_error_dialect(request):
            return anthropic_proxy_error_response(InvalidRequestError(message="Invalid request"))
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    @app.exception_handler(ManagedAssetAudienceNotFoundError)
    async def managed_asset_audience_error_handler(
        _request: Request, exc: ManagedAssetAudienceNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled exception", extra={"error_type": type(exc).__name__})
        proxy_error = ProxyError()
        return _proxy_error_response_for_request(request, proxy_error)
