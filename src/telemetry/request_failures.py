from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError

from src.audit.actions import AuditAction
from src.models.errors import ApprovalRequiredError, BudgetExceededError, PermissionDeniedError, ProxyError
from src.routers.audit_helpers import emit_audit_event
from src.routers.utils import fire_and_forget

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
    "/v1/chat/completions": _GatewayRouteDefinition(call_type="completion", audit_action="CHAT_COMPLETION_REQUEST"),
    "/v1/completions": _GatewayRouteDefinition(call_type="completion", audit_action="COMPLETION_REQUEST"),
    "/v1/responses": _GatewayRouteDefinition(call_type="completion", audit_action="RESPONSES_REQUEST"),
    "/v1/embeddings": _GatewayRouteDefinition(call_type="embedding", audit_action=AuditAction.EMBEDDING_REQUEST),
    "/v1/images/generations": _GatewayRouteDefinition(call_type="image_generation", audit_action=AuditAction.IMAGE_GENERATION_REQUEST),
    "/v1/audio/speech": _GatewayRouteDefinition(call_type="audio_speech", audit_action=AuditAction.AUDIO_SPEECH_REQUEST),
    "/v1/audio/transcriptions": _GatewayRouteDefinition(call_type="audio_transcription", audit_action=AuditAction.AUDIO_TRANSCRIPTION_REQUEST),
    "/v1/rerank": _GatewayRouteDefinition(call_type="rerank", audit_action=AuditAction.RERANK_REQUEST),
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


def enqueue_request_log_write(request: Request, coro: Any) -> None:
    mark_request_log_emitted(request)
    fire_and_forget(coro)


def maybe_log_proxy_error(request: Request, exc: ProxyError) -> None:
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
    enqueue_request_log_write(
        request,
        spend_tracking_service.log_request_failure(
            request_id=request.headers.get("x-request-id") or "",
            api_key=getattr(auth, "api_key", None) or "anonymous",
            user_id=getattr(auth, "user_id", None),
            team_id=getattr(auth, "team_id", None),
            organization_id=getattr(auth, "organization_id", None),
            end_user_id=None,
            model=(context.model if context is not None and context.model else None) or "(unknown)",
            call_type=(context.call_type if context is not None else route.call_type),
            metadata=metadata,
            cache_hit=False,
            http_status_code=int(getattr(exc, "status_code", 500) or 500),
            exc=exc,
        ),
    )

    if not _should_emit_preflight_audit(exc):
        return
    if context is None or context.request_start is None:
        return
    emit_audit_event(
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


async def maybe_log_request_validation_failure(request: Request, exc: RequestValidationError) -> None:
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
    enqueue_request_log_write(
        request,
        spend_tracking_service.log_request_failure(
            request_id=request.headers.get("x-request-id") or "",
            api_key=getattr(auth, "api_key", None) or "anonymous",
            user_id=getattr(auth, "user_id", None),
            team_id=getattr(auth, "team_id", None),
            organization_id=getattr(auth, "organization_id", None),
            end_user_id=None,
            model=(context.model if context is not None and context.model else None) or "(unknown)",
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


def _should_emit_preflight_audit(exc: ProxyError) -> bool:
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
