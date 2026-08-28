from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import logging
import secrets
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.auth.roles import OrganizationRole, Permission, validate_organization_role
from src.audit.actions import AuditAction
from src.services.asset_binding_mirror import (
    callable_catalog,
    callable_target_access_group_binding_repository,
    callable_target_binding_repository,
    list_all_callable_target_bindings,
    list_all_route_group_bindings,
    reload_callable_target_grants,
    route_group_repository,
)
from src.api.admin.endpoints.common import (
    db_or_503,
    emit_admin_mutation_audit,
    get_auth_scope,
    optional_int,
    to_json_value,
    validate_runtime_user_scope,
)
from src.api.admin.endpoints.organization_schemas import (
    OrganizationListResponse,
    OrganizationResponse,
)
from src.api.admin.organization_mutations import require_active_organization_mutation
from src.db.callable_target_access_groups import CallableTargetAccessGroupBindingRepository
from src.db.callable_targets import CallableTargetBindingRepository
from src.db.organization_admin import (
    OrganizationAdminRepository,
    OrganizationPersistenceValues,
)
from src.db.route_groups import RouteGroupRepository
from src.db.repositories import AUDIT_METADATA_RETENTION_DAYS_KEY, AUDIT_PAYLOAD_RETENTION_DAYS_KEY
from src.middleware.admin import require_admin_permission
from src.services.asset_visibility_preview import (
    build_asset_visibility_preview,
    list_scope_route_group_bindings,
)
from src.services.organization_callable_target_sync import (
    get_organization_auto_follow_catalog,
    organization_auto_follow_catalog,
    set_organization_auto_follow_catalog,
    with_organization_auto_follow_catalog,
)
from src.services.model_visibility import get_tier_policy_mode_from_app
from src.services.scoped_asset_access import build_scope_asset_access, sync_scope_asset_access_state
from src.services.tier_admin_errors import (
    TierAdminConflictError,
    TierAdminError,
    TierAdminNotFoundError,
    TierAdminUnavailableError,
)
from src.services.tier_assignment_admin import (
    _assignment_admin_error,
    _assignment_storage_error,
)
from src.services.tier_assignment_admin_payloads import normalize_assignment_create
from src.services.tier_assignment_admin_serialization import serialize_tier_assignment
from src.services.tier_assignment_cache_invalidation import (
    apply_best_effort_org_cache_invalidation,
    enqueue_org_tier_assignment_cache_invalidation,
)
from src.services.tier_policy_invalidation import reload_tier_policy
from src.services.ui_authorization import build_organization_capabilities

router = APIRouter(tags=["Admin Organizations"])
logger = logging.getLogger(__name__)

_BUDGET_RESET_METADATA_KEY = "_budget_reset"
_MONTHLY_ANCHOR_DAY_KEY = "monthly_anchor_day"
_MAX_BUDGET_DURATION_AMOUNT = 10_000


