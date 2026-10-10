"""Bounded durable observations; cached health is never a financial authority."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from time import monotonic
from typing import Literal, Protocol

from pydantic import Field, model_validator

from src.billing.selector_charge import FrozenBillingContract
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.telemetry.lifecycle import WorkerHealth, WorkerState


MAX_RETAINED_BACKLOG_BYTES = 4096


class AccountingBacklogSnapshot(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    protocol_state: Literal["prepared", "active", "draining", "fenced"]
    partition_count: int = Field(ge=1, le=64)
    outstanding_operations: int = Field(ge=0, le=64_000_000)
    pending_entries: int = Field(ge=0, le=64_000_000)
    pending_bytes: int = Field(ge=0, le=64 * 67_108_864)
    failed_entries: int = Field(ge=0, le=64_000_000)
    oldest_age_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    capacity_saturated: bool

    @model_validator(mode="after")
    def validate_backlog(self) -> AccountingBacklogSnapshot:
        if self.failed_entries > self.pending_entries:
            raise ValueError("failed terminal entries exceed the pending charge")
        if self.pending_entries > self.outstanding_operations:
            raise ValueError("pending terminal entries exceed funded operations")
        if self.pending_entries:
            if self.oldest_age_seconds is None or self.pending_bytes < 4 * self.pending_entries:
                raise ValueError("pending terminal work has incomplete observations")
        elif self.pending_bytes or self.oldest_age_seconds is not None:
            raise ValueError("empty terminal work has inconsistent observations")
        return self

    @property
    def sampled_drained(self) -> bool:
        return self.pending_entries == 0 and self.outstanding_operations == 0


class AccountingBacklogPolicy(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    stale_after_seconds: float = Field(default=5, ge=0.05, le=30, allow_inf_nan=False)
    max_pending_entries: int = Field(default=1_000_000, ge=1, le=64_000_000)
    max_age_seconds: float = Field(default=60, ge=0.1, le=3600, allow_inf_nan=False)


class AccountingBacklogPersistence(Protocol):
    async def snapshot(
        self, *, generation: int, expires_at: float
    ) -> AccountingBacklogSnapshot: ...


class AccountingBacklogProbe:
    """Own one snapshot and reject concurrent refresh without creating a task."""

    def __init__(
        self,
        persistence: AccountingBacklogPersistence,
        policy: AccountingBacklogPolicy,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._policy = AccountingBacklogPolicy(
            generation=policy.generation,
            stale_after_seconds=policy.stale_after_seconds,
            max_pending_entries=policy.max_pending_entries,
            max_age_seconds=policy.max_age_seconds,
        )
        self._persistence = persistence
        self._clock = clock
        self._snapshot: AccountingBacklogSnapshot | None = None
        self._observed_at = 0.0
        self._busy = False
        self._unavailable = False
        self._closed = False

    @property
    def snapshot(self) -> AccountingBacklogSnapshot | None:
        return self._snapshot

    @property
    def retained_snapshot_bytes(self) -> int:
        return 0 if self._snapshot is None else MAX_RETAINED_BACKLOG_BYTES

    @property
    def worker_health(self) -> WorkerHealth:
        if self._closed:
            return WorkerHealth(WorkerState.STOPPING, "probe_closed")
        snapshot = self._snapshot
        if self._unavailable:
            return WorkerHealth(WorkerState.DEGRADED, "backlog_unavailable")
        if snapshot is None:
            return WorkerHealth(WorkerState.STARTING, "backlog_unknown")
        elapsed = self._clock() - self._observed_at
        if not math.isfinite(elapsed) or elapsed < 0 or elapsed >= self._policy.stale_after_seconds:
            return WorkerHealth(WorkerState.DEGRADED, "backlog_stale")
        if snapshot.protocol_state not in {"active", "draining"}:
            return WorkerHealth(WorkerState.FAILED, "generation_inactive")
        if snapshot.failed_entries:
            return WorkerHealth(WorkerState.FAILED, "terminal_failed")
        if snapshot.capacity_saturated:
            return WorkerHealth(WorkerState.DEGRADED, "terminal_capacity")
        if snapshot.pending_entries > self._policy.max_pending_entries:
            return WorkerHealth(WorkerState.DEGRADED, "terminal_backlog_limit")
        age = snapshot.oldest_age_seconds
        if age is not None and age + elapsed > self._policy.max_age_seconds:
            return WorkerHealth(WorkerState.DEGRADED, "terminal_age_limit")
        return WorkerHealth(WorkerState.READY)

    async def refresh(self, *, expires_at: float) -> bool:
        if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
            raise ValueError("accounting backlog deadline is invalid")
        if self._busy or self._closed:
            return False
        if expires_at <= asyncio.get_running_loop().time():
            self._unavailable = True
            return False
        self._busy = True
        observed_at = self._clock()
        try:
            async with asyncio.timeout_at(expires_at):
                value = await self._persistence.snapshot(
                    generation=self._policy.generation, expires_at=expires_at
                )
                value = validated_snapshot(value, generation=self._policy.generation)
            if self._closed or expires_at <= asyncio.get_running_loop().time():
                self._unavailable = True
                return False
            self._snapshot = value
            self._observed_at = observed_at
            self._unavailable = False
            return self.worker_health.ready
        except asyncio.CancelledError:
            self._unavailable = True
            raise
        except (AccountingProtocolUnavailable, TimeoutError):
            self._unavailable = True
            return False
        except Exception:
            self._unavailable = True
            raise
        finally:
            self._busy = False

    def close(self) -> None:
        self._closed = True


def validated_snapshot(
    value: AccountingBacklogSnapshot, *, generation: int
) -> AccountingBacklogSnapshot:
    if type(value) is not AccountingBacklogSnapshot:
        raise invalid_result()
    try:
        frozen = AccountingBacklogSnapshot(
            generation=value.generation,
            protocol_state=value.protocol_state,
            partition_count=value.partition_count,
            outstanding_operations=value.outstanding_operations,
            pending_entries=value.pending_entries,
            pending_bytes=value.pending_bytes,
            failed_entries=value.failed_entries,
            oldest_age_seconds=value.oldest_age_seconds,
            capacity_saturated=value.capacity_saturated,
        )
    except ValueError:
        raise invalid_result() from None
    if frozen.generation != generation:
        raise invalid_result()
    return frozen
