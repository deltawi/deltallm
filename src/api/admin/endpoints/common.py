from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import logging
from time import perf_counter
from typing import Any

from fastapi import HTTPException, Request, status

from src.api.audit import emit_control_audit_event
from src.audit.actions import AuditAction
from src.guardrails.catalog import (
    get_guardrail_preset_by_class_path,
    guardrail_threshold_from_params,
    guardrail_type_from_class_path,
    serialize_guardrail_editor_config,
)
from src.providers.resolution import resolve_provider

logger = logging.getLogger(__name__)

@dataclass
class AuthScope:
    is_platform_admin: bool = False
    org_ids: list[str] = field(default_factory=list)
    team_ids: list[str] = field(default_factory=list)
    org_permissions_by_id: dict[str, set[str]] = field(default_factory=dict)
    team_permissions_by_id: dict[str, set[str]] = field(default_factory=dict)
    granted_permissions: set[str] = field(default_factory=set)
    effective_permissions: set[str] = field(default_factory=set)
    account_id: str | None = None


@dataclass(frozen=True)
class ResolvedScopeTarget:
    scope_type: str
    scope_id: str
    organization_id: str | None = None
    team_id: str | None = None


ALLOWED_USER_PROFILE_TYPES = {
    "internal_user",
    "internal_user_viewer",
    "team_admin",
}

USER_PROFILE_TYPE_ALIASES = {
    "user": "internal_user",
    "admin": "team_admin",
}


