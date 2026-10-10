from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import logging
from time import perf_counter
from typing import Any

from fastapi import HTTPException, Request, status
from prisma.errors import RawQueryError

from src.api.admin.auth_scope import AuthScope, get_auth_scope as get_auth_scope
from src.api.audit import emit_control_audit_event
from src.audit.actions import AuditAction
from src.db.repositories import AuditRepository
from src.guardrails.catalog import (
    get_guardrail_preset_by_class_path,
    guardrail_threshold_from_params,
    guardrail_type_from_class_path,
    serialize_guardrail_editor_config,
)
from src.providers.resolution import resolve_provider
from src.services.named_credentials import redact_connection_config
from src.upstream_auth import (
    DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_FORMAT,
    DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_NAME,
    supports_custom_openai_compatible_auth,
)

logger = logging.getLogger(__name__)

_AUTH_SERVICE_UNAVAILABLE_HEADERS = {
    "Cache-Control": "no-store",
    "Retry-After": "5",
    "Vary": "Cookie",
}

_INLINE_SECRET_FIELDS = {
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
}

_CONNECTION_SUMMARY_FIELDS = (
    "api_base",
    "api_version",
    "region",
)

_MODEL_CREDENTIAL_AUDIENCE_CONSTRAINTS = (
    "creator model audience exceeds its named credential audience",
    "creator model has an invalid named credential binding",
)


@asynccontextmanager
async def managed_asset_membership_transaction(db: Any) -> AsyncIterator[Any]:
    """Map deferred model/credential audience failures to a useful API conflict."""

    try:
        async with db.tx() as tx:
            yield tx
    except RawQueryError as exc:
        metadata = exc.meta if isinstance(exc.meta, Mapping) else {}
        if metadata.get("code") == "23514" and any(
            message in str(exc) for message in _MODEL_CREDENTIAL_AUDIENCE_CONSTRAINTS
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This membership cannot be removed because a creator-owned model "
                    "still uses a named credential shared through it. Change the model "
                    "or credential access first."
                ),
            ) from exc
        raise


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


def db_or_503(request: Request) -> Any:
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        )
    return db


def telemetry_db_or_503(request: Request) -> object:
    """Resolve the dedicated telemetry client once at the HTTP boundary."""

    db = getattr(
        getattr(request.app.state, "telemetry_worker_prisma_manager", None), "client", None
    )
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telemetry database unavailable",
        )
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="user_id does not belong to team_id"
        )
    if organization_id and user_organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id does not belong to organization_id",
        )
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type and scope_id are required"
        )

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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
            )
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="API key must belong to a team"
            )
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

    details = " ".join(
        f"{key}={value}" for key, value in context.items() if value not in (None, "", [])
    )
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer"
        )
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer"
        ) from exc


