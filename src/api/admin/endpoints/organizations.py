from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
import secrets
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.auth.roles import OrganizationRole, Permission, validate_organization_role
from src.audit.actions import AuditAction
from src.services.asset_binding_mirror import (
    callable_catalog,
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
from src.db.callable_targets import CallableTargetBindingRepository
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
from src.services.scoped_asset_access import build_scope_asset_access, sync_scope_asset_access_state
from src.services.ui_authorization import build_organization_capabilities

router = APIRouter(tags=["Admin Organizations"])

_BUDGET_RESET_METADATA_KEY = "_budget_reset"
_MONTHLY_ANCHOR_DAY_KEY = "monthly_anchor_day"
_MAX_BUDGET_DURATION_AMOUNT = 10_000


def _organization_response_payload(
    organization: dict[str, Any],
    *,
    capabilities: dict[str, bool] | None = None,
) -> dict[str, Any]:
    payload = to_json_value(dict(organization))
    if isinstance(payload, dict):
        payload["budget_reset_at"] = _serialize_budget_reset_at(organization.get("budget_reset_at"))
        if capabilities is not None:
            payload["capabilities"] = capabilities
    return payload


def _optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a boolean")


def _optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a number") from exc
    if parsed < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be >= 0")
    return parsed


def _optional_budget_duration(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a string")
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an ISO 8601 datetime")
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an ISO 8601 datetime") from exc
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

    duration_raw = payload["budget_duration"] if "budget_duration" in payload else existing.get("budget_duration")
    reset_at_raw = payload["budget_reset_at"] if "budget_reset_at" in payload else existing.get("budget_reset_at")
    duration = _optional_budget_duration(duration_raw, "budget_duration")
    reset_at = _optional_datetime(reset_at_raw, "budget_reset_at")

    if duration is None and reset_at is None:
        return None, None
    if duration is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="budget_duration is required when budget_reset_at is set")
    if reset_at is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="budget_reset_at is required when budget_duration is set")
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
        budget_reset_settings = dict(raw_budget_reset_settings) if isinstance(raw_budget_reset_settings, dict) else {}
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} keys must be non-empty strings")
        try:
            int_val = int(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} values must be integers")
        if int_val < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} values must be non-negative")
        result[k.strip()] = int_val
    return result if result else None


def _audit_retention_metadata(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any] | None:
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be >= 1")
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


async def _validate_org_route_group_binding_payloads(
    repository,  # noqa: ANN001
    *,
    binding_payloads: list[dict[str, Any]],
) -> None:
    if not binding_payloads:
        return
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Route group repository unavailable")

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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="route_group_bindings must be an array")

    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="route_group_bindings entries must be objects")
        group_key = str(item.get("group_key") or "").strip()
        if not group_key:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="route_group_bindings.group_key is required")
        metadata = item.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="route_group_bindings.metadata must be an object")
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="callable_target_bindings must be an array")

    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="callable_target_bindings entries must be objects")
        callable_key = str(item.get("callable_key") or "").strip()
        if not callable_key:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="callable_target_bindings.callable_key is required")
        metadata = item.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="callable_target_bindings.metadata must be an object")
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
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Route group repository unavailable")
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
                await callable_repository.delete_binding(callable_binding.callable_target_binding_id)

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


async def _list_org_route_group_bindings(request: Request, organization_id: str) -> list[dict[str, Any]]:
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
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Callable target binding repository unavailable")

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
        if route_repository is not None and await route_repository.get_group(callable_key) is not None:
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


async def _list_org_callable_target_bindings(request: Request, organization_id: str) -> list[dict[str, Any]]:
    repository = callable_target_binding_repository(request)
    if repository is None:
        return []
    bindings = await list_all_callable_target_bindings(
        repository,
        scope_type="organization",
        scope_id=organization_id,
    )
    return [to_json_value(asdict(binding)) for binding in bindings]


async def _build_org_asset_visibility_preview(request: Request, organization_id: str) -> dict[str, Any]:
    return await build_asset_visibility_preview(request, organization_id=organization_id)