def get_auth_scope(
    request: Request,
    authorization: str | None = None,
    x_master_key: str | None = None,
    required_permission: str | None = None,
    any_permission: list[str] | None = None,
) -> AuthScope:
    configured = None
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is not None:
        configured = getattr(getattr(app_config, "general_settings", None), "master_key", None)
    if not configured:
        configured = getattr(getattr(request.app.state, "settings", None), "master_key", None)

    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1].strip()
    else:
        provided = x_master_key

    import hmac as _hmac
    if configured and provided and _hmac.compare_digest(provided, configured):
        return AuthScope(is_platform_admin=True)

    from src.middleware.platform_auth import get_platform_auth_context
    from src.middleware.platform_auth import requires_mfa_verification
    from src.auth.roles import has_platform_permission, Permission as Perm, ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS

    context = get_platform_auth_context(request)
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if requires_mfa_verification(context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA verification required")

    account_id = str(context.account_id) if context.account_id else None

    if has_platform_permission(context.role, Perm.PLATFORM_ADMIN):
        return AuthScope(is_platform_admin=True, account_id=account_id)

    org_permissions_by_id: dict[str, set[str]] = {}
    for membership in context.organization_memberships:
        role_perms = ORG_ROLE_PERMISSIONS.get(str(membership.get("role") or ""), set())
        organization_id = str(membership.get("organization_id") or "").strip()
        if not organization_id:
            continue
        org_permissions_by_id.setdefault(organization_id, set()).update(role_perms)

    team_permissions_by_id: dict[str, set[str]] = {}
    for membership in context.team_memberships:
        role_perms = TEAM_ROLE_PERMISSIONS.get(str(membership.get("role") or ""), set())
        team_id = str(membership.get("team_id") or "").strip()
        if not team_id:
            continue
        team_permissions_by_id.setdefault(team_id, set()).update(role_perms)

    effective_permissions: set[str] = set()
    for permissions in org_permissions_by_id.values():
        effective_permissions.update(permissions)
    for permissions in team_permissions_by_id.values():
        effective_permissions.update(permissions)

    permissions_to_check: list[str] = []
    if required_permission:
        permissions_to_check = [required_permission]
    elif any_permission:
        permissions_to_check = list(any_permission)

    if permissions_to_check:
        org_ids_set: set[str] = set()
        granted: set[str] = set()
        for organization_id, role_perms in org_permissions_by_id.items():
            matched = [p for p in permissions_to_check if p in role_perms]
            if matched:
                org_ids_set.add(organization_id)
                granted.update(matched)

        team_ids_set: set[str] = set()
        for team_id, role_perms in team_permissions_by_id.items():
            matched = [p for p in permissions_to_check if p in role_perms]
            if matched:
                team_ids_set.add(team_id)
                granted.update(matched)

        org_ids = list(org_ids_set)
        team_ids = list(team_ids_set)
    else:
        org_ids = list(org_permissions_by_id)
        team_ids = list(team_permissions_by_id)
        granted = set()

    return AuthScope(
        is_platform_admin=False,
        org_ids=org_ids,
        team_ids=team_ids,
        org_permissions_by_id=org_permissions_by_id,
        team_permissions_by_id=team_permissions_by_id,
        granted_permissions=granted,
        effective_permissions=effective_permissions,
        account_id=account_id,
    )


def db_or_503(request: Request) -> Any:
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if db is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
    return db


async def get_runtime_user_row(db: Any, user_id: str) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT
            u.user_id,
            u.team_id,
            t.organization_id
        FROM deltallm_usertable u
        LEFT JOIN deltallm_teamtable t ON t.team_id = u.team_id
        WHERE u.user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id not found")
    return dict(rows[0])


async def validate_runtime_user_scope(
    db: Any,
    user_id: str,
    *,
    team_id: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    row = await get_runtime_user_row(db, user_id)
    user_team_id = str(row.get("team_id") or "").strip() or None
    user_organization_id = str(row.get("organization_id") or "").strip() or None
    if team_id and user_team_id != team_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id does not belong to team_id")
    if organization_id and user_organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id does not belong to organization_id")
    return row


async def resolve_runtime_scope_target(
    db: Any,
    *,
    scope_type: str,
    scope_id: str,
) -> ResolvedScopeTarget:
    normalized_scope_type = str(scope_type or "").strip()
    normalized_scope_id = str(scope_id or "").strip()
    if not normalized_scope_type or not normalized_scope_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type and scope_id are required")

    if normalized_scope_type == "organization":
        rows = await db.query_raw(
            """
            SELECT organization_id
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            normalized_scope_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        return ResolvedScopeTarget(
            scope_type="organization",
            scope_id=normalized_scope_id,
            organization_id=normalized_scope_id,
        )

    if normalized_scope_type == "team":
        rows = await db.query_raw(
            """
            SELECT team_id, organization_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            normalized_scope_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        row = rows[0]
        return ResolvedScopeTarget(
            scope_type="team",
            scope_id=normalized_scope_id,
            organization_id=str(row.get("organization_id") or "").strip() or None,
            team_id=normalized_scope_id,
        )

    if normalized_scope_type == "api_key":
        rows = await db.query_raw(
            """
            SELECT vt.token, vt.team_id, t.organization_id
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
            WHERE vt.token = $1
            LIMIT 1
            """,
            normalized_scope_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
        row = rows[0]
        team_id = str(row.get("team_id") or "").strip() or None
        if team_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API key must belong to a team")
        return ResolvedScopeTarget(
            scope_type="api_key",
            scope_id=normalized_scope_id,
            organization_id=str(row.get("organization_id") or "").strip() or None,
            team_id=team_id,
        )

    if normalized_scope_type == "user":
        row = await validate_runtime_user_scope(db, normalized_scope_id)
        return ResolvedScopeTarget(
            scope_type="user",
            scope_id=normalized_scope_id,
            organization_id=str(row.get("organization_id") or "").strip() or None,
            team_id=str(row.get("team_id") or "").strip() or None,
        )

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported scope_type")


def to_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [to_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_json_value(v) for k, v in value.items()}
    return value


def log_admin_query_timing(name: str, started_at: float, **context: Any) -> None:
    elapsed_ms = int((perf_counter() - started_at) * 1000)
    if not logger.isEnabledFor(logging.DEBUG) and elapsed_ms < 500:
        return

    details = " ".join(f"{key}={value}" for key, value in context.items() if value not in (None, "", []))
    message = f"Admin query completed: name={name} latency_ms={elapsed_ms}"
    if details:
        message = f"{message} {details}"

    if elapsed_ms >= 500:
        logger.info(message)
    else:
        logger.debug(message)


def optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer") from exc


def normalize_user_profile_type(value: Any, default: str = "internal_user") -> str:
    raw = str(value or default).strip().lower()
    normalized = USER_PROFILE_TYPE_ALIASES.get(raw, raw)
    if normalized not in ALLOWED_USER_PROFILE_TYPES:
        allowed = ", ".join(sorted(ALLOWED_USER_PROFILE_TYPES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"user_role must be one of: {allowed}")
    return normalized


def model_entries(app: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    registry: dict[str, list[dict[str, Any]]] = getattr(app.state, "model_registry", {})
    for model_name, deployments in registry.items():
        for index, deployment in enumerate(deployments):
            deployment_id = str(deployment.get("deployment_id") or f"{model_name}-{index}")
            params = dict(deployment.get("deltallm_params", {}))
            model_info = dict(deployment.get("model_info", {}))
            entries.append(
                {
                    "deployment_id": deployment_id,
                    "model_name": model_name,
                    "provider": resolve_provider(params),
                    "mode": model_info.get("mode", "chat"),
                    "deltallm_params": params,
                    "model_info": model_info,
                }
            )
    return entries

def serialize_guardrail(raw: Any) -> dict[str, Any]:
    item = raw.model_dump(mode="python") if hasattr(raw, "model_dump") else dict(raw)
    deltallm_params = dict(item.get("deltallm_params", {}))
    class_path = str(deltallm_params.get("guardrail") or "")
    preset = get_guardrail_preset_by_class_path(class_path)

    return {
        "guardrail_name": item.get("guardrail_name"),
        "type": guardrail_type_from_class_path(class_path),
        "preset_id": preset["preset_id"] if preset is not None else None,
        "is_custom": preset is None,
        "class_path": class_path or None,
        "mode": deltallm_params.get("mode", "pre_call"),
        "enabled": bool(deltallm_params.get("enabled", True)),
        "default_action": deltallm_params.get("default_action", "block"),
        "threshold": guardrail_threshold_from_params(deltallm_params),
        "editor": serialize_guardrail_editor_config(deltallm_params),
        "deltallm_params": to_json_value(deltallm_params),
    }


def changed_fields(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    if before is None or after is None:
        return []
    keys = set(before.keys()) | set(after.keys())
    return sorted([key for key in keys if before.get(key) != after.get(key)])


async def emit_admin_mutation_audit(
    *,
    request: Request,
    action: str | AuditAction,
    scope: AuthScope | None = None,
    resource_type: str,
    resource_id: str | None = None,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    status: str = "success",
    error: Exception | None = None,
    request_start: float | None = None,
) -> None:
    metadata: dict[str, Any] = {}
    if before is not None and after is not None:
        metadata["changed_fields"] = changed_fields(before, after)
    await emit_control_audit_event(
        request=request,
        request_start=request_start if request_start is not None else perf_counter(),
        action=action,
        status=status,
        resource_type=resource_type,
        resource_id=resource_id,
        request_payload=request_payload,
        response_payload=response_payload,
        scope=scope,
        metadata=metadata,
        error=error,
        critical=True,
    )
