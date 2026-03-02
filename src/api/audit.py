from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import Request

from src.auth.roles import Permission, has_platform_permission
from src.middleware.platform_auth import get_platform_auth_context
from src.services.audit_service import AuditEventInput, AuditPayloadInput, AuditService

_SENSITIVE_KEYS = {
    "password",
    "current_password",
    "new_password",
    "token",
    "raw_key",
    "api_key",
    "secret",
    "client_secret",
    "authorization",
    "x_master_key",
    "master_key",
}


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    if request.client and request.client.host:
        return request.client.host
    return None


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _SENSITIVE_KEYS or "password" in lowered or "secret" in lowered or "token" in lowered:
                redacted[str(key)] = "***REDACTED***"
            else:
                redacted[str(key)] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def _permission_context(request: Request, scope: Any | None = None) -> dict[str, Any]:
    if scope is not None:
        return {
            "is_platform_admin": bool(getattr(scope, "is_platform_admin", False)),
            "org_ids": list(getattr(scope, "org_ids", []) or []),
            "team_ids": list(getattr(scope, "team_ids", []) or []),
        }
    auth_ctx = get_platform_auth_context(request)
    if auth_ctx is None:
        return {"is_platform_admin": False, "org_ids": [], "team_ids": []}
    return {
        "is_platform_admin": has_platform_permission(getattr(auth_ctx, "role", None), Permission.PLATFORM_ADMIN),
        "org_ids": [str(item.get("organization_id")) for item in auth_ctx.organization_memberships if item.get("organization_id")],
        "team_ids": [str(item.get("team_id")) for item in auth_ctx.team_memberships if item.get("team_id")],
    }


async def emit_control_audit_event(
    *,
    request: Request,
    request_start: float,
    action: str,
    status: str,
    actor_type: str = "platform_account",
    actor_id: str | None = None,
    organization_id: str | None = None,
    api_key: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    scope: Any | None = None,
    metadata: dict[str, Any] | None = None,
    error: Exception | None = None,
    critical: bool = True,
) -> None:
    audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
    if audit_service is None:
        return

    auth_ctx = get_platform_auth_context(request)
    resolved_actor_id = actor_id or (getattr(auth_ctx, "account_id", None) if auth_ctx is not None else None)
    permission = _permission_context(request, scope=scope)
    event_metadata = dict(metadata or {})
    event_metadata.setdefault("route", request.url.path)
    event_metadata["permission_context"] = permission

    payloads: list[AuditPayloadInput] = []
    if request_payload is not None:
        payloads.append(AuditPayloadInput(kind="request", content_json=redact_sensitive(request_payload)))
    if response_payload is not None:
        payloads.append(AuditPayloadInput(kind="response", content_json=redact_sensitive(response_payload)))

    event = AuditEventInput(
        action=action,
        organization_id=organization_id,
        actor_type=actor_type,
        actor_id=resolved_actor_id,
        api_key=api_key,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request.headers.get("x-request-id"),
        correlation_id=request.headers.get("x-request-id"),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        status=status,
        latency_ms=int((perf_counter() - request_start) * 1000),
        error_type=error.__class__.__name__ if error is not None else None,
        error_code=getattr(getattr(error, "response", None), "status_code", None) if error is not None else None,
        metadata=event_metadata,
    )
    if critical:
        await audit_service.record_event_sync(event, payloads=payloads)
    else:
        audit_service.record_event(event, payloads=payloads, critical=False)