def _organization_response_payload(
    organization: dict[str, Any],
    *,
    capabilities: dict[str, bool] | None = None,
    service_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = to_json_value(dict(organization))
    if isinstance(payload, dict):
        payload["budget_reset_at"] = _serialize_budget_reset_at(organization.get("budget_reset_at"))
        payload["service_policy"] = service_policy or _organization_service_policy_payload(
            organization,
            assignment_rows=[],
            tier_policy_mode="disabled",
        )
        if capabilities is not None:
            payload["capabilities"] = capabilities
    return payload


_ORGANIZATION_HARD_CAP_FIELDS = (
    "rpm_limit",
    "tpm_limit",
    "rph_limit",
    "rpd_limit",
    "tpd_limit",
)


def _organization_service_policy_payload(
    organization: dict[str, Any],
    *,
    assignment_rows: list[dict[str, Any]],
    tier_policy_mode: str,
) -> dict[str, Any]:
    primary_row = next(
        (row for row in assignment_rows if str(row.get("assignment_type") or "") == "primary"),
        None,
    )
    primary_tier = None
    if primary_row is not None:
        primary_tier = {
            "assignment_id": primary_row.get("assignment_id"),
            "tier_id": primary_row.get("tier_id"),
            "tier_key": primary_row.get("tier_key"),
            "tier_name": primary_row.get("tier_name"),
            "tier_version_id": primary_row.get("effective_tier_version_id"),
            "tier_version_number": primary_row.get("tier_version_number"),
            "follows_active_version": primary_row.get("tier_version_id") is None,
        }

    hard_caps = {
        field: organization.get(field)
        for field in _ORGANIZATION_HARD_CAP_FIELDS
        if organization.get(field) is not None
    }
    overlays = [
        row
        for row in assignment_rows
        if str(row.get("assignment_type") or "") in {"addon", "override"}
    ]
    tier_configured = bool(assignment_rows)
    tier_authoritative = tier_configured and tier_policy_mode == "enforce"
    return {
        # ``source`` describes the configured organization policy. During a
        # disabled/shadow rollout, legacy Asset Access is still authoritative
        # at runtime even when a tier assignment is already staged.
        "source": "tier" if tier_configured else "legacy",
        "runtime_source": "tier" if tier_authoritative else "legacy",
        "tier_authoritative": tier_authoritative,
        "tier_policy_mode": tier_policy_mode,
        "primary_tier": primary_tier,
        "active_assignment_count": len(assignment_rows),
        "overlay_count": len(overlays),
        "hard_caps_configured": bool(hard_caps),
        "organization_hard_caps": hard_caps,
        "legacy_model_limits_configured": bool(
            organization.get("model_rpm_limit") or organization.get("model_tpm_limit")
        ),
    }


async def _shadow_tier_callable_target_bindings(
    repository: Any,
    *,
    tier_id: str,
    tier_version_id: str | None,
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    """Mirror a primary tier's allowlist for shadow-mode runtime compatibility.

    Tier policy is observational in shadow mode, so the legacy callable-target
    policy remains authoritative. New tier-first organizations need an exact
    legacy mirror or they would start with no callable access until enforcement.
    """
    effective_version_id = tier_version_id
    if effective_version_id is None:
        get_active_version = getattr(repository, "get_active_tier_version", None)
        if not callable(get_active_version):
            raise TierAdminUnavailableError(
                "Tier repository cannot resolve the active version for shadow access"
            )
        active_version = await get_active_version(tier_id)
        if active_version is None:
            raise TierAdminConflictError("enabled tier assignments require an active tier version")
        effective_version_id = str(active_version.tier_version_id or "").strip()

    list_model_policies = getattr(repository, "list_model_policies", None)
    if not callable(list_model_policies):
        raise TierAdminUnavailableError(
            "Tier repository cannot load model policies for shadow access"
        )
    policies = await list_model_policies(effective_version_id)
    allowed_keys = sorted(
        {
            str(policy.callable_key or "").strip()
            for policy in policies
            if bool(getattr(policy, "enabled", True))
            and str(getattr(policy, "access_mode", "allow") or "").strip().lower() == "allow"
            and str(getattr(policy, "callable_key", "") or "").strip()
        }
    )
    missing_keys = [callable_key for callable_key in allowed_keys if callable_key not in catalog]
    if missing_keys:
        sample = ", ".join(missing_keys[:5])
        suffix = "" if len(missing_keys) <= 5 else f" (+{len(missing_keys) - 5} more)"
        raise TierAdminConflictError(
            "Tier allowlist contains callable targets that are not currently configured: "
            f"{sample}{suffix}"
        )

    return [
        {
            "callable_key": callable_key,
            "enabled": True,
            "metadata": {
                "source": "tier_shadow_mirror",
                "tier_id": tier_id,
                "tier_version_id": effective_version_id,
            },
        }
        for callable_key in allowed_keys
    ]


async def _load_organization_service_policies(
    db: Any,
    organizations: list[dict[str, Any]],
    *,
    tier_policy_mode: str,
) -> dict[str, dict[str, Any]]:
    organization_ids = [
        str(organization.get("organization_id") or "").strip()
        for organization in organizations
        if str(organization.get("organization_id") or "").strip()
    ]
    rows: list[dict[str, Any]] = []
    if organization_ids:
        placeholders = ", ".join(f"${index}" for index in range(1, len(organization_ids) + 1))
        raw_rows = await db.query_raw(
            f"""
            SELECT
                a.assignment_id,
                a.organization_id,
                a.tier_id,
                a.tier_version_id,
                a.assignment_type,
                a.weight,
                t.tier_key,
                t.name AS tier_name,
                resolved_version.tier_version_id AS effective_tier_version_id,
                resolved_version.version_number AS tier_version_number
            FROM deltallm_organizationtierassignment a
            JOIN deltallm_tier t ON t.tier_id = a.tier_id
            JOIN LATERAL (
                SELECT v.tier_version_id, v.version_number
                FROM deltallm_tierversion v
                WHERE v.tier_id = a.tier_id
                  AND v.status = 'active'
                  AND (a.tier_version_id IS NULL OR v.tier_version_id = a.tier_version_id)
                ORDER BY v.version_number DESC, v.tier_version_id ASC
                LIMIT 1
            ) AS resolved_version ON TRUE
            WHERE a.organization_id IN ({placeholders})
              AND a.enabled = TRUE
              AND t.enabled = TRUE
              AND (a.starts_at IS NULL OR a.starts_at <= NOW())
              AND (a.ends_at IS NULL OR a.ends_at > NOW())
            ORDER BY
                a.organization_id ASC,
                CASE a.assignment_type
                    WHEN 'primary' THEN 1
                    WHEN 'override' THEN 2
                    WHEN 'addon' THEN 3
                    ELSE 4
                END ASC,
                a.weight DESC,
                a.created_at ASC,
                a.assignment_id ASC
            """,
            *organization_ids,
        )
        rows = [dict(row) for row in raw_rows]

    assignments_by_org: dict[str, list[dict[str, Any]]] = {
        organization_id: [] for organization_id in organization_ids
    }
    for row in rows:
        organization_id = str(row.get("organization_id") or "")
        if organization_id in assignments_by_org:
            assignments_by_org[organization_id].append(row)

    return {
        organization_id: _organization_service_policy_payload(
            organization,
            assignment_rows=assignments_by_org.get(organization_id, []),
            tier_policy_mode=tier_policy_mode,
        )
        for organization in organizations
        if (organization_id := str(organization.get("organization_id") or ""))
    }


def _normalize_primary_tier_payload(
    payload: dict[str, Any],
    *,
    required: bool,
) -> dict[str, Any] | None:
    raw_primary_tier = payload.get("primary_tier")
    if raw_primary_tier is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="primary_tier is required while tier policy mode is enforce",
            )
        return None
    if not isinstance(raw_primary_tier, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="primary_tier must be an object",
        )
    unexpected_fields = set(raw_primary_tier) - {"tier_id", "tier_version_id"}
    if unexpected_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="primary_tier only accepts tier_id and tier_version_id",
        )
    try:
        return normalize_assignment_create(
            {
                "tier_id": raw_primary_tier.get("tier_id"),
                "tier_version_id": raw_primary_tier.get("tier_version_id"),
                "assignment_type": "primary",
                "enabled": True,
                "weight": 1,
            }
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


_TIER_ASSIGNMENT_UPDATE_FIELDS = frozenset(
    {
        "legacy_policy_exception",
        "primary_tier",
        "service_policy",
        "tier_assignment",
        "tier_assignments",
        "tier_id",
        "tier_version_id",
    }
)
_PLATFORM_MANAGED_MODEL_POLICY_FIELDS = frozenset(
    {
        "model_rpm_limit",
        "model_tpm_limit",
    }
)


def _reject_organization_tier_policy_update_fields(
    payload: dict[str, Any],
    *,
    is_platform_admin: bool,
) -> None:
    tier_assignment_fields = sorted(_TIER_ASSIGNMENT_UPDATE_FIELDS.intersection(payload))
    if tier_assignment_fields:
        if not is_platform_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only platform admins can manage organization tier assignments",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Tier assignments cannot be changed through organization settings; "
                "use the organization tier-assignment endpoints"
            ),
        )

    model_policy_fields = sorted(_PLATFORM_MANAGED_MODEL_POLICY_FIELDS.intersection(payload))
    if model_policy_fields and not is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform admins can update model-specific organization policy",
        )


def _tier_assignment_http_error(exc: TierAdminError) -> HTTPException:
    if isinstance(exc, TierAdminNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, TierAdminConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(exc, TierAdminUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=status_code, detail=exc.detail)


def _optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a boolean"
    )


def _optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a number"
        )
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a number"
        ) from exc
    if parsed < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be >= 0"
        )
    return parsed


def _optional_budget_duration(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a string"
        )
    normalized = value.strip()
    if not normalized:
        return None
    if _parse_budget_duration(normalized) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a positive integer up to {_MAX_BUDGET_DURATION_AMOUNT} followed by h, d, or mo",
        )
    return normalized


def _parse_budget_duration(value: str) -> tuple[int, str] | None:
    if value.endswith("mo"):
        amount_raw = value[:-2]
        unit = "mo"
    else:
        amount_raw = value[:-1]
        unit = value[-1:]
    if not amount_raw.isdigit():
        return None
    amount = int(amount_raw)
    if amount <= 0 or amount > _MAX_BUDGET_DURATION_AMOUNT or unit not in {"h", "d", "mo"}:
        return None
    return amount, unit


def _budget_duration_unit(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = _parse_budget_duration(value)
    return parsed[1] if parsed is not None else None


def _optional_datetime(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc_datetime(value)
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be an ISO 8601 datetime",
        )
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be an ISO 8601 datetime",
        ) from exc
    return _as_utc_datetime(parsed)


def _resolve_budget_reset_fields(
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
) -> tuple[str | None, datetime | None]:
    existing = existing or {}
    reset_fields_provided = "budget_duration" in payload or "budget_reset_at" in payload
    if not reset_fields_provided and existing:
        duration = _existing_budget_duration(existing.get("budget_duration"))
        reset_at = _coerce_budget_reset_datetime(existing.get("budget_reset_at"))
        return duration, reset_at

    duration_raw = (
        payload["budget_duration"]
        if "budget_duration" in payload
        else existing.get("budget_duration")
    )
    reset_at_raw = (
        payload["budget_reset_at"]
        if "budget_reset_at" in payload
        else existing.get("budget_reset_at")
    )
    duration = _optional_budget_duration(duration_raw, "budget_duration")
    reset_at = _optional_datetime(reset_at_raw, "budget_reset_at")

    if duration is None and reset_at is None:
        return None, None
    if duration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="budget_duration is required when budget_reset_at is set",
        )
    if reset_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="budget_reset_at is required when budget_duration is set",
        )
    return duration, reset_at


