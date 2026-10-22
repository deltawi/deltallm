from __future__ import annotations

import logging
import traceback

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

from src.services.admission.output_limit_redis import OutputUsageUnknownError

logger = logging.getLogger(__name__)

_ANTHROPIC_MESSAGES_PATHS = frozenset({"/v1/messages", "/v1/messages/count_tokens"})


def serialize_proxy_error(exc: ProxyError) -> dict[str, object]:
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


def _short_source_path(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    for marker in ("/site-packages/", "/dist-packages/"):
        if marker in normalized:
            return normalized.rsplit(marker, 1)[1]
    if "/src/" in normalized:
        return "src/" + normalized.rsplit("/src/", 1)[1]
    return normalized.rsplit("/", 1)[-1]


def _frame_label(frame: traceback.FrameSummary | None) -> str:
    if frame is None:
        return "unknown"
    return f"{_short_source_path(frame.filename)}:{frame.lineno} in {frame.name}"


def _unhandled_exception_context(request: Request, exc: BaseException) -> dict[str, str | None]:
    """Describe where an unhandled error came from without its message, locals or arguments.

    Exception text can carry credentials or user content, so only the exception type, the
    request's route template and code locations are logged.
    """

    frames = traceback.extract_tb(exc.__traceback__)
    app_frames = [
        frame for frame in frames if _short_source_path(frame.filename).startswith("src/")
    ]
    # The raw URL can carry IDs, so an error raised before routing logs no path at all.
    route = getattr(request.scope.get("route"), "path", None) or "(unmatched route)"
    # Only an explicit ``raise ... from`` cause. Starlette's middleware re-raises errors from its
    # task group with the group as cause, which says nothing about the failure.
    cause = exc.__cause__
    if isinstance(cause, BaseExceptionGroup):
        cause = None
    return {
        "method": request.method,
        "route": route,
        "request_id": request.headers.get("x-request-id"),
        "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "cause_type": type(cause).__qualname__ if cause is not None else None,
        "raised_at": _frame_label(frames[-1] if frames else None),
        "app_frame": _frame_label(app_frames[-1] if app_frames else None),
    }


def _retry_after_headers(exc: ProxyError) -> dict[str, str]:
    # Provider 5xx hints are for batch retry, not interactive clients.
    if isinstance(exc, (RateLimitError, AuthenticationUnavailableError, OutputUsageUnknownError)):
        if exc.retry_after is not None:
            return {"Retry-After": str(exc.retry_after)}
    return {}


def proxy_error_response(exc: ProxyError) -> JSONResponse:
    """Build the canonical HTTP response for a gateway error."""
    return JSONResponse(
        status_code=exc.status_code,
        content=serialize_proxy_error(exc),
        headers=_retry_after_headers(exc),
    )


def anthropic_proxy_error_response(exc: ProxyError) -> JSONResponse:
    """Render a sanitized gateway failure in the Anthropic Messages dialect."""
    return anthropic_error_response(
        status_code=exc.status_code,
        message=exc.message,
        headers=_retry_after_headers(exc),
    )


def _uses_anthropic_error_dialect(request: Request) -> bool:
    return request.url.path.rstrip("/") in _ANTHROPIC_MESSAGES_PATHS


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
        context = _unhandled_exception_context(request, exc)
        logger.error(
            "unhandled exception: %s %s request_id=%s error_type=%s%s raised_at=%s app_frame=%s",
            context["method"],
            context["route"],
            context["request_id"],
            context["error_type"],
            f" cause_type={context['cause_type']}" if context["cause_type"] else "",
            context["raised_at"],
            context["app_frame"],
            extra=context,
        )
        response = _proxy_error_response_for_request(request, ProxyError())
        # This handler runs outside the request-identity middleware, so the ID it would have
        # added to the response is attached here to let a reported 500 be matched to its log.
        if context["request_id"]:
            response.headers["X-Request-ID"] = context["request_id"]
        return response
