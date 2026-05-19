from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

SchedulerMode: TypeAlias = Literal[
    "fifo_v1",
    "slice_v1",
    "model_capacity_v1",
    "fair_share_v1",
    "smart_v1",
]
SchedulerShadowMode: TypeAlias = Literal[
    "none",
    "fifo_v1",
    "slice_v1",
    "model_capacity_v1",
    "fair_share_v1",
    "smart_v1",
]

SCHEDULER_MODES: tuple[SchedulerMode, ...] = (
    "fifo_v1",
    "slice_v1",
    "model_capacity_v1",
    "fair_share_v1",
    "smart_v1",
)
SCHEDULER_SHADOW_MODES: tuple[SchedulerShadowMode, ...] = ("none", *SCHEDULER_MODES)


@dataclass(frozen=True, slots=True)
class BatchSchedulerModes:
    active_mode: SchedulerMode = "fifo_v1"
    shadow_mode: SchedulerShadowMode = "none"

    @property
    def active_uses_work_slice(self) -> bool:
        return scheduler_mode_uses_work_slice(self.active_mode)

    @property
    def active_uses_model_capacity(self) -> bool:
        return scheduler_mode_uses_model_capacity(self.active_mode)

    @property
    def active_uses_fair_share(self) -> bool:
        return scheduler_mode_uses_fair_share(self.active_mode)

    @property
    def active_uses_size_aware(self) -> bool:
        return scheduler_mode_uses_size_aware(self.active_mode)

    @property
    def shadow_uses_model_capacity(self) -> bool:
        return scheduler_mode_uses_model_capacity(self.shadow_mode)

    @property
    def shadow_uses_fair_share(self) -> bool:
        return scheduler_mode_uses_fair_share(self.shadow_mode)

    @property
    def shadow_uses_size_aware(self) -> bool:
        return scheduler_mode_uses_size_aware(self.shadow_mode)


@dataclass(frozen=True, slots=True)
class BatchSchedulerRollbackEvent:
    from_mode: str
    to_mode: str
    reason: str


_SCHEDULER_ROLLOUT_RANK: dict[str, int] = {
    "none": -1,
    "fifo_v1": 0,
    "slice_v1": 1,
    "model_capacity_v1": 2,
    "fair_share_v1": 3,
    "smart_v1": 4,
}


def normalize_scheduler_mode(value: object, *, default: SchedulerMode = "fifo_v1") -> SchedulerMode:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SCHEDULER_MODES else default


def normalize_scheduler_shadow_mode(
    value: object,
    *,
    default: SchedulerShadowMode = "none",
) -> SchedulerShadowMode:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SCHEDULER_SHADOW_MODES else default


def scheduler_mode_uses_work_slice(mode: object) -> bool:
    return normalize_scheduler_shadow_mode(mode) in {
        "slice_v1",
        "model_capacity_v1",
        "fair_share_v1",
        "smart_v1",
    }


def scheduler_mode_uses_model_capacity(mode: object) -> bool:
    return normalize_scheduler_shadow_mode(mode) in {
        "model_capacity_v1",
        "fair_share_v1",
        "smart_v1",
    }


def scheduler_mode_uses_fair_share(mode: object) -> bool:
    return normalize_scheduler_shadow_mode(mode) in {"fair_share_v1", "smart_v1"}


def scheduler_mode_uses_size_aware(mode: object) -> bool:
    return normalize_scheduler_shadow_mode(mode) == "smart_v1"


def resolve_legacy_scheduler_mode(settings: Any) -> SchedulerMode:
    if bool(getattr(settings, "embeddings_batch_size_aware_scheduling_enabled", False)):
        return "smart_v1"
    if bool(getattr(settings, "embeddings_batch_tenant_fair_share_enabled", False)):
        return "fair_share_v1"
    if bool(getattr(settings, "embeddings_batch_model_capacity_enabled", False)):
        return "model_capacity_v1"
    if (
        bool(getattr(settings, "embeddings_batch_scheduler_enabled", False))
        or str(getattr(settings, "embeddings_batch_scheduler_claim_mode", "") or "").strip()
        == "work_slice"
    ):
        return "slice_v1"
    return "fifo_v1"