def _existing_budget_duration(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _budget_reset_storage_value(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _as_utc_datetime(value).replace(tzinfo=None)


def _serialize_budget_reset_at(value: Any) -> str | None:
    parsed = _coerce_budget_reset_datetime(value)
    if parsed is None:
        return None
    return _as_utc_datetime(parsed).isoformat().replace("+00:00", "Z")


def _coerce_budget_reset_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc_datetime(value)
    if isinstance(value, str):
        try:
            return _as_utc_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _apply_budget_reset_metadata(
    metadata: dict[str, Any] | None,
    *,
    duration: str | None,
    reset_at: datetime | None,
    reset_fields_provided: bool,
) -> dict[str, Any] | None:
    if not reset_fields_provided:
        return metadata

    next_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    if _budget_duration_unit(duration) == "mo" and reset_at is not None:
        raw_budget_reset_settings = next_metadata.get(_BUDGET_RESET_METADATA_KEY)
        budget_reset_settings = (
            dict(raw_budget_reset_settings) if isinstance(raw_budget_reset_settings, dict) else {}
        )
        budget_reset_settings[_MONTHLY_ANCHOR_DAY_KEY] = _as_utc_datetime(reset_at).day
        next_metadata[_BUDGET_RESET_METADATA_KEY] = budget_reset_settings
    else:
        next_metadata.pop(_BUDGET_RESET_METADATA_KEY, None)
    return next_metadata or None


def _validate_model_limit_dict(value: Any, field_name: str) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be an object mapping model names to integer limits",
        )
    result: dict[str, int] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not k.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} keys must be non-empty strings",
            )
        try:
            int_val = int(v)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} values must be integers",
            )
        if int_val < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} values must be non-negative",
            )
        result[k.strip()] = int_val
    return result if result else None


def _audit_retention_metadata(
    payload: dict[str, Any], existing: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    raw_metadata = payload.get("metadata")
    if isinstance(raw_metadata, dict):
        metadata.update(raw_metadata)
    if isinstance(existing, dict):
        metadata = {**existing, **metadata}

    metadata_changed = False
    for field_name in (AUDIT_METADATA_RETENTION_DAYS_KEY, AUDIT_PAYLOAD_RETENTION_DAYS_KEY):
        if field_name not in payload:
            continue
        value = optional_int(payload.get(field_name), field_name)
        if value is None:
            metadata.pop(field_name, None)
            metadata_changed = True
            continue
        if value < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be >= 1"
            )
        metadata[field_name] = value
        metadata_changed = True

    if not metadata and not metadata_changed:
        return None
    return metadata


def _route_group_repository_for_request(
    request: Request,
    *,
    db_client: Any | None = None,
) -> RouteGroupRepository | Any | None:
    repository = route_group_repository(request)
    if repository is None or db_client is None:
        return repository
    if isinstance(repository, RouteGroupRepository):
        return RouteGroupRepository(db_client)
    return repository


def _callable_target_binding_repository_for_request(
    request: Request,
    *,
    db_client: Any | None = None,
) -> CallableTargetBindingRepository | Any | None:
    repository = callable_target_binding_repository(request)
    if repository is None or db_client is None:
        return repository
    if isinstance(repository, CallableTargetBindingRepository):
        return CallableTargetBindingRepository(db_client)
    return repository


def _callable_target_access_group_repository_for_request(
    request: Request,
    *,
    db_client: Any | None = None,
) -> CallableTargetAccessGroupBindingRepository | Any | None:
    repository = callable_target_access_group_binding_repository(request)
    if repository is None or db_client is None:
        return repository
    if isinstance(repository, CallableTargetAccessGroupBindingRepository):
        return CallableTargetAccessGroupBindingRepository(db_client)
    return repository


async def _validate_org_route_group_binding_payloads(
    repository,  # noqa: ANN001
    *,
    binding_payloads: list[dict[str, Any]],
) -> None:
    if not binding_payloads:
        return
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Route group repository unavailable",
        )

    for item in binding_payloads:
        if await repository.get_group(item["group_key"]) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"route_group_bindings.group_key does not exist: {item['group_key']}",
            )


def _validate_org_callable_target_binding_payloads(
    *,
    binding_payloads: list[dict[str, Any]],
    catalog: dict[str, Any],
) -> None:
    for item in binding_payloads:
        if item["callable_key"] not in catalog:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"callable_target_bindings.callable_key does not exist: {item['callable_key']}",
            )


def _normalize_route_group_binding_payloads(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="route_group_bindings must be an array"
        )

    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="route_group_bindings entries must be objects",
            )
        group_key = str(item.get("group_key") or "").strip()
        if not group_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="route_group_bindings.group_key is required",
            )
        metadata = item.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="route_group_bindings.metadata must be an object",
            )
        normalized.append(
            {
                "group_key": group_key,
                "enabled": bool(item.get("enabled", True)),
                "metadata": metadata,
            }
        )

    deduped: dict[str, dict[str, Any]] = {}
    for item in normalized:
        deduped[item["group_key"]] = item
    return list(deduped.values())


def _normalize_callable_target_binding_payloads(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="callable_target_bindings must be an array",
        )

    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="callable_target_bindings entries must be objects",
            )
        callable_key = str(item.get("callable_key") or "").strip()
        if not callable_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="callable_target_bindings.callable_key is required",
            )
        metadata = item.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="callable_target_bindings.metadata must be an object",
            )
        normalized.append(
            {
                "callable_key": callable_key,
                "enabled": bool(item.get("enabled", True)),
                "metadata": metadata,
            }
        )

    deduped: dict[str, dict[str, Any]] = {}
    for item in normalized:
        deduped[item["callable_key"]] = item
    return list(deduped.values())


def _validate_route_group_callable_target_overlap(
    route_group_bindings: list[dict[str, Any]],
    callable_target_bindings: list[dict[str, Any]],
) -> None:
    callable_by_key = {item["callable_key"]: item for item in callable_target_bindings}
    for binding in route_group_bindings:
        callable_binding = callable_by_key.get(binding["group_key"])
        if callable_binding is None:
            continue
        if bool(callable_binding.get("enabled", True)) != bool(binding.get("enabled", True)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"route_group_bindings and callable_target_bindings disagree for: {binding['group_key']}",
            )
        if (callable_binding.get("metadata") or None) != (binding.get("metadata") or None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"route_group_bindings and callable_target_bindings disagree for: {binding['group_key']}",
            )


async def _sync_org_route_group_bindings(
    request: Request,
    *,
    organization_id: str,
    binding_payloads: list[dict[str, Any]],
    route_repo=None,  # noqa: ANN001
    callable_binding_repo=None,  # noqa: ANN001
) -> list[dict[str, Any]]:
    repository = route_repo or route_group_repository(request)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Route group repository unavailable",
        )
    callable_repository = callable_binding_repo or callable_target_binding_repository(request)

    await _validate_org_route_group_binding_payloads(repository, binding_payloads=binding_payloads)
    desired_by_group = {item["group_key"]: item for item in binding_payloads}

    current_bindings = await list_all_route_group_bindings(
        repository,
        scope_type="organization",
        scope_id=organization_id,
    )
    current_by_group = {binding.group_key: binding for binding in current_bindings}

    for group_key, binding in current_by_group.items():
        if group_key in desired_by_group:
            continue
        await repository.delete_binding(binding.route_group_binding_id)
        if callable_repository is not None:
            callable_bindings = await list_all_callable_target_bindings(
                callable_repository,
                callable_key=group_key,
                scope_type="organization",
                scope_id=organization_id,
            )
            for callable_binding in callable_bindings:
                await callable_repository.delete_binding(
                    callable_binding.callable_target_binding_id
                )

    for group_key, item in desired_by_group.items():
        await repository.upsert_binding(
            group_key,
            scope_type="organization",
            scope_id=organization_id,
            enabled=item["enabled"],
            metadata=item["metadata"],
        )
        if callable_repository is not None:
            await callable_repository.upsert_binding(
                callable_key=group_key,
                scope_type="organization",
                scope_id=organization_id,
                enabled=item["enabled"],
                metadata=item["metadata"],
            )

    bindings = await list_all_route_group_bindings(
        repository,
        scope_type="organization",
        scope_id=organization_id,
    )
    return [to_json_value(asdict(binding)) for binding in bindings]


