from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError

from src.audit.actions import AuditAction
from src.billing.accounting_protocol import AccountingOperationHandle
from src.metrics.counters import increment_optional_request_diagnostic
from src.models.errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    PermissionDeniedError,
    ProxyError,
)
from src.routers.audit_helpers import emit_audit_event
from src.telemetry.spend_operation import billing_write_context
from src.billing.spend_operations import SpendPersistenceUnavailable
from src.db.accounting_protocol import AccountingProtocolUnavailable
from src.metrics.accounting import increment_accounting_failure

logger = logging.getLogger(__name__)
_ACCOUNTING_WRITE_FAILURES_LOGGED: set[str] = set()

_REQUEST_LOG_EMITTED_ATTR = "_request_log_emitted"
_REQUEST_FAILURE_CONTEXT_ATTR = "_request_failure_context"


@dataclass(slots=True)
class RequestFailureContext:
    call_type: str
    model: str | None = None
    request_start: float | None = None
    audit_action: str | AuditAction | None = None


@dataclass(frozen=True, slots=True)
class _GatewayRouteDefinition:
    call_type: str
    audit_action: str | AuditAction | None = None


_GATEWAY_ROUTE_DEFINITIONS: dict[str, _GatewayRouteDefinition] = {
    "/v1/chat/completions": _GatewayRouteDefinition(
        call_type="completion", audit_action="CHAT_COMPLETION_REQUEST"
    ),
    "/v1/completions": _GatewayRouteDefinition(
        call_type="completion", audit_action="COMPLETION_REQUEST"
    ),
    "/v1/responses": _GatewayRouteDefinition(
        call_type="completion", audit_action="RESPONSES_REQUEST"
    ),
    "/v1/embeddings": _GatewayRouteDefinition(
        call_type="embedding", audit_action=AuditAction.EMBEDDING_REQUEST
    ),
    "/v1/images/generations": _GatewayRouteDefinition(
        call_type="image_generation", audit_action=AuditAction.IMAGE_GENERATION_REQUEST
    ),
    "/v1/audio/speech": _GatewayRouteDefinition(
        call_type="audio_speech", audit_action=AuditAction.AUDIO_SPEECH_REQUEST
    ),
    "/v1/audio/transcriptions": _GatewayRouteDefinition(
        call_type="audio_transcription", audit_action=AuditAction.AUDIO_TRANSCRIPTION_REQUEST
    ),
    "/v1/rerank": _GatewayRouteDefinition(
        call_type="rerank", audit_action=AuditAction.RERANK_REQUEST
    ),
}


def seed_request_failure_context(
    request: Request,
    *,
    call_type: str,
    model: str | None = None,
    request_start: float | None = None,
    audit_action: str | AuditAction | None = None,
) -> None:
    setattr(
        request.state,
        _REQUEST_FAILURE_CONTEXT_ATTR,
        RequestFailureContext(
            call_type=call_type,
            model=model,
            request_start=request_start,
            audit_action=audit_action,
        ),
    )


def mark_request_log_emitted(request: Request) -> None:
    setattr(request.state, _REQUEST_LOG_EMITTED_ATTR, True)


async def enqueue_request_log_write(request: Request, coro: Awaitable[None]) -> None:
    """Finalization belongs to the request in both legacy and durable modes."""
    mark_request_log_emitted(request)
    try:
        await coro
    except Exception as exc:
        reason = _request_log_failure_reason(exc)
        increment_accounting_failure("finalization", "telemetry", reason)
        if reason not in _ACCOUNTING_WRITE_FAILURES_LOGGED:
            _ACCOUNTING_WRITE_FAILURES_LOGGED.add(reason)
            logger.warning("required request telemetry failed reason=%s", reason)
        raise SpendPersistenceUnavailable() from None


def _request_log_failure_reason(exc: Exception) -> str:
    if isinstance(exc, AccountingProtocolUnavailable):
        return exc.reason
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (TypeError, ValueError)):
        return "invalid_payload"
    return "unknown"


async def maybe_log_proxy_error(request: Request, exc: ProxyError) -> None:
    if _request_log_already_emitted(request):
        return
    route = _route_definition(request)
    auth = getattr(request.state, "user_api_key", None)
    spend_tracking_service = getattr(request.app.state, "spend_tracking_service", None)
    if route is None or auth is None or spend_tracking_service is None:
        return

    context = _request_failure_context(request)
    metadata = {
        "route": request.url.path,
        "request_method": request.method,
        "failure_stage": "preflight",
    }
    if uses_optional_accounting_v2_diagnostics(request):
        record_optional_accounting_v2_diagnostic(
            request,
            status_code=int(getattr(exc, "status_code", 500) or 500),
        )
    else:
        await enqueue_request_log_write(
            request,
            spend_tracking_service.log_request_failure(
                **billing_write_context(request),
                request_id=request.headers.get("x-request-id") or "",
                api_key=getattr(auth, "api_key", None) or "anonymous",
                user_id=getattr(auth, "user_id", None),
                team_id=getattr(auth, "team_id", None),
                organization_id=getattr(auth, "organization_id", None),
                owner_account_id=getattr(auth, "owner_account_id", None),
                end_user_id=None,
                model=(context.model if context is not None and context.model else None)
                or "(unknown)",
                call_type=(context.call_type if context is not None else route.call_type),
                metadata=metadata,
                cache_hit=False,
                http_status_code=int(getattr(exc, "status_code", 500) or 500),
                exc=exc,
            ),
        )

    if not requires_preflight_audit(exc):
        return
    if context is None or context.request_start is None:
        return
    await emit_audit_event(
        request=request,
        request_start=context.request_start,
        action=context.audit_action or route.audit_action or "GATEWAY_REQUEST",
        status="error",
        actor_type="api_key",
        actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
        organization_id=getattr(auth, "organization_id", None),
        api_key=getattr(auth, "api_key", None),
        resource_type="model",
        resource_id=context.model,
        error=exc,
        metadata=metadata,
    )


