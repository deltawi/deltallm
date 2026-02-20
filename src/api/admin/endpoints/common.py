from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import Header, HTTPException, Request, status


@dataclass
class AuthScope:
    is_platform_admin: bool = False
    org_ids: list[str] = field(default_factory=list)
    team_ids: list[str] = field(default_factory=list)


def get_auth_scope(
    request: Request,
    authorization: str | None = None,
    x_master_key: str | None = None,
    required_permission: str | None = None,
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

    if configured and provided == configured:
        return AuthScope(is_platform_admin=True)

    from src.middleware.platform_auth import get_platform_auth_context
    from src.auth.roles import has_platform_permission, Permission as Perm, ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS

    context = get_platform_auth_context(request)
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    if has_platform_permission(context.role, Perm.PLATFORM_ADMIN):
        return AuthScope(is_platform_admin=True)

    if required_permission:
        org_ids = [
            str(m.get("organization_id"))
            for m in context.organization_memberships
            if m.get("organization_id")
            and required_permission in ORG_ROLE_PERMISSIONS.get(str(m.get("role") or ""), set())
        ]
        team_ids = [
            str(m.get("team_id"))
            for m in context.team_memberships
            if m.get("team_id")
            and required_permission in TEAM_ROLE_PERMISSIONS.get(str(m.get("role") or ""), set())
        ]
    else:
        org_ids = [str(m.get("organization_id")) for m in context.organization_memberships if m.get("organization_id")]
        team_ids = [str(m.get("team_id")) for m in context.team_memberships if m.get("team_id")]

    return AuthScope(is_platform_admin=False, org_ids=org_ids, team_ids=team_ids)


def db_or_503(request: Request) -> Any:
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if db is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
    return db


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


def optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer") from exc


def model_entries(app: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    registry: dict[str, list[dict[str, Any]]] = getattr(app.state, "model_registry", {})
    for model_name, deployments in registry.items():
        for index, deployment in enumerate(deployments):
            deployment_id = str(deployment.get("deployment_id") or f"{model_name}-{index}")
            params = dict(deployment.get("litellm_params", {}))
            model_info = dict(deployment.get("model_info", {}))
            entries.append(
                {
                    "deployment_id": deployment_id,
                    "model_name": model_name,
                    "provider": str(params.get("model", "")).split("/")[0] or "unknown",
                    "litellm_params": params,
                    "model_info": model_info,
                }
            )
    return entries


def guardrail_type_from_class_path(class_path: str) -> str:
    lowered = class_path.lower()
    if "presidio" in lowered:
        return "PII Detection (Presidio)"
    if "lakera" in lowered:
        return "Prompt Injection (Lakera)"
    return "Custom Guardrail"


def serialize_guardrail(raw: Any) -> dict[str, Any]:
    item = raw.model_dump(mode="python") if hasattr(raw, "model_dump") else dict(raw)
    litellm_params = dict(item.get("litellm_params", {}))
    class_path = str(litellm_params.get("guardrail") or "")
    threshold = litellm_params.get("threshold")
    if threshold is None:
        threshold = litellm_params.get("score_threshold")
    if threshold is None:
        threshold = litellm_params.get("confidence_threshold")

    return {
        "guardrail_name": item.get("guardrail_name"),
        "type": guardrail_type_from_class_path(class_path),
        "mode": litellm_params.get("mode", "pre_call"),
        "enabled": bool(litellm_params.get("enabled", True)),
        "default_action": litellm_params.get("default_action", "block"),
        "threshold": float(threshold) if threshold is not None else 0.5,
        "litellm_params": to_json_value(litellm_params),
    }