async def _list_org_route_group_bindings(
    request: Request, organization_id: str
) -> list[dict[str, Any]]:
    return await list_scope_route_group_bindings(
        request,
        scope_type="organization",
        scope_id=organization_id,
    )


async def _sync_org_callable_target_bindings(
    request: Request,
    *,
    organization_id: str,
    binding_payloads: list[dict[str, Any]],
    protected_callable_keys: set[str] | None = None,
    callable_binding_repo=None,  # noqa: ANN001
    route_repo=None,  # noqa: ANN001
    catalog: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    repository = callable_binding_repo or callable_target_binding_repository(request)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Callable target binding repository unavailable",
        )

    current_catalog = catalog or callable_catalog(request)
    _validate_org_callable_target_binding_payloads(
        binding_payloads=binding_payloads,
        catalog=current_catalog,
    )
    desired_by_key = {item["callable_key"]: item for item in binding_payloads}

    current_bindings = await list_all_callable_target_bindings(
        repository,
        scope_type="organization",
        scope_id=organization_id,
    )
    current_by_key = {binding.callable_key: binding for binding in current_bindings}
    protected_keys = protected_callable_keys or set()
    route_repository = route_repo or route_group_repository(request)

    for callable_key, binding in current_by_key.items():
        if callable_key in desired_by_key or callable_key in protected_keys:
            continue
        await repository.delete_binding(binding.callable_target_binding_id)
        if route_repository is not None:
            route_group_bindings = await list_all_route_group_bindings(
                route_repository,
                group_key=callable_key,
                scope_type="organization",
                scope_id=organization_id,
            )
            for route_group_binding in route_group_bindings:
                await route_repository.delete_binding(route_group_binding.route_group_binding_id)

    for callable_key, item in desired_by_key.items():
        await repository.upsert_binding(
            callable_key=callable_key,
            scope_type="organization",
            scope_id=organization_id,
            enabled=item["enabled"],
            metadata=item["metadata"],
        )
        if (
            route_repository is not None
            and await route_repository.get_group(callable_key) is not None
        ):
            await route_repository.upsert_binding(
                callable_key,
                scope_type="organization",
                scope_id=organization_id,
                enabled=item["enabled"],
                metadata=item["metadata"],
            )

    bindings = await list_all_callable_target_bindings(
        repository,
        scope_type="organization",
        scope_id=organization_id,
    )
    return [to_json_value(asdict(binding)) for binding in bindings]


async def _list_org_callable_target_bindings(
    request: Request, organization_id: str
) -> list[dict[str, Any]]:
    repository = callable_target_binding_repository(request)
    if repository is None:
        return []
    bindings = await list_all_callable_target_bindings(
        repository,
        scope_type="organization",
        scope_id=organization_id,
    )
    return [to_json_value(asdict(binding)) for binding in bindings]


async def _build_org_asset_visibility_preview(
    request: Request, organization_id: str
) -> dict[str, Any]:
    return await build_asset_visibility_preview(request, organization_id=organization_id)