@router.get("/ui/api/organizations")
async def list_organizations(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_READ)
    db = db_or_503(request)

    clauses: list[str] = []
    params: list[Any] = []

    if not scope.is_platform_admin:
        if scope.org_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            clauses.append(f"o.organization_id IN ({ph})")
        else:
            return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if search:
        params.append(f"%{search}%")
        clauses.append(f"(o.organization_name ILIKE ${len(params)} OR o.organization_id ILIKE ${len(params)})")

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    select_cols = """o.organization_id, o.organization_name, o.max_budget, o.soft_budget, o.spend, o.budget_duration, o.budget_reset_at, o.rpm_limit, o.tpm_limit,
                   o.rph_limit, o.rpd_limit, o.tpd_limit,
                   o.model_rpm_limit, o.model_tpm_limit,
                   o.audit_content_storage_enabled, o.metadata, o.created_at, o.updated_at,
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

    return {
        "data": [
            _organization_response_payload(
                dict(row),
                capabilities=build_organization_capabilities(scope, dict(row)),
            )
            for row in rows
        ],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/organizations/{organization_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def get_organization(
    request: Request,
    organization_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_READ)
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, soft_budget, spend, budget_duration, budget_reset_at, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, model_rpm_limit, model_tpm_limit, audit_content_storage_enabled, metadata, created_at, updated_at
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    organization = dict(rows[0])
    payload = _organization_response_payload(
        organization,
        capabilities=build_organization_capabilities(scope, organization),
    )
    if isinstance(payload, dict):
        payload["route_group_bindings"] = await _list_org_route_group_bindings(request, organization_id)
        payload["callable_target_bindings"] = await _list_org_callable_target_bindings(request, organization_id)
    return payload


@router.get("/ui/api/organizations/{organization_id}/asset-visibility", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def get_organization_asset_visibility(
    request: Request,
    organization_id: str,
    user_id: str | None = Query(default=None),
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
    )


@router.get("/ui/api/organizations/{organization_id}/asset-access", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def get_organization_asset_access(
    request: Request,
    organization_id: str,
    include_targets: bool = Query(default=True),
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
    )
    response["auto_follow_catalog"] = await get_organization_auto_follow_catalog(db, organization_id)
    return response


@router.put("/ui/api/organizations/{organization_id}/asset-access", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
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
    auto_follow_catalog = bool(payload.get("select_all_selectable", False))
    async def _apply_asset_access(db_client: Any, *, callable_repository, route_repository) -> None:  # noqa: ANN001, ANN202
        await sync_scope_asset_access_state(
            request,
            scope_type="organization",
            scope_id=organization_id,
            organization_id=organization_id,
            mode=payload.get("mode"),
            selected_callable_keys=payload.get("selected_callable_keys", []),
            select_all_selectable=auto_follow_catalog,
            binding_repository=callable_repository,
            route_group_repository=route_repository,
            reload_after_write=False,
        )
        await set_organization_auto_follow_catalog(
            db_client,
            organization_id,
            enabled=auto_follow_catalog,
        )

    if hasattr(db, "tx"):
        async with db.tx() as tx:
            await _apply_asset_access(
                tx,
                callable_repository=_callable_target_binding_repository_for_request(request, db_client=tx),
                route_repository=_route_group_repository_for_request(request, db_client=tx),
            )
    else:
        await _apply_asset_access(
            db,
            callable_repository=_callable_target_binding_repository_for_request(request),
            route_repository=_route_group_repository_for_request(request),
        )
    await reload_callable_target_grants(request)
    response = await build_scope_asset_access(
        request,
        scope_type="organization",
        scope_id=organization_id,
        organization_id=organization_id,
    )
    response["auto_follow_catalog"] = auto_follow_catalog
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


@router.post("/ui/api/organizations", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def create_organization(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    organization_id = str(payload.get("organization_id") or f"org-{secrets.token_hex(6)}")
    organization_name = payload.get("organization_name")
    max_budget = _optional_float(payload.get("max_budget"), "max_budget")
    soft_budget = _optional_float(payload.get("soft_budget"), "soft_budget")
    if max_budget is not None and soft_budget is not None and soft_budget > max_budget:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="soft_budget must be less than or equal to max_budget")
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
    route_group_bindings = _normalize_route_group_binding_payloads(payload.get("route_group_bindings"))
    callable_target_bindings = _normalize_callable_target_binding_payloads(payload.get("callable_target_bindings"))
    _validate_route_group_callable_target_overlap(route_group_bindings, callable_target_bindings)
    route_repo = _route_group_repository_for_request(request)
    callable_binding_repo = _callable_target_binding_repository_for_request(request)
    catalog = callable_catalog(request)
    await _validate_org_route_group_binding_payloads(route_repo, binding_payloads=route_group_bindings)
    _validate_org_callable_target_binding_payloads(
        binding_payloads=callable_target_bindings,
        catalog=catalog,
    )

    async def _apply_create(db_client: Any, *, route_repository, callable_repository) -> dict[str, Any]:  # noqa: ANN001
        await db_client.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                id,
                organization_id,
                organization_name,
                max_budget,
                soft_budget,
                budget_duration,
                budget_reset_at,
                spend,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                model_rpm_limit,
                model_tpm_limit,
                audit_content_storage_enabled,
                metadata,
                created_at,
                updated_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6::timestamp, 0, $7, $8, $9, $10, $11, $12::jsonb, $13::jsonb, $14, $15::jsonb, NOW(), NOW())
            ON CONFLICT (organization_id)
            DO UPDATE SET
                organization_name = EXCLUDED.organization_name,
                max_budget = EXCLUDED.max_budget,
                soft_budget = EXCLUDED.soft_budget,
                budget_duration = CASE
                    WHEN $16::boolean THEN EXCLUDED.budget_duration
                    ELSE deltallm_organizationtable.budget_duration
                END,
                budget_reset_at = CASE
                    WHEN $16::boolean THEN EXCLUDED.budget_reset_at
                    ELSE deltallm_organizationtable.budget_reset_at
                END,
                rpm_limit = EXCLUDED.rpm_limit,
                tpm_limit = EXCLUDED.tpm_limit,
                rph_limit = EXCLUDED.rph_limit,
                rpd_limit = EXCLUDED.rpd_limit,
                tpd_limit = EXCLUDED.tpd_limit,
                model_rpm_limit = EXCLUDED.model_rpm_limit,
                model_tpm_limit = EXCLUDED.model_tpm_limit,
                audit_content_storage_enabled = EXCLUDED.audit_content_storage_enabled,
                metadata = CASE
                    WHEN NOT $16::boolean
                    THEN NULLIF(COALESCE(deltallm_organizationtable.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb), '{}'::jsonb)
                    WHEN EXCLUDED.budget_duration IS NULL OR EXCLUDED.budget_duration NOT LIKE '%mo'
                    THEN NULLIF((COALESCE(deltallm_organizationtable.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb)) - '_budget_reset', '{}'::jsonb)
                    ELSE NULLIF(COALESCE(deltallm_organizationtable.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb), '{}'::jsonb)
                END,
                updated_at = NOW()
            """,
            organization_id,
            organization_name,
            max_budget,
            soft_budget,
            budget_duration,
            budget_reset_at_storage,
            rpm_limit,
            tpm_limit,
            rph_limit,
            rpd_limit,
            tpd_limit,
            json.dumps(model_rpm_limit) if model_rpm_limit else None,
            json.dumps(model_tpm_limit) if model_tpm_limit else None,
            bool(audit_content_storage_enabled) if audit_content_storage_enabled is not None else False,
            metadata if metadata is not None else None,
            reset_fields_provided,
        )
        persisted_rows = await db_client.query_raw(
            """
            SELECT organization_id, organization_name, max_budget, soft_budget, spend, budget_duration, budget_reset_at, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, model_rpm_limit, model_tpm_limit, audit_content_storage_enabled, metadata, created_at, updated_at
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        if not persisted_rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        response_payload = _organization_response_payload(dict(persisted_rows[0]))
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
                binding_payloads=callable_target_bindings,
                protected_callable_keys={item["group_key"] for item in route_group_bindings},
                callable_binding_repo=callable_repository,
                route_repo=route_repository,
                catalog=catalog,
            )
            if callable_target_bindings
            else []
        )
        response_payload["route_group_bindings"] = applied_route_group_bindings
        response_payload["callable_target_bindings"] = applied_callable_target_bindings
        return response_payload

    if hasattr(db, "tx"):
        async with db.tx() as tx:
            response = await _apply_create(
                tx,
                route_repository=_route_group_repository_for_request(request, db_client=tx),
                callable_repository=_callable_target_binding_repository_for_request(request, db_client=tx),
            )
    else:
        response = await _apply_create(
            db,
            route_repository=route_repo,
            callable_repository=callable_binding_repo,
        )
    if route_group_bindings or callable_target_bindings:
        await reload_callable_target_grants(request)
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


@router.put("/ui/api/organizations/{organization_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def update_organization(
    request: Request,
    organization_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, soft_budget, spend, budget_duration, budget_reset_at, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, model_rpm_limit, model_tpm_limit, audit_content_storage_enabled, metadata, created_at, updated_at
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    existing = dict(rows[0])
    organization_name = payload.get("organization_name", existing.get("organization_name"))
    max_budget = _optional_float(payload.get("max_budget", existing.get("max_budget")), "max_budget")
    soft_budget = _optional_float(payload.get("soft_budget", existing.get("soft_budget")), "soft_budget")
    if max_budget is not None and soft_budget is not None and soft_budget > max_budget:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="soft_budget must be less than or equal to max_budget")
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
    existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else None
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
    _validate_route_group_callable_target_overlap(route_group_bindings or [], callable_target_bindings or [])
    if (route_group_bindings is not None or callable_target_bindings is not None) and not scope.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform admins can update asset bootstrap bindings",
        )
    route_repo = _route_group_repository_for_request(request)
    callable_binding_repo = _callable_target_binding_repository_for_request(request)
    catalog = callable_catalog(request)
    if route_group_bindings is not None:
        await _validate_org_route_group_binding_payloads(route_repo, binding_payloads=route_group_bindings)
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
        await db_client.execute_raw(
            """
            UPDATE deltallm_organizationtable
            SET organization_name = $1,
                max_budget = $2,
                soft_budget = $3,
                budget_duration = $4,
                budget_reset_at = $5::timestamp,
                rpm_limit = $6,
                tpm_limit = $7,
                rph_limit = $8,
                rpd_limit = $9,
                tpd_limit = $10,
                model_rpm_limit = $11::jsonb,
                model_tpm_limit = $12::jsonb,
                audit_content_storage_enabled = $13,
                metadata = $14::jsonb,
                updated_at = NOW()
            WHERE organization_id = $15
            """,
            organization_name,
            max_budget,
            soft_budget,
            budget_duration,
            budget_reset_at_storage,
            rpm_limit,
            tpm_limit,
            rph_limit,
            rpd_limit,
            tpd_limit,
            json.dumps(model_rpm_limit) if model_rpm_limit else None,
            json.dumps(model_tpm_limit) if model_tpm_limit else None,
            bool(audit_content_storage_enabled),
            metadata if metadata is not None else None,
            organization_id,
        )
        updated_rows = await db_client.query_raw(
            """
            SELECT organization_id, organization_name, max_budget, soft_budget, spend, budget_duration, budget_reset_at, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, model_rpm_limit, model_tpm_limit, audit_content_storage_enabled, metadata, created_at, updated_at
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        if not updated_rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        updated_organization = dict(updated_rows[0])
        updated_payload = _organization_response_payload(
            updated_organization,
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
                    protected_callable_keys={item["group_key"] for item in (route_group_bindings or [])},
                    callable_binding_repo=callable_repository,
                    route_repo=route_repository,
                    catalog=catalog,
                )
                if callable_target_bindings is not None
                else await _list_org_callable_target_bindings(request, organization_id)
            )
        return updated_payload

    if hasattr(db, "tx"):
        async with db.tx() as tx:
            updated = await _apply_update(
                tx,
                route_repository=_route_group_repository_for_request(request, db_client=tx),
                callable_repository=_callable_target_binding_repository_for_request(request, db_client=tx),
            )
    else:
        updated = await _apply_update(
            db,
            route_repository=route_repo,
            callable_repository=callable_binding_repo,
        )
    if route_group_bindings is not None or callable_target_bindings is not None:
        await reload_callable_target_grants(request)
    key_service = getattr(request.app.state, "key_service", None)
    if key_service is not None:
        try:
            await key_service.invalidate_keys_for_org(organization_id)
        except Exception:
            pass
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