def resolve_legacy_scheduler_shadow_mode(settings: Any) -> SchedulerShadowMode:
    if not bool(getattr(settings, "embeddings_batch_scheduler_shadow_enabled", False)):
        return "none"
    if bool(getattr(settings, "embeddings_batch_size_aware_scheduling_enabled", False)):
        return "smart_v1"
    return "fair_share_v1"


def _field_was_set(settings: Any, field_name: str) -> bool:
    fields_set = getattr(settings, "model_fields_set", set())
    return field_name in fields_set


def resolve_scheduler_modes_from_settings(settings: Any) -> BatchSchedulerModes:
    active_mode = (
        normalize_scheduler_mode(getattr(settings, "embeddings_batch_scheduler_mode", "fifo_v1"))
        if _field_was_set(settings, "embeddings_batch_scheduler_mode")
        else resolve_legacy_scheduler_mode(settings)
    )
    shadow_mode = (
        normalize_scheduler_shadow_mode(
            getattr(settings, "embeddings_batch_scheduler_shadow_mode", "none")
        )
        if _field_was_set(settings, "embeddings_batch_scheduler_shadow_mode")
        else resolve_legacy_scheduler_shadow_mode(settings)
    )
    return BatchSchedulerModes(active_mode=active_mode, shadow_mode=shadow_mode)


def scheduler_version_for_modes(*, active_mode: object, shadow_mode: object = "none") -> str:
    normalized_active = normalize_scheduler_mode(active_mode)
    if normalized_active != "fifo_v1":
        return normalized_active
    normalized_shadow = normalize_scheduler_shadow_mode(shadow_mode)
    if normalized_shadow != "none":
        return f"{normalized_shadow}_shadow"
    return "fifo_v1"


def scheduler_config_fingerprint_from_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


_SCHEDULER_SETTINGS_PAYLOAD_FIELDS: tuple[tuple[str, Any], ...] = (
    ("embeddings_batch_scheduler_shadow_decision_timeout_seconds", None),
    ("embeddings_batch_scheduler_shadow_max_pending_decisions", None),
    ("embeddings_batch_scheduler_strict_model_homogeneity_enabled", False),
    ("embeddings_batch_scheduler_default_service_tier", "standard"),
    ("embeddings_batch_scheduler_estimator_version", "v1"),
    ("embeddings_batch_advisory_lock_mode", "dual"),
    ("embeddings_batch_work_claim_max_items", None),
    ("embeddings_batch_work_claim_max_work_units", None),
    ("embeddings_batch_work_claim_min_items_for_microbatch", None),
    ("embeddings_batch_default_model_max_in_flight", None),
    ("embeddings_batch_default_model_max_claim_work_units", None),
    ("embeddings_batch_model_capacity_fraction", None),
    ("embeddings_batch_model_capacity_refresh_seconds", None),
    ("embeddings_batch_model_capacity_fail_open", None),
    ("embeddings_batch_scheduler_base_quantum_work_units", None),
    ("embeddings_batch_scheduler_max_deficit_multiplier", None),
    ("embeddings_batch_tenant_max_in_flight_work_units", None),
    ("embeddings_batch_tenant_max_queued_work_units", None),
    ("embeddings_batch_scheduler_max_active_flows_per_decision", None),
    ("embeddings_batch_scheduler_max_candidate_jobs_per_flow", None),
    ("embeddings_batch_tenant_scope_preference", None),
    ("embeddings_batch_tenant_fair_share_disabled_model_groups", None),
    ("embeddings_batch_aging_seconds_per_work_unit", None),
    ("embeddings_batch_max_age_credit_work_units", None),
    ("embeddings_batch_min_large_job_claim_interval_seconds", None),
    ("embeddings_batch_small_job_fast_lane_enabled", None),
    ("embeddings_batch_small_job_max_work_units", None),
)