@router.get("/ui/api/organizations", response_model=OrganizationListResponse)
async def list_organizations(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.ORG_READ
    )
    db = db_or_503(request)

    clauses: list[str] = []
    params: list[Any] = []

    if not scope.is_platform_admin:
        if scope.org_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            clauses.append(f"o.organization_id IN ({ph})")
        else:
            return {
                "data": [],
                "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False},
            }

    if search:
        params.append(f"%{search}%")
        clauses.append(
            f"(o.organization_name ILIKE ${len(params)} OR o.organization_id ILIKE ${len(params)})"
        )

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    select_cols = """o.organization_id, o.organization_name, o.max_budget, o.soft_budget, o.spend, o.budget_duration, o.budget_reset_at, o.rpm_limit, o.tpm_limit,
                   o.rph_limit, o.rpd_limit, o.tpd_limit,
                   o.model_rpm_limit, o.model_tpm_limit,
                   o.audit_content_storage_enabled, o.metadata,
                   o.lifecycle_state, o.deletion_requested_at, o.deletion_not_before_at,
                   o.created_at, o.updated_at,
                   (SELECT COUNT(*) FROM deltallm_teamtable t WHERE t.organization_id = o.organization_id) AS team_count"""

    count_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM deltallm_organizationtable o {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    params.append(limit)
    params.append(offset)
    rows = await db.query_raw(
        f"""
        SELECT {select_cols}
        FROM deltallm_organizationtable o
        {where_sql}
        ORDER BY o.created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )

    organizations = [dict(row) for row in rows]
    service_policies = await _load_organization_service_policies(
        db,
        organizations,
        tier_policy_mode=get_tier_policy_mode_from_app(request.app),
    )

    return {
        "data": [
            _organization_response_payload(
                organization,
                capabilities=build_organization_capabilities(scope, organization),
                service_policy=service_policies.get(str(organization.get("organization_id") or "")),
            )
            for organization in organizations
        ],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.get(
    "/ui/api/organizations/{organization_id}",
    dependencies=[Depends(require_admin_permission(Permission.ORG_READ))],
    response_model=OrganizationResponse,
)
async def get_organization(
    request: Request,
    organization_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.ORG_READ
    )
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, soft_budget, spend, budget_duration, budget_reset_at, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, model_rpm_limit, model_tpm_limit, audit_content_storage_enabled, metadata, lifecycle_state, deletion_requested_at, deletion_not_before_at, created_at, updated_at
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    organization = dict(rows[0])
    service_policies = await _load_organization_service_policies(
        db,
        [organization],
        tier_policy_mode=get_tier_policy_mode_from_app(request.app),
    )
    payload = _organization_response_payload(
        organization,
        capabilities=build_organization_capabilities(scope, organization),
        service_policy=service_policies.get(organization_id),
    )
    if isinstance(payload, dict):
        payload["route_group_bindings"] = await _list_org_route_group_bindings(
            request, organization_id
        )
        payload["callable_target_bindings"] = await _list_org_callable_target_bindings(
            request, organization_id
        )
    return payload


@router.get(
    "/ui/api/organizations/{organization_id}/asset-visibility",
    dependencies=[Depends(require_admin_permission(Permission.ORG_READ))],
)
async def get_organization_asset_visibility(
    request: Request,
    organization_id: str,
    user_id: str | None = Query(default=None),
    include_access_groups: bool = Query(default=False),
    access_group_search: str | None = Query(default=None),
    access_group_limit: int = Query(default=50, ge=1, le=200),
    access_group_offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    user_row = (
        await validate_runtime_user_scope(db, user_id, organization_id=organization_id)
        if user_id is not None and str(user_id).strip()
        else None
    )
    return await build_asset_visibility_preview(
        request,
        organization_id=organization_id,
        user_id=str(user_row.get("user_id") or "").strip() or None if user_row else None,
        include_access_groups=include_access_groups,
        access_group_search=access_group_search,
        access_group_limit=access_group_limit,
        access_group_offset=access_group_offset,
    )


@router.get(
    "/ui/api/organizations/{organization_id}/asset-access",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def get_organization_asset_access(
    request: Request,
    organization_id: str,
    include_targets: bool = Query(default=True),
    access_group_search: str | None = Query(default=None),
    access_group_limit: int = Query(default=50, ge=1, le=200),
    access_group_offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    response = await build_scope_asset_access(
        request,
        scope_type="organization",
        scope_id=organization_id,
        organization_id=organization_id,
        include_targets=include_targets,
        access_group_search=access_group_search,
        access_group_limit=access_group_limit,
        access_group_offset=access_group_offset,
    )
    response["auto_follow_catalog"] = await get_organization_auto_follow_catalog(
        db, organization_id
    )
    return response


@router.put(
    "/ui/api/organizations/{organization_id}/asset-access",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def update_organization_asset_access(
    request: Request,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    tier_policy_mode = get_tier_policy_mode_from_app(request.app)
    if tier_policy_mode == "enforce":
        service_policies = await _load_organization_service_policies(
            db,
            [dict(rows[0])],
            tier_policy_mode=tier_policy_mode,
        )
        if bool(service_policies.get(organization_id, {}).get("tier_authoritative")):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Organization Asset Access cannot be changed while an active tier policy "
                    "is authoritative; update the tier or use team, key, or user restrictions"
                ),
            )
    auto_follow_catalog = bool(payload.get("select_all_selectable", False))

    async def _apply_asset_access(
        db_client: Any,
        *,
        callable_repository,
        access_group_repository,
        route_repository,
    ) -> None:  # noqa: ANN001, ANN202
        await require_active_organization_mutation(db_client, organization_id)
        asset_access_payload = {
            "scope_type": "organization",
            "scope_id": organization_id,
            "organization_id": organization_id,
            "mode": payload.get("mode"),
            "selected_callable_keys": payload.get("selected_callable_keys", []),
            "select_all_selectable": auto_follow_catalog,
            "binding_repository": callable_repository,
            "access_group_repository": access_group_repository,
            "route_group_repository": route_repository,
            "reload_after_write": False,
        }
        if "selected_access_group_keys" in payload:
            asset_access_payload["selected_access_group_keys"] = payload[
                "selected_access_group_keys"
            ]
        await sync_scope_asset_access_state(request, **asset_access_payload)
        await set_organization_auto_follow_catalog(
            db_client,
            organization_id,
            enabled=auto_follow_catalog,
        )

    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Organization asset-access mutation requires transaction support",
        )
    async with db.tx() as tx:
        await _apply_asset_access(
            tx,
            callable_repository=_callable_target_binding_repository_for_request(
                request, db_client=tx
            ),
            access_group_repository=_callable_target_access_group_repository_for_request(
                request, db_client=tx
            ),
            route_repository=_route_group_repository_for_request(request, db_client=tx),
        )
    await reload_callable_target_grants(request)
    response = await build_scope_asset_access(
        request,
        scope_type="organization",
        scope_id=organization_id,
        organization_id=organization_id,
    )
    response["auto_follow_catalog"] = auto_follow_catalog
    audit_service = getattr(request.app.state, "audit_service", None)
    if audit_service is not None:
        await audit_service.invalidate_content_storage_policy_distributed(organization_id)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ORGANIZATION_ASSET_ACCESS_UPDATE,
        resource_type="organization_asset_access",
        resource_id=organization_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post(
    "/ui/api/organizations",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
    response_model=OrganizationResponse,
)
async def create_organization(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.PLATFORM_ADMIN,
    )
    tier_policy_mode = get_tier_policy_mode_from_app(request.app)
    organization_id = str(payload.get("organization_id") or "").strip()
    if not organization_id:
        organization_id = f"org-{secrets.token_hex(6)}"

    # POST historically behaves as an upsert. Enforcement applies the new
    # primary-tier requirement to newly inserted organizations without forcing
    # an existing legacy organization to migrate merely because an older client
    # still uses this endpoint for updates.
    existing_organization = False
    if tier_policy_mode in {"shadow", "enforce"} and payload.get("primary_tier") is None:
        existing_rows = await db.query_raw(
            """
            SELECT organization_id
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        existing_organization = bool(existing_rows)
    legacy_policy_exception = _optional_bool(
        payload.get("legacy_policy_exception"),
        "legacy_policy_exception",
    )
    primary_tier_fields = _normalize_primary_tier_payload(
        payload,
        required=tier_policy_mode == "enforce" and not existing_organization,
    )
    if primary_tier_fields is not None and legacy_policy_exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="legacy_policy_exception cannot be combined with primary_tier",
        )
    if (
        tier_policy_mode == "shadow"
        and not existing_organization
        and primary_tier_fields is None
        and legacy_policy_exception is not True
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "primary_tier is required for new organizations in shadow mode unless "
                "legacy_policy_exception is explicitly true"
            ),
        )
    organization_name_raw = payload.get("organization_name")
    organization_name = (
        str(organization_name_raw).strip() if organization_name_raw is not None else None
    )
    if primary_tier_fields is not None and not organization_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="organization_name is required with primary_tier",
        )
    max_budget = _optional_float(payload.get("max_budget"), "max_budget")
    soft_budget = _optional_float(payload.get("soft_budget"), "soft_budget")
    if max_budget is not None and soft_budget is not None and soft_budget > max_budget:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="soft_budget must be less than or equal to max_budget",
        )
    reset_fields_provided = "budget_duration" in payload or "budget_reset_at" in payload
    budget_duration, budget_reset_at = _resolve_budget_reset_fields(payload)
    budget_reset_at_storage = _budget_reset_storage_value(budget_reset_at)
    rpm_limit = optional_int(payload.get("rpm_limit"), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit"), "tpm_limit")
    rph_limit = optional_int(payload.get("rph_limit"), "rph_limit")
    rpd_limit = optional_int(payload.get("rpd_limit"), "rpd_limit")
    tpd_limit = optional_int(payload.get("tpd_limit"), "tpd_limit")
    model_rpm_limit = _validate_model_limit_dict(payload.get("model_rpm_limit"), "model_rpm_limit")
    model_tpm_limit = _validate_model_limit_dict(payload.get("model_tpm_limit"), "model_tpm_limit")
    if primary_tier_fields is not None and (model_rpm_limit or model_tpm_limit):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model-specific organization limits are not supported with primary_tier; configure them on the tier",
        )
    audit_content_storage_enabled = _optional_bool(
        payload.get("audit_content_storage_enabled"),
        "audit_content_storage_enabled",
    )
    metadata = with_organization_auto_follow_catalog(
        _audit_retention_metadata(payload),
        enabled=False,
    )
    metadata = _apply_budget_reset_metadata(
        metadata,
        duration=budget_duration,
        reset_at=budget_reset_at,
        reset_fields_provided=True,
    )
    route_group_bindings = _normalize_route_group_binding_payloads(
        payload.get("route_group_bindings")
    )
    callable_target_bindings = _normalize_callable_target_binding_payloads(
        payload.get("callable_target_bindings")
    )
    if primary_tier_fields is not None and (route_group_bindings or callable_target_bindings):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="organization asset bootstrap bindings are not supported with primary_tier; configure model access on the tier",
        )
    _validate_route_group_callable_target_overlap(route_group_bindings, callable_target_bindings)
    route_repo = _route_group_repository_for_request(request)
    catalog = callable_catalog(request)
    await _validate_org_route_group_binding_payloads(
        route_repo, binding_payloads=route_group_bindings
    )
    _validate_org_callable_target_binding_payloads(
        binding_payloads=callable_target_bindings,
        catalog=catalog,
    )
    tier_repository = getattr(request.app.state, "tier_repository", None)
    if primary_tier_fields is not None:
        if tier_repository is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tier repository unavailable",
            )
        if not hasattr(db, "tx") or not callable(getattr(tier_repository, "with_db", None)):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tier-managed organization creation requires transaction support",
            )

    async def _apply_create(db_client: Any, *, route_repository, callable_repository):  # noqa: ANN001, ANN202
        current_rows = await db_client.query_raw(
            """
            SELECT organization_id
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        if current_rows:
            await require_active_organization_mutation(db_client, organization_id)
        persisted_organization = await OrganizationAdminRepository(db_client).upsert(
            OrganizationPersistenceValues(
                organization_id=organization_id,
                organization_name=organization_name,
                max_budget=max_budget,
                soft_budget=soft_budget,
                budget_duration=budget_duration,
                budget_reset_at=budget_reset_at_storage,
                rpm_limit=rpm_limit,
                tpm_limit=tpm_limit,
                rph_limit=rph_limit,
                rpd_limit=rpd_limit,
                tpd_limit=tpd_limit,
                model_rpm_limit=model_rpm_limit,
                model_tpm_limit=model_tpm_limit,
                audit_content_storage_enabled=bool(audit_content_storage_enabled),
                metadata=metadata,
            ),
            reset_fields_provided=reset_fields_provided,
        )
        if persisted_organization is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
            )
        response_payload = _organization_response_payload(dict(persisted_organization))
        assignment = None
        scheduled_cache_invalidation = None
        shadow_access_mirrored = False
        effective_callable_target_bindings = callable_target_bindings
        if primary_tier_fields is not None:
            assignment_repository = tier_repository.with_db(db_client)
            assignment = await assignment_repository.upsert_org_assignment_in_current_transaction(
                organization_id=organization_id,
                **primary_tier_fields,
            )
            if assignment is None:
                raise TierAdminNotFoundError("Tier assignment not found")
            cache_invalidation_service = getattr(
                request.app.state, "cache_invalidation_service", None
            )
            scheduled_cache_invalidation = await enqueue_org_tier_assignment_cache_invalidation(
                db_client,
                organization_id=organization_id,
                reason="organization_tier_assignment_create",
                metadata={
                    "assignment_id": assignment.assignment_id,
                    "source": "organization_create",
                },
                max_attempts=max(
                    1,
                    int(getattr(cache_invalidation_service, "max_attempts", 10) or 10),
                ),
            )
            if tier_policy_mode == "shadow":
                effective_callable_target_bindings = await _shadow_tier_callable_target_bindings(
                    assignment_repository,
                    tier_id=primary_tier_fields["tier_id"],
                    tier_version_id=primary_tier_fields["tier_version_id"],
                    catalog=catalog,
                )
                shadow_access_mirrored = True

        applied_route_group_bindings = (
            await _sync_org_route_group_bindings(
                request,
                organization_id=organization_id,
                binding_payloads=route_group_bindings,
                route_repo=route_repository,
                callable_binding_repo=callable_repository,
            )
            if route_group_bindings
            else []
        )
        applied_callable_target_bindings = (
            await _sync_org_callable_target_bindings(
                request,
                organization_id=organization_id,
                binding_payloads=effective_callable_target_bindings,
                protected_callable_keys={item["group_key"] for item in route_group_bindings},
                callable_binding_repo=callable_repository,
                route_repo=route_repository,
                catalog=catalog,
            )
            if effective_callable_target_bindings or shadow_access_mirrored
            else []
        )
        response_payload["route_group_bindings"] = applied_route_group_bindings
        response_payload["callable_target_bindings"] = applied_callable_target_bindings
        return (
            response_payload,
            assignment,
            scheduled_cache_invalidation,
            shadow_access_mirrored,
        )

    try:
        if not hasattr(db, "tx"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Organization mutation requires transaction support",
            )
        async with db.tx() as tx:
            (
                response,
                primary_tier_assignment,
                scheduled_cache_invalidation,
                shadow_access_mirrored,
            ) = await _apply_create(
                tx,
                route_repository=_route_group_repository_for_request(request, db_client=tx),
                callable_repository=_callable_target_binding_repository_for_request(
                    request, db_client=tx
                ),
            )
    except ValueError as exc:
        if primary_tier_fields is not None:
            raise _tier_assignment_http_error(_assignment_admin_error(exc)) from exc
        raise
    except TierAdminError as exc:
        raise _tier_assignment_http_error(exc) from exc
    except Exception as exc:
        if primary_tier_fields is not None:
            mapped_error = _assignment_storage_error(exc)
            if mapped_error is not None:
                raise _tier_assignment_http_error(mapped_error) from exc
        raise

    if route_group_bindings or callable_target_bindings or shadow_access_mirrored:
        await reload_callable_target_grants(request)
    response["service_policy"] = _organization_service_policy_payload(
        response,
        assignment_rows=[],
        tier_policy_mode=tier_policy_mode,
    )
    response["capabilities"] = build_organization_capabilities(scope, response)
    if primary_tier_assignment is not None and scheduled_cache_invalidation is not None:
        cache_invalidation_service = getattr(request.app.state, "cache_invalidation_service", None)
        await apply_best_effort_org_cache_invalidation(
            scheduled_cache_invalidation,
            cache_invalidation_service=cache_invalidation_service,
            organization_id=organization_id,
            reason="organization_tier_assignment_create",
        )
        await reload_tier_policy(request)
        response["service_policy"] = _organization_service_policy_payload(
            response,
            assignment_rows=[
                {
                    **serialize_tier_assignment(primary_tier_assignment),
                    "effective_tier_version_id": primary_tier_assignment.tier_version_id,
                }
            ],
            tier_policy_mode=tier_policy_mode,
        )
        response["primary_tier_assignment"] = serialize_tier_assignment(primary_tier_assignment)
    elif primary_tier_fields is None:
        # Preserve the historical POST-upsert contract without making response
        # generation capable of turning a committed mutation into a 5xx. This
        # also reports any assignment that already existed before the upsert.
        try:
            service_policies = await _load_organization_service_policies(
                db,
                [response],
                tier_policy_mode=tier_policy_mode,
            )
        except Exception:
            logger.exception(
                "Failed loading organization service policy after create/upsert",
                extra={"organization_id": organization_id},
            )
        else:
            response["service_policy"] = service_policies.get(
                organization_id,
                response["service_policy"],
            )
    audit_service = getattr(request.app.state, "audit_service", None)
    if audit_service is not None:
        await audit_service.invalidate_content_storage_policy_distributed(organization_id)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ORGANIZATION_CREATE,
        resource_type="organization",
        resource_id=organization_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.put(
    "/ui/api/organizations/{organization_id}",
    dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))],
    response_model=OrganizationResponse,
)
async def update_organization(
    request: Request,
    organization_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE
    )
    _reject_organization_tier_policy_update_fields(
        payload,
        is_platform_admin=scope.is_platform_admin,
    )
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, soft_budget, spend, budget_duration, budget_reset_at, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, model_rpm_limit, model_tpm_limit, audit_content_storage_enabled, metadata, lifecycle_state, deletion_requested_at, deletion_not_before_at, created_at, updated_at
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    existing = dict(rows[0])
    tier_policy_mode = get_tier_policy_mode_from_app(request.app)
    existing_service_policies = await _load_organization_service_policies(
        db,
        [existing],
        tier_policy_mode=tier_policy_mode,
    )
    tier_authoritative = bool(
        existing_service_policies.get(organization_id, {}).get("tier_authoritative")
    )
    organization_name = payload.get("organization_name", existing.get("organization_name"))
    max_budget = _optional_float(
        payload.get("max_budget", existing.get("max_budget")), "max_budget"
    )
    soft_budget = _optional_float(
        payload.get("soft_budget", existing.get("soft_budget")), "soft_budget"
    )
    if max_budget is not None and soft_budget is not None and soft_budget > max_budget:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="soft_budget must be less than or equal to max_budget",
        )
    reset_fields_provided = "budget_duration" in payload or "budget_reset_at" in payload
    budget_duration, budget_reset_at = _resolve_budget_reset_fields(payload, existing=existing)
    budget_reset_at_storage = _budget_reset_storage_value(budget_reset_at)
    rpm_limit = optional_int(payload.get("rpm_limit", existing.get("rpm_limit")), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit", existing.get("tpm_limit")), "tpm_limit")
    rph_limit = optional_int(payload.get("rph_limit", existing.get("rph_limit")), "rph_limit")
    rpd_limit = optional_int(payload.get("rpd_limit", existing.get("rpd_limit")), "rpd_limit")
    tpd_limit = optional_int(payload.get("tpd_limit", existing.get("tpd_limit")), "tpd_limit")
    model_rpm_limit = _validate_model_limit_dict(
        payload.get("model_rpm_limit", existing.get("model_rpm_limit")), "model_rpm_limit"
    )
    model_tpm_limit = _validate_model_limit_dict(
        payload.get("model_tpm_limit", existing.get("model_tpm_limit")), "model_tpm_limit"
    )
    audit_content_storage_enabled = _optional_bool(
        payload.get("audit_content_storage_enabled", existing.get("audit_content_storage_enabled")),
        "audit_content_storage_enabled",
    )
    existing_metadata = (
        existing.get("metadata") if isinstance(existing.get("metadata"), dict) else None
    )
    metadata = _audit_retention_metadata(payload, existing_metadata)
    route_group_bindings = (
        _normalize_route_group_binding_payloads(payload.get("route_group_bindings"))
        if "route_group_bindings" in payload
        else None
    )
    callable_target_bindings = (
        _normalize_callable_target_binding_payloads(payload.get("callable_target_bindings"))
        if "callable_target_bindings" in payload
        else None
    )
    if tier_authoritative:
        if ("model_rpm_limit" in payload and model_rpm_limit) or (
            "model_tpm_limit" in payload and model_tpm_limit
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Model-specific organization limits cannot be configured while an active "
                    "tier policy is authoritative; configure them on the tier"
                ),
            )
        if route_group_bindings is not None or callable_target_bindings is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Organization asset bootstrap bindings cannot be changed while an active "
                    "tier policy is authoritative; configure model access on the tier"
                ),
            )
    _validate_route_group_callable_target_overlap(
        route_group_bindings or [], callable_target_bindings or []
    )
    if (
        route_group_bindings is not None or callable_target_bindings is not None
    ) and not scope.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform admins can update asset bootstrap bindings",
        )
    route_repo = _route_group_repository_for_request(request)
    catalog = callable_catalog(request)
    if route_group_bindings is not None:
        await _validate_org_route_group_binding_payloads(
            route_repo, binding_payloads=route_group_bindings
        )
    if callable_target_bindings is not None:
        _validate_org_callable_target_binding_payloads(
            binding_payloads=callable_target_bindings,
            catalog=catalog,
        )
    metadata = with_organization_auto_follow_catalog(
        metadata if metadata is not None else existing_metadata,
        enabled=(
            organization_auto_follow_catalog(existing_metadata)
            and route_group_bindings is None
            and callable_target_bindings is None
        ),
    )
    metadata = _apply_budget_reset_metadata(
        metadata,
        duration=budget_duration,
        reset_at=budget_reset_at,
        reset_fields_provided=reset_fields_provided,
    )

    async def _apply_update(db_client: Any, *, route_repository, callable_repository):  # noqa: ANN001, ANN202
        await require_active_organization_mutation(db_client, organization_id)
        updated_organization = await OrganizationAdminRepository(db_client).update(
            OrganizationPersistenceValues(
                organization_id=organization_id,
                organization_name=organization_name,
                max_budget=max_budget,
                soft_budget=soft_budget,
                budget_duration=budget_duration,
                budget_reset_at=budget_reset_at_storage,
                rpm_limit=rpm_limit,
                tpm_limit=tpm_limit,
                rph_limit=rph_limit,
                rpd_limit=rpd_limit,
                tpd_limit=tpd_limit,
                model_rpm_limit=model_rpm_limit,
                model_tpm_limit=model_tpm_limit,
                audit_content_storage_enabled=bool(audit_content_storage_enabled),
                metadata=metadata,
            )
        )
        if updated_organization is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
            )
        updated_payload = _organization_response_payload(
            dict(updated_organization),
            capabilities=build_organization_capabilities(scope, updated_organization),
        )
        if isinstance(updated_payload, dict):
            updated_payload["route_group_bindings"] = (
                await _sync_org_route_group_bindings(
                    request,
                    organization_id=organization_id,
                    binding_payloads=route_group_bindings,
                    route_repo=route_repository,
                    callable_binding_repo=callable_repository,
                )
                if route_group_bindings is not None
                else await _list_org_route_group_bindings(request, organization_id)
            )
            updated_payload["callable_target_bindings"] = (
                await _sync_org_callable_target_bindings(
                    request,
                    organization_id=organization_id,
                    binding_payloads=callable_target_bindings,
                    protected_callable_keys={
                        item["group_key"] for item in (route_group_bindings or [])
                    },
                    callable_binding_repo=callable_repository,
                    route_repo=route_repository,
                    catalog=catalog,
                )
                if callable_target_bindings is not None
                else await _list_org_callable_target_bindings(request, organization_id)
            )
        return updated_payload

    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Organization mutation requires transaction support",
        )
    async with db.tx() as tx:
        updated = await _apply_update(
            tx,
            route_repository=_route_group_repository_for_request(request, db_client=tx),
            callable_repository=_callable_target_binding_repository_for_request(
                request, db_client=tx
            ),
        )
    if route_group_bindings is not None or callable_target_bindings is not None:
        await reload_callable_target_grants(request)
    if isinstance(updated, dict):
        service_policies = await _load_organization_service_policies(
            db,
            [updated],
            tier_policy_mode=get_tier_policy_mode_from_app(request.app),
        )
        updated["service_policy"] = service_policies.get(
            organization_id,
            _organization_service_policy_payload(
                updated,
                assignment_rows=[],
                tier_policy_mode=get_tier_policy_mode_from_app(request.app),
            ),
        )
    key_service = getattr(request.app.state, "key_service", None)
    if key_service is not None:
        try:
            await key_service.invalidate_keys_for_org(organization_id)
        except Exception:
            pass
    audit_service = getattr(request.app.state, "audit_service", None)
    if audit_service is not None:
        await audit_service.invalidate_content_storage_policy_distributed(organization_id)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ORGANIZATION_UPDATE,
        resource_type="organization",
        resource_id=organization_id,
        request_payload=payload,
        response_payload=updated if isinstance(updated, dict) else None,
        before=to_json_value(existing),
        after=updated if isinstance(updated, dict) else None,
    )
    return updated


@router.get(
    "/ui/api/organizations/{organization_id}/members",
    dependencies=[Depends(require_admin_permission(Permission.ORG_READ))],
)
async def list_organization_members(request: Request, organization_id: str) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT
            om.membership_id,
            om.account_id,
            pa.email,
            om.role AS org_role,
            om.created_at,
            om.updated_at,
            COALESCE(team_stats.team_count, 0) AS team_count,
            COALESCE(team_stats.teams, ARRAY[]::text[]) AS teams
        FROM deltallm_organizationmembership om
        JOIN deltallm_platformaccount pa
          ON pa.account_id = om.account_id
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*)::int AS team_count,
                ARRAY_AGG(COALESCE(t.team_alias, t.team_id) ORDER BY t.team_alias, t.team_id) AS teams
            FROM deltallm_teammembership tm
            JOIN deltallm_teamtable t
              ON t.team_id = tm.team_id
            WHERE tm.account_id = om.account_id
              AND t.organization_id = $1
        ) team_stats ON true
        WHERE om.organization_id = $1
        ORDER BY om.created_at DESC
        """,
        organization_id,
    )
    return [to_json_value(dict(row)) for row in rows]