async def maybe_log_request_validation_failure(
    request: Request, exc: RequestValidationError
) -> None:
    if _request_log_already_emitted(request):
        return
    route = _route_definition(request)
    spend_tracking_service = getattr(request.app.state, "spend_tracking_service", None)
    if route is None or spend_tracking_service is None:
        return

    auth = await _resolve_request_auth(request)
    if auth is None:
        return

    context = _request_failure_context(request)
    errors = exc.errors()
    first_error_type = _first_validation_error_type(errors)
    metadata = {
        "route": request.url.path,
        "request_method": request.method,
        "failure_stage": "request_validation",
        "content_type": request.headers.get("content-type"),
        "error": {
            "code": first_error_type,
            "message": "Request validation failed",
        },
        "validation": _validation_metadata(errors),
    }
    if uses_optional_accounting_v2_diagnostics(request):
        record_optional_accounting_v2_diagnostic(request, status_code=422)
    else:
        await enqueue_request_log_write(
            request,
            spend_tracking_service.log_request_failure(
                **billing_write_context(request),
                request_id=request.headers.get("x-request-id") or "",
                api_key=getattr(auth, "api_key", None) or "anonymous",
                user_id=getattr(auth, "user_id", None),
                team_id=getattr(auth, "team_id", None),
                organization_id=getattr(auth, "organization_id", None),
                owner_account_id=getattr(auth, "owner_account_id", None),
                end_user_id=None,
                model=(context.model if context is not None and context.model else None)
                or "(unknown)",
                call_type=(context.call_type if context is not None else route.call_type),
                metadata=metadata,
                cache_hit=False,
                http_status_code=422,
                exc=None,
                error_type="request_validation_error",
            ),
        )


def _request_log_already_emitted(request: Request) -> bool:
    return bool(getattr(request.state, _REQUEST_LOG_EMITTED_ATTR, False))


def uses_optional_accounting_v2_diagnostics(request: Request) -> bool:
    """Keep pre-provider diagnostics out of the durable accounting data path."""
    if not bool(getattr(request.app.state, "accounting_protocol_enabled", False)):
        return False
    return not isinstance(
        getattr(request.state, "spend_operation_handle", None),
        AccountingOperationHandle,
    )


def record_optional_accounting_v2_diagnostic(
    request: Request,
    *,
    status_code: int,
) -> None:
    """Record one bounded no-dispatch diagnostic without another durable write."""
    mark_request_log_emitted(request)
    increment_optional_request_diagnostic(
        route=_optional_diagnostic_route(request.url.path),
        reason=_optional_diagnostic_reason(status_code),
    )


def _optional_diagnostic_reason(status_code: int) -> str:
    if status_code in {400, 401, 403, 404, 409, 413, 422, 429}:
        return "client_rejection"
    if status_code in {408, 502, 503, 504}:
        return "dependency_unavailable"
    return "internal_error"


def _optional_diagnostic_route(path: str) -> str:
    return {
        "/v1/chat/completions": "chat_completions",
        "/v1/completions": "completions",
        "/v1/responses": "responses",
        "/v1/embeddings": "embeddings",
        "/v1/images/generations": "images",
        "/v1/audio/speech": "audio",
        "/v1/audio/transcriptions": "audio",
        "/v1/rerank": "rerank",
    }.get(path, "other")


async def _resolve_request_auth(request: Request) -> Any | None:
    auth = getattr(request.state, "user_api_key", None)
    if auth is not None:
        return auth

    authorization = request.headers.get("authorization")
    if not authorization:
        return None

    try:
        from src.middleware.auth import authenticate_request

        return await authenticate_request(request, authorization=authorization)
    except Exception:
        return None


def _request_failure_context(request: Request) -> RequestFailureContext | None:
    context = getattr(request.state, _REQUEST_FAILURE_CONTEXT_ATTR, None)
    if isinstance(context, RequestFailureContext):
        return context
    return None


def _route_definition(request: Request) -> _GatewayRouteDefinition | None:
    return _GATEWAY_ROUTE_DEFINITIONS.get(request.url.path)


def requires_preflight_audit(exc: Exception) -> bool:
    return isinstance(exc, (ApprovalRequiredError, BudgetExceededError, PermissionDeniedError))


def _first_validation_error_type(errors: list[dict[str, Any]]) -> str:
    for error in errors:
        value = str(error.get("type") or "").strip()
        if value:
            return value
    return "validation_error"


def _validation_metadata(errors: list[dict[str, Any]]) -> dict[str, Any]:
    summarized: list[dict[str, Any]] = []
    for error in errors[:5]:
        summarized.append(
            {
                "type": str(error.get("type") or "validation_error"),
                "loc": [str(item) for item in error.get("loc") or []],
                "msg": str(error.get("msg") or ""),
            }
        )
    return {
        "error_count": len(errors),
        "errors": summarized,
    }