def normalize_user_profile_type(value: Any, default: str = "internal_user") -> str:
    raw = str(value or default).strip().lower()
    normalized = USER_PROFILE_TYPE_ALIASES.get(raw, raw)
    if normalized not in ALLOWED_USER_PROFILE_TYPES:
        allowed = ", ".join(sorted(ALLOWED_USER_PROFILE_TYPES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"user_role must be one of: {allowed}"
        )
    return normalized


def model_entries(app: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    registry: dict[str, list[dict[str, Any]]] = getattr(app.state, "model_registry", {})
    for model_name, deployments in registry.items():
        for index, deployment in enumerate(deployments):
            deployment_id = str(deployment.get("deployment_id") or f"{model_name}-{index}")
            params = dict(deployment.get("deltallm_params", {}))
            model_info = dict(deployment.get("model_info", {}))
            named_credential_id = (
                str(deployment.get("named_credential_id")).strip()
                if deployment.get("named_credential_id") is not None
                else None
            )
            provider = resolve_provider(params)
            credential_source = (
                "named"
                if (
                    named_credential_id
                    or deployment.get("credential_binding_mode")
                    or deployment.get("credential_binding_state")
                )
                else "inline"
            )
            entries.append(
                {
                    "deployment_id": deployment_id,
                    "model_id": (
                        str(deployment.get("model_id")).strip()
                        if deployment.get("model_id") is not None
                        else None
                    ),
                    "model_name": model_name,
                    "routable": True,
                    "runtime_status": "active",
                    "provider": provider,
                    "mode": model_info.get("mode", "chat"),
                    "credential_source": credential_source,
                    "inline_credentials_present": _inline_credentials_present(params)
                    if credential_source == "inline"
                    else False,
                    "connection_summary": build_connection_summary(params, provider=provider),
                    "named_credential_id": named_credential_id or None,
                    "named_credential_name": (
                        str(deployment.get("named_credential_name")).strip()
                        if deployment.get("named_credential_name") is not None
                        else None
                    ),
                    "credential_binding_mode": deployment.get("credential_binding_mode"),
                    "credential_binding_state": deployment.get("credential_binding_state"),
                    "credential_bound_by_account_id": deployment.get(
                        "credential_bound_by_account_id"
                    ),
                    "deltallm_params": redact_connection_config(params),
                    "model_info": model_info,
                }
            )
    return entries


def _inline_credentials_present(params: dict[str, Any]) -> bool:
    return any(str(params.get(field) or "").strip() for field in _INLINE_SECRET_FIELDS)


def build_connection_summary(
    params: dict[str, Any], *, provider: str | None = None
) -> dict[str, Any]:
    summary = {field: params.get(field) or None for field in _CONNECTION_SUMMARY_FIELDS}
    summary_auth_header_name, custom_auth_label = _custom_auth_summary(params, provider=provider)
    if summary_auth_header_name:
        summary["auth_header_name"] = summary_auth_header_name
    if custom_auth_label:
        summary["custom_auth_label"] = custom_auth_label
    return summary


def _custom_auth_summary(
    params: dict[str, Any], *, provider: str | None = None
) -> tuple[str | None, str | None]:
    resolved_provider = provider or resolve_provider(params)
    if not supports_custom_openai_compatible_auth(resolved_provider):
        return None, None

    raw_header_name = str(params.get("auth_header_name") or "").strip()
    raw_header_format = str(params.get("auth_header_format") or "").strip()
    if not raw_header_name and not raw_header_format:
        return None, None

    effective_header_name = raw_header_name or DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_NAME
    effective_header_format = raw_header_format or DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_FORMAT
    uses_custom_auth = (
        effective_header_name != DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_NAME
        or effective_header_format != DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_FORMAT
    )
    if not uses_custom_auth:
        return None, None

    return effective_header_name, _custom_auth_label(effective_header_name, effective_header_format)


def _custom_auth_label(header_name: str, header_format: str) -> str:
    if header_name != DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_NAME:
        return header_name

    auth_scheme = _extract_auth_scheme(header_format)
    if auth_scheme and auth_scheme != "Bearer":
        return f"{header_name} ({auth_scheme})"
    return f"{header_name} (custom)"


def _extract_auth_scheme(header_format: str) -> str | None:
    if header_format.count("{api_key}") != 1:
        return None
    prefix, suffix = header_format.split("{api_key}", 1)
    if suffix.strip():
        return None
    scheme = prefix.strip()
    return scheme or None


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
    organization_id: str | None = None,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    status: str = "success",
    error: Exception | None = None,
    request_start: float | None = None,
    critical: bool = True,
    force_sync: bool = False,
    event_id: str | None = None,
    transactional_audit_repository: AuditRepository | None = None,
) -> None:
    audit_metadata = dict(metadata or {})
    if before is not None and after is not None:
        audit_metadata["changed_fields"] = changed_fields(before, after)
    await emit_control_audit_event(
        request=request,
        request_start=request_start if request_start is not None else perf_counter(),
        action=action,
        status=status,
        resource_type=resource_type,
        resource_id=resource_id,
        organization_id=organization_id,
        request_payload=request_payload,
        response_payload=response_payload,
        scope=scope,
        metadata=audit_metadata,
        error=error,
        critical=critical,
        force_sync=force_sync,
        event_id=event_id,
        transactional_repository=transactional_audit_repository,
    )