_WORKER_CONFIG_PAYLOAD_FIELDS: tuple[tuple[str, str], ...] = (
    ("embeddings_batch_scheduler_claim_mode", "scheduler_claim_mode"),
    (
        "embeddings_batch_scheduler_shadow_decision_timeout_seconds",
        "scheduler_shadow_decision_timeout_seconds",
    ),
    (
        "embeddings_batch_scheduler_shadow_max_pending_decisions",
        "scheduler_shadow_max_pending_decisions",
    ),
    ("embeddings_batch_work_claim_max_items", "work_claim_max_items"),
    ("embeddings_batch_work_claim_max_work_units", "work_claim_max_work_units"),
    ("embeddings_batch_work_claim_min_items_for_microbatch", "work_claim_min_items_for_microbatch"),
    ("embeddings_batch_model_capacity_enabled", "model_capacity_enabled"),
    ("embeddings_batch_tenant_fair_share_enabled", "tenant_fair_share_enabled"),
    (
        "embeddings_batch_scheduler_base_quantum_work_units",
        "tenant_fair_share_base_quantum_work_units",
    ),
    (
        "embeddings_batch_scheduler_max_deficit_multiplier",
        "tenant_fair_share_max_deficit_multiplier",
    ),
    ("embeddings_batch_tenant_max_in_flight_work_units", "tenant_max_in_flight_work_units"),
    (
        "embeddings_batch_scheduler_max_active_flows_per_decision",
        "tenant_fair_share_max_active_flows_per_decision",
    ),
    (
        "embeddings_batch_scheduler_max_candidate_jobs_per_flow",
        "tenant_fair_share_max_candidate_jobs_per_flow",
    ),
    (
        "embeddings_batch_tenant_fair_share_disabled_model_groups",
        "tenant_fair_share_disabled_model_groups",
    ),
    ("embeddings_batch_size_aware_scheduling_enabled", "size_aware_scheduling_enabled"),
    ("embeddings_batch_aging_seconds_per_work_unit", "aging_seconds_per_work_unit"),
    ("embeddings_batch_max_age_credit_work_units", "max_age_credit_work_units"),
    (
        "embeddings_batch_min_large_job_claim_interval_seconds",
        "min_large_job_claim_interval_seconds",
    ),
    ("embeddings_batch_small_job_fast_lane_enabled", "small_job_fast_lane_enabled"),
    ("embeddings_batch_small_job_max_work_units", "small_job_max_work_units"),
)


