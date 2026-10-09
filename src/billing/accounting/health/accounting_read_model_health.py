"""A fixed-cell report observation separates caught-up, stale, and unavailable."""

from __future__ import annotations

import math
from collections.abc import Callable
from time import monotonic

from pydantic import Field, model_validator

from src.billing.charges.selector_charge import FrozenBillingContract
from src.db.accounting_permit_results import invalid_result
from src.metrics.accounting_read_models import (
    observe_read_model_progress,
    unavailable_read_model_progress,
)
from src.telemetry.lifecycle import WorkerHealth, WorkerState


class ReadModelProgress(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    partition_count: int = Field(ge=1, le=64)
    slots: int = Field(ge=0, le=64)
    pending_partitions: int = Field(ge=0, le=64)
    oldest_head_age_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_observation(self) -> ReadModelProgress:
        if self.pending_partitions > self.slots or self.slots > self.partition_count:
            raise ValueError("report observation has inconsistent cells")
        if (self.oldest_head_age_seconds is not None) != (self.pending_partitions > 0):
            raise ValueError("report observation has incomplete queue age")
        return self


class ReadModelHealth:
    def __init__(self, generation: int, *, clock: Callable[[], float] = monotonic) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("report health generation is invalid")
        self.generation = generation
        self._clock = clock
        self.progress: ReadModelProgress | None = None
        self._observed_at = 0.0
        self._unavailable = False

    @property
    def worker_health(self) -> WorkerHealth:
        value = self.progress
        if self._unavailable:
            return WorkerHealth(WorkerState.DEGRADED, "read_model_progress_unavailable")
        if value is None:
            return WorkerHealth(WorkerState.STARTING, "read_model_progress_unknown")
        elapsed = self._clock() - self._observed_at
        if not math.isfinite(elapsed) or not 0 <= elapsed < 5:
            return WorkerHealth(WorkerState.DEGRADED, "read_model_progress_stale")
        if value.slots != value.partition_count:
            return WorkerHealth(WorkerState.FAILED, "read_model_cells_missing")
        if (
            value.oldest_head_age_seconds is not None
            and value.oldest_head_age_seconds + elapsed > 60
        ):
            return WorkerHealth(WorkerState.DEGRADED, "read_model_age_limit")
        return WorkerHealth(WorkerState.READY)

    def observe(self, value: ReadModelProgress, *, observed_at: float) -> None:
        if type(value) is not ReadModelProgress:
            raise invalid_result()
        try:
            value = ReadModelProgress.model_validate(value.model_dump())
        except ValueError:
            raise invalid_result() from None
        if value.generation != self.generation:
            raise invalid_result()
        if type(observed_at) not in (int, float) or not math.isfinite(observed_at):
            raise ValueError("report observation clock is invalid")
        self.progress = value
        self._observed_at = observed_at
        self._unavailable = False
        observe_read_model_progress(
            complete=value.slots == value.partition_count,
            pending_partitions=value.pending_partitions,
            oldest_age_seconds=value.oldest_head_age_seconds,
        )

    def unavailable(self) -> None:
        self._unavailable = True
        unavailable_read_model_progress()
