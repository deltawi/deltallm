"""Projection presence is a bounded health lease, never a financial authority."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from src.billing.charges.selector_charge import FrozenBillingContract
from src.concurrency import CapacityGateFull
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.db.telemetry_acceptance import AcceptanceFailure
from src.telemetry.lifecycle import WorkerHealth, WorkerHealthSource, WorkerState


class ProjectionLease(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    slot: int = Field(ge=0, le=63)
    owner_token: UUID


class ProjectionPresence(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    present_slots: int = Field(ge=0, le=64)
    ready_slots: int = Field(ge=0, le=64)

    @model_validator(mode="after")
    def validate_slots(self) -> ProjectionPresence:
        if self.ready_slots > self.present_slots:
            raise ValueError("ready projection slots exceed present slots")
        return self


class ProjectionPresencePersistence(Protocol):
    async def initialize(self, *, generation: int, expires_at: float) -> None: ...

    async def acquire(
        self, *, generation: int, owner_token: UUID, lease_seconds: int, expires_at: float
    ) -> ProjectionLease | None: ...

    async def publish(
        self, lease: ProjectionLease, *, ready: bool, lease_seconds: int, expires_at: float
    ) -> bool: ...

    async def release(self, lease: ProjectionLease, *, expires_at: float) -> bool: ...


class ProjectionPresencePublisher:
    """The recovery task renews one fenced slot after its actual checks pass."""

    def __init__(
        self,
        persistence: ProjectionPresencePersistence,
        *,
        generation: int,
        processing: WorkerHealthSource,
        lease_seconds: int = 10,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("projection presence generation is invalid")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 30:
            raise ValueError("projection presence lease is invalid")
        self.generation = generation
        self._persistence = persistence
        self._processing = processing
        self._seconds = lease_seconds
        self._clock = clock
        self._lease: ProjectionLease | None = None
        self._valid_until = 0.0
        self._unavailable = True
        self._closed = False
        self._busy = False

    @property
    def lease(self) -> ProjectionLease | None:
        return self._lease

    @property
    def worker_health(self) -> WorkerHealth:
        if self._closed:
            return WorkerHealth(WorkerState.STOPPING, "presence_closed")
        remaining = self._valid_until - self._clock()
        if (
            self._unavailable
            or self._lease is None
            or not math.isfinite(remaining)
            or not 0 < remaining <= self._seconds
        ):
            return WorkerHealth(WorkerState.DEGRADED, "presence_unavailable")
        health = self._processing.worker_health
        if health.state is not WorkerState.READY:
            return WorkerHealth(WorkerState.DEGRADED, "processing_unready")
        return health

    async def start(self, *, expires_at: float) -> None:
        _deadline(expires_at)
        if self._closed or self._lease is not None:
            raise RuntimeError("projection presence cannot start twice or after close")
        self._enter()
        try:
            async with asyncio.timeout_at(expires_at):
                await self._persistence.initialize(
                    generation=self.generation, expires_at=expires_at
                )
                await self._acquire(expires_at=expires_at)
        finally:
            self._busy = False

    async def observe(self, *, expires_at: float) -> None:
        _deadline(expires_at)
        if self._closed:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DATABASE_UNAVAILABLE)
        self._enter()
        try:
            async with asyncio.timeout_at(expires_at):
                await self._publish(expires_at=expires_at)
        except BaseException:
            self._unavailable = True
            raise
        finally:
            self._busy = False

    async def _publish(self, *, expires_at: float) -> None:
        observed = _observed(self._clock)
        if self._lease is None or observed >= self._valid_until:
            await self._acquire(expires_at=expires_at)
        lease = self._lease
        if lease is None:
            raise invalid_result()
        ready = self._processing.worker_health.state is WorkerState.READY
        accepted = await self._persistence.publish(
            lease, ready=ready, lease_seconds=self._seconds, expires_at=expires_at
        )
        if type(accepted) is not bool or not accepted:
            raise invalid_result()
        _deadline(expires_at)
        if self._closed:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DATABASE_UNAVAILABLE)
        self._valid_until = observed + self._seconds
        self._unavailable = not ready
        if not ready:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DATABASE_UNAVAILABLE)

    async def close(self, *, expires_at: float) -> None:
        _deadline(expires_at)
        self._closed = True
        self._unavailable = True
        if self._lease is not None:
            async with asyncio.timeout_at(expires_at):
                released = await self._persistence.release(self._lease, expires_at=expires_at)
            if type(released) is not bool:
                raise invalid_result()

    async def _acquire(self, *, expires_at: float) -> None:
        token = uuid4()
        observed = _observed(self._clock)
        lease = await self._persistence.acquire(
            generation=self.generation,
            owner_token=token,
            lease_seconds=self._seconds,
            expires_at=expires_at,
        )
        if type(lease) is not ProjectionLease:
            raise invalid_result()
        try:
            frozen = ProjectionLease(
                generation=lease.generation, slot=lease.slot, owner_token=lease.owner_token
            )
        except ValueError:
            raise invalid_result() from None
        if frozen.generation != self.generation or frozen.owner_token != token:
            raise invalid_result()
        _deadline(expires_at)
        if self._closed:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DATABASE_UNAVAILABLE)
        self._lease = frozen
        self._valid_until = observed + self._seconds

    def _enter(self) -> None:
        if self._busy:
            raise CapacityGateFull("projection presence already has an owner")
        self._busy = True


def _deadline(expires_at: float) -> None:
    if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
        raise ValueError("projection presence deadline is invalid")
    if expires_at <= asyncio.get_running_loop().time():
        raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)


def _observed(clock: Callable[[], float]) -> float:
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("projection presence clock is invalid")
    return value
