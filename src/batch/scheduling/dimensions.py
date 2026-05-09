from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from src.batch.scheduling.estimator import size_class_for_work_units

API_KEY_TENANT_SCOPE_PREFIX = "api_key_sha256:"
DEFAULT_SCHEDULER_VERSION = "fifo_v1"
DEFAULT_SERVICE_TIER = "standard"
MIXED_MODEL_GROUP = "mixed"


class ModelGroupResolver(Protocol):
    def resolve_model_group(self, model_name: str) -> str:
        ...


@dataclass(frozen=True, slots=True)
class BatchTenantScope:
    scope_type: str
    scope_id: str


@dataclass(frozen=True, slots=True)
class BatchSchedulingDimensions:
    scheduler_version: str
    scheduling_model: str | None
    scheduling_model_group: str | None
    scheduling_endpoint: str
    tenant_scope_type: str
    tenant_scope_id: str
    service_tier: str
    estimated_work_units: int
    remaining_work_units: int
    size_class: str
    scheduler_debug: dict[str, Any]


def _normalize_optional(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def normalize_service_tier(value: object, *, default: str = DEFAULT_SERVICE_TIER) -> str:
    normalized = _normalize_optional(value)
    return normalized or default


def resolve_scheduler_version(*, active_enabled: bool = False, shadow_enabled: bool = False) -> str:
    # Phase 1 only instruments the FIFO scheduler. The active flag is validated
    # at config load, but keep this helper conservative for direct callers.
    del active_enabled
    if shadow_enabled:
        return "scheduler_v2_shadow"
    return DEFAULT_SCHEDULER_VERSION


def stable_tenant_scope_id(*, scope_type: str, scope_id: str) -> str:
    normalized_type = str(scope_type or "").strip()
    normalized_id = str(scope_id or "").strip()
    if not normalized_id:
        return "anonymous"
    if normalized_type == "api_key":
        if normalized_id.startswith(API_KEY_TENANT_SCOPE_PREFIX):
            return normalized_id
        digest = hashlib.sha256(normalized_id.encode("utf-8")).hexdigest()
        return f"{API_KEY_TENANT_SCOPE_PREFIX}{digest}"
    return normalized_id


def resolve_tenant_scope(
    *,
    organization_id: str | None = None,
    team_id: str | None = None,
    api_key: str | None = None,
    user_id: str | None = None,
) -> BatchTenantScope:
    for scope_type, scope_id in (
        ("organization", organization_id),
        ("team", team_id),
        ("api_key", api_key),
        ("user", user_id),
    ):
        normalized = _normalize_optional(scope_id)
        if normalized is not None:
            return BatchTenantScope(
                scope_type=scope_type,
                scope_id=stable_tenant_scope_id(scope_type=scope_type, scope_id=normalized),
            )
    return BatchTenantScope(scope_type="anonymous", scope_id="anonymous")


def resolve_model_group(model: str | None, resolver: ModelGroupResolver | None = None) -> str | None:
    normalized = _normalize_optional(model)
    if normalized is None:
        return None
    if resolver is None:
        return normalized
    try:
        resolved = resolver.resolve_model_group(normalized)
    except Exception:
        return normalized
    return _normalize_optional(resolved) or normalized


def build_scheduling_dimensions(
    *,
    endpoint: str,
    model: str | None,
    model_group: str | None = None,
    organization_id: str | None = None,
    team_id: str | None = None,
    api_key: str | None = None,
    user_id: str | None = None,
    service_tier: str | None = None,
    estimated_work_units: int = 0,
    remaining_work_units: int | None = None,
    scheduler_version: str | None = None,
    estimator_version: str = "v1",
    scheduler_debug: dict[str, Any] | None = None,
    mixed_model: bool = False,
    strict_model_homogeneity_enabled: bool = False,
) -> BatchSchedulingDimensions:
    tenant = resolve_tenant_scope(
        organization_id=organization_id,
        team_id=team_id,
        api_key=api_key,
        user_id=user_id,
    )
    bounded_work_units = max(0, int(estimated_work_units or 0))
    bounded_remaining = (
        max(0, int(remaining_work_units))
        if remaining_work_units is not None
        else bounded_work_units
    )
    debug = dict(scheduler_debug or {})
    debug.setdefault("estimator_version", estimator_version)
    if mixed_model:
        debug["mixed_model"] = True
        debug["strict_model_homogeneity_enabled"] = bool(strict_model_homogeneity_enabled)
    return BatchSchedulingDimensions(
        scheduler_version=_normalize_optional(scheduler_version) or DEFAULT_SCHEDULER_VERSION,
        scheduling_model=_normalize_optional(model),
        scheduling_model_group=_normalize_optional(model_group) or _normalize_optional(model),
        scheduling_endpoint=str(endpoint or "").strip(),
        tenant_scope_type=tenant.scope_type,
        tenant_scope_id=tenant.scope_id,
        service_tier=normalize_service_tier(service_tier),
        estimated_work_units=bounded_work_units,
        remaining_work_units=bounded_remaining,
        size_class=size_class_for_work_units(bounded_work_units),
        scheduler_debug=debug,
    )