@router.get(
    "/ui/api/organizations/{organization_id}/member-candidates",
    dependencies=[Depends(require_admin_permission(Permission.ORG_READ))],
)
async def list_organization_member_candidates(
    request: Request,
    organization_id: str,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> list[dict[str, Any]]:
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.ORG_READ
    )
    db = db_or_503(request)
    if not scope.is_platform_admin and organization_id not in scope.org_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    # Privacy-by-default: do not return broad account listings.
    # Callers must provide an exact user identifier (case-insensitive).
    normalized_search = (search or "").strip()
    if not normalized_search:
        return []

    clauses: list[str] = []
    params: list[Any] = []

    params.append(normalized_search)
    # Exact (case-insensitive) matching on either email or account_id.
    clauses.append(
        f"(lower(email) = lower(${len(params)}) OR lower(account_id::text) = lower(${len(params)}))"
    )
    if not scope.is_platform_admin:
        params.append(organization_id)
        clauses.append(
            "("
            f"EXISTS (SELECT 1 FROM deltallm_organizationmembership om WHERE om.account_id = deltallm_platformaccount.account_id AND om.organization_id = ${len(params)})"
            " OR "
            "NOT EXISTS (SELECT 1 FROM deltallm_organizationmembership om_any WHERE om_any.account_id = deltallm_platformaccount.account_id)"
            ")"
        )

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = await db.query_raw(
        f"""
        SELECT account_id, email, role, is_active, created_at, updated_at
        FROM deltallm_platformaccount
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    return [to_json_value(dict(row)) for row in rows]


@router.post(
    "/ui/api/organizations/{organization_id}/members",
    dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))],
)
async def add_organization_member(
    request: Request, organization_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    account_id = payload.get("account_id")
    email = str(payload.get("email") or "").strip().lower()
    try:
        role = validate_organization_role(payload.get("role") or OrganizationRole.MEMBER)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    org_rows = await db.query_raw(
        "SELECT organization_id FROM deltallm_organizationtable WHERE organization_id = $1 LIMIT 1",
        organization_id,
    )
    if not org_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    if not account_id and email:
        rows = await db.query_raw(
            "SELECT account_id FROM deltallm_platformaccount WHERE lower(email)=lower($1) LIMIT 1",
            email,
        )
        if rows:
            account_id = rows[0].get("account_id")
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="account_id or known email is required"
        )
    account_rows = await db.query_raw(
        "SELECT account_id FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1",
        account_id,
    )
    if not account_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Organization membership mutation requires transaction support",
        )
    async with db.tx() as tx:
        await require_active_organization_mutation(tx, organization_id)
        await tx.execute_raw(
            """
            INSERT INTO deltallm_organizationmembership (membership_id, account_id, organization_id, role, created_at, updated_at)
            VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
            ON CONFLICT (account_id, organization_id)
            DO UPDATE SET role = EXCLUDED.role, updated_at = NOW()
            """,
            account_id,
            organization_id,
            role,
        )

        rows = await tx.query_raw(
            """
            SELECT membership_id, account_id, organization_id, role, created_at, updated_at
            FROM deltallm_organizationmembership
            WHERE account_id = $1 AND organization_id = $2
            LIMIT 1
            """,
            account_id,
            organization_id,
        )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership upsert failed"
        )
    response = to_json_value(dict(rows[0]))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_RBAC_ORG_MEMBERSHIP_UPSERT,
        resource_type="organization_membership",
        resource_id=str(rows[0].get("membership_id") or ""),
        request_payload=payload,
        response_payload=response if isinstance(response, dict) else None,
    )
    return response


@router.delete(
    "/ui/api/organizations/{organization_id}/members/{membership_id}",
    dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))],
)
async def remove_organization_member(
    request: Request, organization_id: str, membership_id: str
) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Organization membership mutation requires transaction support",
        )
    async with db.tx() as tx:
        await require_active_organization_mutation(tx, organization_id)
        rows = await tx.query_raw(
            """
            SELECT membership_id, account_id
            FROM deltallm_organizationmembership
            WHERE membership_id = $1 AND organization_id = $2
            LIMIT 1
            FOR UPDATE
            """,
            membership_id,
            organization_id,
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization membership not found",
            )
        account_id = rows[0].get("account_id")
        removed_team_memberships = await tx.execute_raw(
            """
            DELETE FROM deltallm_teammembership
            WHERE account_id = $1
              AND team_id IN (
                SELECT team_id
                FROM deltallm_teamtable
                WHERE organization_id = $2
              )
            """,
            account_id,
            organization_id,
        )
        deleted = await tx.execute_raw(
            "DELETE FROM deltallm_organizationmembership WHERE membership_id = $1",
            membership_id,
        )
    response = {
        "deleted": int(deleted or 0) > 0,
        "team_memberships_removed": int(removed_team_memberships or 0),
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_RBAC_ORG_MEMBERSHIP_DELETE,
        resource_type="organization_membership",
        resource_id=membership_id,
        response_payload=response,
    )
    return response


@router.get(
    "/ui/api/organizations/{organization_id}/teams",
    dependencies=[Depends(require_admin_permission(Permission.ORG_READ))],
)
async def list_organization_teams(request: Request, organization_id: str) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT t.team_id, t.team_alias, t.max_budget, t.spend, t.rpm_limit, t.tpm_limit, t.blocked, t.created_at, t.updated_at,
               (SELECT COUNT(*) FROM deltallm_teammembership tm WHERE tm.team_id = t.team_id) AS member_count
        FROM deltallm_teamtable t
        WHERE t.organization_id = $1
        ORDER BY t.created_at DESC
        """,
        organization_id,
    )
    return [to_json_value(dict(row)) for row in rows]
