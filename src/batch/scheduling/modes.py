from __future__ import annotations

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
