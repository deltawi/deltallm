from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from src.batch.scheduling.dimensions import (
    API_KEY_TENANT_SCOPE_PREFIX,
    DEFAULT_SERVICE_TIER,
    DEFAULT_TENANT_SCOPE_PREFERENCE,
    normalize_tenant_scope_preference,
)
from src.batch.scheduling.modes import resolve_scheduler_modes_from_settings

MAX_QUANTUM_WORK_UNITS = 256


@dataclass(frozen=True, slots=True)
class BatchTenantFairShareConfig:
    enabled: bool = False
    base_quantum_work_units: int = 16
    max_deficit_multiplier: int = 8
    tenant_max_in_flight_work_units: int = 0
    tenant_max_queued_work_units: int = 0
    max_active_flows_per_decision: int = 100
    max_candidate_jobs_per_flow: int = 50
    tenant_scope_preference: tuple[str, ...] = DEFAULT_TENANT_SCOPE_PREFERENCE
    disabled_model_groups: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, settings: Any) -> "BatchTenantFairShareConfig":
        scheduler_modes = resolve_scheduler_modes_from_settings(settings)
        explicit_mode_control = (
            "embeddings_batch_scheduler_mode" in getattr(settings, "model_fields_set", set())
            or "embeddings_batch_scheduler_shadow_mode" in getattr(settings, "model_fields_set", set())
        )
        mode_enabled = scheduler_modes.active_uses_fair_share or scheduler_modes.shadow_uses_fair_share
        return cls(
            enabled=(
                mode_enabled
                if explicit_mode_control
                else bool(getattr(settings, "embeddings_batch_tenant_fair_share_enabled", False))
                or mode_enabled
            ),
            base_quantum_work_units=max(
                1,
                int(getattr(settings, "embeddings_batch_scheduler_base_quantum_work_units", 16) or 16),
            ),
            max_deficit_multiplier=max(
                1,
                int(getattr(settings, "embeddings_batch_scheduler_max_deficit_multiplier", 8) or 8),
            ),
            tenant_max_in_flight_work_units=max(
                0,
                int(getattr(settings, "embeddings_batch_tenant_max_in_flight_work_units", 0) or 0),
            ),
            tenant_max_queued_work_units=max(
                0,
                int(getattr(settings, "embeddings_batch_tenant_max_queued_work_units", 0) or 0),
            ),
            max_active_flows_per_decision=max(
                1,
                min(
                    1_000,
                    int(
                        getattr(
                            settings,
                            "embeddings_batch_scheduler_max_active_flows_per_decision",
                            100,
                        )
                        or 100
                    ),
                ),
            ),
            max_candidate_jobs_per_flow=max(
                1,
                min(
                    1_000,
                    int(
                        getattr(
                            settings,
                            "embeddings_batch_scheduler_max_candidate_jobs_per_flow",
                            50,
                        )
                        or 50
                    ),
                ),
            ),
            tenant_scope_preference=parse_tenant_scope_preference(
                getattr(
                    settings,
                    "embeddings_batch_tenant_scope_preference",
                    ",".join(DEFAULT_TENANT_SCOPE_PREFERENCE),
                )
            ),
            disabled_model_groups=parse_model_group_list(
                getattr(settings, "embeddings_batch_tenant_fair_share_disabled_model_groups", ())
            ),
        )


def normalize_flow_part(value: object, *, default: str) -> str:
    normalized = str(value or "").strip()
    return normalized or default


def parse_tenant_scope_preference(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_parts: Iterable[object] = value.split(",")
    elif isinstance(value, Iterable):
        raw_parts = value
    else:
        raw_parts = None
    return normalize_tenant_scope_preference(raw_parts)


def parse_model_group_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_parts: Iterable[object] = value.split(",")
    elif isinstance(value, Iterable):
        raw_parts = value
    else:
        raw_parts = ()
    seen: set[str] = set()
    parsed: list[str] = []
    for part in raw_parts:
        normalized = str(part or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed.append(normalized)
    return tuple(parsed)


def default_flow_weight(*, service_tier: str) -> int:
    del service_tier
    return 1


def quantum_for_weight(*, base_quantum_work_units: int, weight: int) -> int:
    return max(1, min(MAX_QUANTUM_WORK_UNITS, int(base_quantum_work_units) * max(1, int(weight))))


def max_deficit_for_flow(*, quantum_work_units: int, max_deficit_multiplier: int) -> int:
    return max(1, int(quantum_work_units)) * max(1, int(max_deficit_multiplier))


def build_flow_id(
    *,
    service_tier: str,
    model_group: str,
    tenant_scope_type: str,
    tenant_scope_id: str,
) -> str:
    normalized = "\x1f".join(
        (
            normalize_flow_part(service_tier, default=DEFAULT_SERVICE_TIER),
            normalize_flow_part(model_group, default="unknown"),
            normalize_flow_part(tenant_scope_type, default="anonymous"),
            normalize_flow_part(tenant_scope_id, default="anonymous"),
        )
    )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"flow_{digest[:32]}"


def display_tenant_scope_id(*, scope_type: str | None, scope_id: str | None) -> str | None:
    normalized_type = str(scope_type or "").strip()
    normalized_id = str(scope_id or "").strip()
    if not normalized_id:
        return None
    if normalized_type == "api_key":
        if normalized_id.startswith(API_KEY_TENANT_SCOPE_PREFIX):
            digest = normalized_id[len(API_KEY_TENANT_SCOPE_PREFIX) :]
            return f"api_key:{digest[:12]}" if digest else "api_key"
        return "api_key"
    return normalized_id