@router.get("/ui/api/organizations/{organization_id}/members", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
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


@router.get("/ui/api/organizations/{organization_id}/member-candidates", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def list_organization_member_candidates(
    request: Request,
    organization_id: str,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> list[dict[str, Any]]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_READ)
    db = db_or_503(request)
    if not scope.is_platform_admin and organization_id not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    # Privacy-by-default: do not return broad account listings.
    # Callers must provide an exact user identifier (case-insensitive).
    normalized_search = (search or "").strip()
    if not normalized_search:
        return []

    clauses: list[str] = []
    params: list[Any] = []

    params.append(normalized_search)
    # Exact (case-insensitive) matching on either email or account_id.
    clauses.append(f"(lower(email) = lower(${len(params)}) OR lower(account_id::text) = lower(${len(params)}))")
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


@router.post("/ui/api/organizations/{organization_id}/members", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def add_organization_member(request: Request, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id or known email is required")
    account_rows = await db.query_raw(
        "SELECT account_id FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1",
        account_id,
    )
    if not account_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    await db.execute_raw(
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

    rows = await db.query_raw(
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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership upsert failed")
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


@router.delete("/ui/api/organizations/{organization_id}/members/{membership_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def remove_organization_member(request: Request, organization_id: str, membership_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT membership_id, account_id
        FROM deltallm_organizationmembership
        WHERE membership_id = $1 AND organization_id = $2
        LIMIT 1
        """,
        membership_id,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization membership not found")
    account_id = rows[0].get("account_id")
    removed_team_memberships = await db.execute_raw(
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
    deleted = await db.execute_raw(
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


@router.get("/ui/api/organizations/{organization_id}/teams", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
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
