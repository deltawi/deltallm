from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DEFAULT_AGING_SECONDS_PER_WORK_UNIT = 30
DEFAULT_MAX_AGE_CREDIT_WORK_UNITS = 1_000
DEFAULT_MIN_LARGE_JOB_CLAIM_INTERVAL_SECONDS = 30
DEFAULT_SMALL_JOB_MAX_WORK_UNITS = 100
LARGE_SIZE_CLASSES = frozenset({"l", "xl"})


@dataclass(frozen=True, slots=True)
class BatchSizeAgingConfig:
    enabled: bool = False
    aging_seconds_per_work_unit: int = DEFAULT_AGING_SECONDS_PER_WORK_UNIT
    max_age_credit_work_units: int = DEFAULT_MAX_AGE_CREDIT_WORK_UNITS
    min_large_job_claim_interval_seconds: int = DEFAULT_MIN_LARGE_JOB_CLAIM_INTERVAL_SECONDS
    small_job_fast_lane_enabled: bool = False
    small_job_max_work_units: int = DEFAULT_SMALL_JOB_MAX_WORK_UNITS

    @classmethod
    def from_settings(cls, settings: Any) -> "BatchSizeAgingConfig":
        return cls(
            enabled=bool(
                getattr(settings, "embeddings_batch_size_aware_scheduling_enabled", False)
            ),
            aging_seconds_per_work_unit=max(
                1,
                int(
                    getattr(
                        settings,
                        "embeddings_batch_aging_seconds_per_work_unit",
                        DEFAULT_AGING_SECONDS_PER_WORK_UNIT,
                    )
                    or DEFAULT_AGING_SECONDS_PER_WORK_UNIT
                ),
            ),
            max_age_credit_work_units=max(
                0,
                int(
                    getattr(
                        settings,
                        "embeddings_batch_max_age_credit_work_units",
                        DEFAULT_MAX_AGE_CREDIT_WORK_UNITS,
                    )
                    or 0
                ),
            ),
            min_large_job_claim_interval_seconds=max(
                0,
                int(
                    getattr(
                        settings,
                        "embeddings_batch_min_large_job_claim_interval_seconds",
                        DEFAULT_MIN_LARGE_JOB_CLAIM_INTERVAL_SECONDS,
                    )
                    or 0
                ),
            ),
            small_job_fast_lane_enabled=bool(
                getattr(settings, "embeddings_batch_small_job_fast_lane_enabled", False)
            ),
            small_job_max_work_units=max(
                1,
                int(
                    getattr(
                        settings,
                        "embeddings_batch_small_job_max_work_units",
                        DEFAULT_SMALL_JOB_MAX_WORK_UNITS,
                    )
                    or DEFAULT_SMALL_JOB_MAX_WORK_UNITS
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class BatchJobRankInput:
    remaining_work_units: int
    service_tier_weight: int = 1
    queue_entered_at: datetime | None = None
    last_scheduled_at: datetime | None = None
    size_class: str = "unknown"


@dataclass(frozen=True, slots=True)
class BatchJobRankResult:
    rank: float
    age_credit_work_units: int
    policy_reason: str
    large_job_progress_floor: bool


def is_large_size_class(size_class: str | None) -> bool:
    return str(size_class or "").strip().lower() in LARGE_SIZE_CLASSES


def calculate_size_aging_rank(
    job: BatchJobRankInput,
    *,
    now: datetime | None = None,
    aging_seconds_per_work_unit: int = DEFAULT_AGING_SECONDS_PER_WORK_UNIT,
    max_age_credit_work_units: int = DEFAULT_MAX_AGE_CREDIT_WORK_UNITS,
    min_large_job_claim_interval_seconds: int = DEFAULT_MIN_LARGE_JOB_CLAIM_INTERVAL_SECONDS,
    small_job_max_work_units: int = DEFAULT_SMALL_JOB_MAX_WORK_UNITS,
) -> BatchJobRankResult:
    reference_now = now or datetime.now(tz=UTC)
    queue_entered_at = job.queue_entered_at or reference_now
    if queue_entered_at.tzinfo is None:
        queue_entered_at = queue_entered_at.replace(tzinfo=UTC)
    queue_age_seconds = max(0.0, (reference_now - queue_entered_at).total_seconds())
    age_credit_score = min(
        max(0, int(max_age_credit_work_units)),
        queue_age_seconds / max(1, int(aging_seconds_per_work_unit)),
    )
    age_credit_work_units = int(age_credit_score)
    normalized_remaining = max(1, int(job.remaining_work_units or 0)) / max(
        1,
        int(job.service_tier_weight or 1),
    )
    rank = max(0.0, float(normalized_remaining) - float(age_credit_score))

    large_progress_floor = False
    if is_large_size_class(job.size_class) and min_large_job_claim_interval_seconds > 0:
        progress_reference_at = job.last_scheduled_at or queue_entered_at
        if progress_reference_at.tzinfo is None:
            progress_reference_at = progress_reference_at.replace(tzinfo=UTC)
        large_progress_floor = (
            reference_now - progress_reference_at
        ).total_seconds() >= min_large_job_claim_interval_seconds

    if large_progress_floor:
        reason = "large_job_progress_floor"
    elif age_credit_work_units > 0:
        reason = "aging_credit"
    elif normalized_remaining <= max(1, int(small_job_max_work_units)):
        reason = "small_remaining_work"
    else:
        reason = "tenant_fair_share"

    return BatchJobRankResult(
        rank=rank,
        age_credit_work_units=age_credit_work_units,
        policy_reason=reason,
        large_job_progress_floor=large_progress_floor,
    )


def tuned_claim_item_limit(
    *,
    max_items: int,
    min_items_for_microbatch: int,
    size_class: str | None,
) -> int:
    bounded_max_items = max(1, int(max_items))
    if not is_large_size_class(size_class):
        return bounded_max_items
    floor = min(bounded_max_items, max(1, int(min_items_for_microbatch)))
    return max(floor, (bounded_max_items + 1) // 2)