def _fields_from(source: Any, fields: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return {field_name: getattr(source, field_name, default) for field_name, default in fields}


def _renamed_fields_from(source: Any, fields: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return {
        payload_name: getattr(source, source_name, None)
        for payload_name, source_name in fields
    }


def scheduler_config_payload_from_settings(settings: Any) -> dict[str, Any]:
    modes = resolve_scheduler_modes_from_settings(settings)
    explicit_mode_control = (
        "embeddings_batch_scheduler_mode" in getattr(settings, "model_fields_set", set())
        or "embeddings_batch_scheduler_shadow_mode" in getattr(settings, "model_fields_set", set())
    )
    model_capacity_mode_enabled = (
        modes.active_uses_model_capacity or modes.shadow_uses_model_capacity
    )
    fair_share_mode_enabled = modes.active_uses_fair_share or modes.shadow_uses_fair_share
    size_aware_mode_enabled = modes.active_uses_size_aware or modes.shadow_uses_size_aware
    payload = _fields_from(settings, _SCHEDULER_SETTINGS_PAYLOAD_FIELDS)
    payload.update(
        {
            "active_mode": modes.active_mode,
            "shadow_mode": modes.shadow_mode,
            "embeddings_batch_scheduler_claim_mode": (
                "work_slice"
                if modes.active_uses_work_slice
                else getattr(settings, "embeddings_batch_scheduler_claim_mode", "job_fifo")
            ),
            "embeddings_batch_scheduler_enabled": bool(
                modes.active_mode != "fifo_v1" or modes.shadow_mode != "none"
            ),
            "embeddings_batch_scheduler_shadow_enabled": modes.shadow_mode != "none",
            "embeddings_batch_model_capacity_enabled": (
                model_capacity_mode_enabled
                if explicit_mode_control
                else bool(getattr(settings, "embeddings_batch_model_capacity_enabled", False))
                or model_capacity_mode_enabled
            ),
            "embeddings_batch_tenant_fair_share_enabled": (
                fair_share_mode_enabled
                if explicit_mode_control
                else bool(getattr(settings, "embeddings_batch_tenant_fair_share_enabled", False))
                or fair_share_mode_enabled
            ),
            "embeddings_batch_size_aware_scheduling_enabled": (
                size_aware_mode_enabled
                if explicit_mode_control
                else bool(getattr(settings, "embeddings_batch_size_aware_scheduling_enabled", False))
                or size_aware_mode_enabled
            ),
        }
    )
    return payload


def scheduler_config_fingerprint(settings: Any) -> str:
    return scheduler_config_fingerprint_from_payload(
        scheduler_config_payload_from_settings(settings)
    )


def scheduler_worker_config_fingerprint(
    config: Any,
    *,
    general_settings: Any,
    model_capacity_config: Any | None = None,
    tenant_fair_share_config: Any | None = None,
) -> str:
    active_mode = normalize_scheduler_mode(getattr(config, "scheduler_mode", "fifo_v1"))
    shadow_mode = normalize_scheduler_shadow_mode(
        getattr(config, "scheduler_shadow_mode", "none")
    )
    payload = scheduler_config_payload_from_settings(general_settings)
    payload.update(_renamed_fields_from(config, _WORKER_CONFIG_PAYLOAD_FIELDS))
    payload.update(
        {
            "active_mode": active_mode,
            "shadow_mode": shadow_mode,
            "embeddings_batch_scheduler_enabled": bool(
                active_mode != "fifo_v1" or shadow_mode != "none"
            ),
            "embeddings_batch_scheduler_shadow_enabled": shadow_mode != "none",
        }
    )
    if model_capacity_config is not None:
        payload.update(
            {
                "embeddings_batch_default_model_max_in_flight": getattr(
                    model_capacity_config,
                    "default_model_max_in_flight",
                    None,
                ),
                "embeddings_batch_default_model_max_claim_work_units": getattr(
                    model_capacity_config,
                    "default_model_max_claim_work_units",
                    None,
                ),
                "embeddings_batch_model_capacity_fraction": getattr(
                    model_capacity_config,
                    "capacity_fraction",
                    None,
                ),
                "embeddings_batch_model_capacity_refresh_seconds": getattr(
                    model_capacity_config,
                    "refresh_seconds",
                    None,
                ),
                "embeddings_batch_model_capacity_fail_open": getattr(
                    model_capacity_config,
                    "fail_open",
                    None,
                ),
            }
        )
    if tenant_fair_share_config is not None:
        payload.update(
            {
                "embeddings_batch_tenant_max_queued_work_units": getattr(
                    tenant_fair_share_config,
                    "tenant_max_queued_work_units",
                    None,
                ),
                "embeddings_batch_tenant_scope_preference": ",".join(
                    getattr(
                        tenant_fair_share_config,
                        "tenant_scope_preference",
                        (),
                    )
                ),
            }
        )
    return scheduler_config_fingerprint_from_payload(payload)


def scheduler_rollout_rank(mode: object) -> int:
    normalized = normalize_scheduler_shadow_mode(mode)
    return _SCHEDULER_ROLLOUT_RANK[normalized]


def scheduler_rollback_events(
    *,
    previous: BatchSchedulerModes,
    current: BatchSchedulerModes,
) -> tuple[BatchSchedulerRollbackEvent, ...]:
    events: list[BatchSchedulerRollbackEvent] = []
    if scheduler_rollout_rank(current.active_mode) < scheduler_rollout_rank(previous.active_mode):
        events.append(
            BatchSchedulerRollbackEvent(
                from_mode=previous.active_mode,
                to_mode=current.active_mode,
                reason="active_mode_downgrade",
            )
        )
    if previous.shadow_mode != "none" and current.shadow_mode == "none":
        events.append(
            BatchSchedulerRollbackEvent(
                from_mode=previous.shadow_mode,
                to_mode=current.shadow_mode,
                reason="shadow_disabled",
            )
        )
    elif (
        previous.shadow_mode != "none"
        and current.shadow_mode != "none"
        and scheduler_rollout_rank(current.shadow_mode) < scheduler_rollout_rank(previous.shadow_mode)
    ):
        events.append(
            BatchSchedulerRollbackEvent(
                from_mode=previous.shadow_mode,
                to_mode=current.shadow_mode,
                reason="shadow_mode_downgrade",
            )
        )
    return tuple(events)
