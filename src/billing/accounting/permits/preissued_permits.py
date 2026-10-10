"""Bounded local ordinals; each dispatch still needs a durable claim ACK."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from src.billing.accounting.accounting_protocol import (
    AccountingReservation,
    BudgetWindowRef,
    DispatchPermit,
    PreissuedPermitAllocation,
    PreissuedPermitClaim,
    PreissuedPermitGrant,
    ReserveDecision,
)
from src.billing.accounting.durable_microbatch import DurableBatchClosed
from src.metrics.accounting import increment_accounting_permit_action, set_accounting_permit_bank


class PermitPersistence(Protocol):
    async def allocate_batch(
        self, allocations: Sequence[PreissuedPermitAllocation], *, expires_at: float
    ) -> Sequence[PreissuedPermitGrant | ReserveDecision]: ...

    async def claim_batch(
        self, claims: Sequence[PreissuedPermitClaim], *, expires_at: float
    ) -> Sequence[DispatchPermit]: ...


@dataclass(frozen=True, slots=True, repr=False)
class PermitSubject:
    """Match the database grant subject, with the protocol generation added."""

    generation: int
    api_key: str
    user_id: str | None
    team_id: str | None
    organization_id: str | None
    model: str
    windows: tuple[BudgetWindowRef, ...]
    allowance: Decimal
    allowance_key: str

    @property
    def retained_bytes(self) -> int:
        # Charge four bytes per character, plus fixed space for the bounded
        # cursor, grant, model dictionaries, numbers, and window objects. This
        # is a conservative retained-state budget, not a process RSS measure.
        values = (
            self.api_key,
            self.user_id,
            self.team_id,
            self.organization_id,
            self.model,
            self.allowance_key,
        )
        return (
            8192
            + 4 * sum(len(value) for value in values if value is not None)
            + sum(2048 + 4 * len(window.scope_id) for window in self.windows)
        )

    @classmethod
    def from_reservation(cls, item: AccountingReservation) -> PermitSubject:
        attribution = item.attribution
        return cls(
            item.protocol_generation,
            attribution.api_key,
            attribution.user_id,
            attribution.team_id,
            attribution.organization_id,
            attribution.model,
            item.windows,
            item.allowance,
            str(item.allowance),
        )


@dataclass(slots=True)
class _GrantCursor:
    grant: PreissuedPermitGrant
    retained_bytes: int
    next_ordinal: int = 0

    @property
    def remaining(self) -> int:
        return self.grant.operation_limit - self.next_ordinal


class PreissuedPermitBank:
    """Batch cold refills; warm batches use one claim call across all subjects."""

    def __init__(
        self,
        repository: PermitPersistence,
        *,
        target_operations: int,
        max_operations: int,
        max_subjects: int,
        max_retained_bytes: int = 8 * 1024 * 1024,
        minimum_validity_seconds: float = 0.1,
        lane: int = 0,
    ) -> None:
        if not 1 <= target_operations <= max_operations <= 1024:
            raise ValueError("permit operation bounds are invalid")
        if not 1 <= max_subjects <= 100_000:
            raise ValueError("permit subject capacity must be between 1 and 100000")
        if not 1 <= max_retained_bytes <= 64 * 1024 * 1024:
            raise ValueError("permit byte capacity must be between 1 and 67108864")
        if not 0 <= minimum_validity_seconds <= 5:
            raise ValueError("permit minimum validity must be between 0 and 5 seconds")
        if not 0 <= lane <= 63:
            raise ValueError("permit lane must be between 0 and 63")
        self._repository = repository
        self._target_operations = target_operations
        self._max_operations = max_operations
        self._max_subjects = max_subjects
        self._max_retained_bytes = max_retained_bytes
        self._minimum_validity = timedelta(seconds=minimum_validity_seconds)
        self._lane = lane
        self._cursors: OrderedDict[PermitSubject, _GrantCursor] = OrderedDict()
        self._available = 0
        self._retained_bytes = 0
        self._lock = asyncio.Lock()
        self._closed = False
        self._refresh_metrics()

    @property
    def active_subjects(self) -> int:
        return len(self._cursors)

    @property
    def available_permits(self) -> int:
        return self._available

    @property
    def retained_bytes(self) -> int:
        return self._retained_bytes

    async def reserve_batch(
        self, reservations: Sequence[AccountingReservation], *, expires_at: float
    ) -> list[DispatchPermit]:
        if not reservations:
            return []
        if len(reservations) > min(256, self._max_operations):
            raise ValueError("permit batch must fit one maximum grant")
        if len({item.operation_id for item in reservations}) != len(reservations):
            raise ValueError("one permit batch cannot repeat an operation")
        if len({item.protocol_generation for item in reservations}) != 1:
            raise ValueError("one permit batch cannot mix protocol generations")
        subjects: dict[PermitSubject, list[AccountingReservation]] = {}
        for item in reservations:
            subjects.setdefault(PermitSubject.from_reservation(item), []).append(item)
        async with self._lock:
            if self._closed:
                raise DurableBatchClosed("pre-issued permit bank is closed")
            pending = {subject: list(items) for subject, items in subjects.items()}
            results: dict[UUID, DispatchPermit] = {}
            claims: list[PreissuedPermitClaim] = []
            try:
                self._collect(pending, claims)
                # One extra refill handles a partial budget or partition grant.
                # This bound does not grow with subject count or returned capacity.
                for _ in range(2):
                    if not pending:
                        break
                    await self._refill(pending, results, expires_at=expires_at)
                    if self._closed:
                        raise DurableBatchClosed("pre-issued permit bank is closed")
                    self._collect(pending, claims)
                for items in pending.values():
                    _deny(items, ReserveDecision.CAPACITY_EXHAUSTED, results)
                if claims:
                    permits = await self._repository.claim_batch(claims, expires_at=expires_at)
                    _merge_claims(claims, permits, results)
                    increment_accounting_permit_action("claim", "success", count=len(claims))
            except BaseException as exc:
                # An uncertain ACK never permits reuse of an ordinal. The worker
                # recovers durable claims and releases only proven unused slots.
                for subject in subjects:
                    self._discard(subject)
                increment_accounting_permit_action(
                    "reserve", "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                )
                raise
            finally:
                self._refresh_metrics()
            return [results[item.operation_id] for item in reservations]

    async def close(self) -> None:
        self._closed = True
        async with self._lock:
            self._cursors.clear()
            self._available = 0
            self._retained_bytes = 0
            self._refresh_metrics()

    def _collect(
        self,
        pending: dict[PermitSubject, list[AccountingReservation]],
        claims: list[PreissuedPermitClaim],
    ) -> None:
        for subject, items in tuple(pending.items()):
            cursor = self._cursors.get(subject)
            if cursor is None:
                continue
            if not self._usable(cursor):
                self._discard(subject)
                continue
            count = min(cursor.remaining, len(items))
            claims.extend(
                PreissuedPermitClaim(
                    grant=cursor.grant,
                    permit_ordinal=cursor.next_ordinal + offset,
                    reservation=item,
                )
                for offset, item in enumerate(items[:count])
            )
            cursor.next_ordinal += count
            self._available -= count
            if count == len(items):
                del pending[subject]
            else:
                pending[subject] = items[count:]
            if cursor.remaining == 0:
                self._discard(subject)

    async def _refill(
        self,
        pending: dict[PermitSubject, list[AccountingReservation]],
        results: dict[UUID, DispatchPermit],
        *,
        expires_at: float,
    ) -> None:
        subjects = self._refill_subjects(pending, results)
        if not subjects:
            return
        allocations = [
            PreissuedPermitAllocation(
                reservation=pending[subject][0],
                fence_token=uuid4(),
                target_operations=min(
                    self._max_operations, self._target_operations * len(pending[subject])
                ),
            )
            for subject in subjects
        ]
        grants = await self._repository.allocate_batch(allocations, expires_at=expires_at)
        if len(grants) != len(subjects):
            raise ValueError("permit refill returned an incomplete batch")
        for subject, allocation, grant in zip(subjects, allocations, grants, strict=True):
            if isinstance(grant, ReserveDecision):
                if grant not in {
                    ReserveDecision.BUDGET_EXHAUSTED,
                    ReserveDecision.CAPACITY_EXHAUSTED,
                }:
                    raise ValueError("permit refill returned an invalid decision")
                _deny(pending.pop(subject), grant, results)
                continue
            if (
                grant.fence_token != allocation.fence_token
                or grant.protocol_generation != subject.generation
                or grant.allowance != subject.allowance
                or grant.operation_limit > allocation.target_operations
            ):
                raise ValueError("permit refill returned a different contract")
            cursor = _GrantCursor(grant, subject.retained_bytes)
            if not self._usable(cursor):
                _deny(pending.pop(subject), ReserveDecision.CAPACITY_EXHAUSTED, results)
                continue
            self._cursors[subject] = cursor
            self._available += cursor.remaining
            self._retained_bytes += cursor.retained_bytes
        increment_accounting_permit_action("refill", "success", count=len(allocations))

    def _refill_subjects(
        self,
        pending: dict[PermitSubject, list[AccountingReservation]],
        results: dict[UUID, DispatchPermit],
    ) -> list[PermitSubject]:
        requested_bytes = sum(subject.retained_bytes for subject in pending)
        if (
            len(self._cursors) + len(pending) > self._max_subjects
            or self._retained_bytes + requested_bytes > self._max_retained_bytes
        ):
            self._prune_expired()
        available_entries = self._max_subjects - len(self._cursors)
        available_bytes = self._max_retained_bytes - self._retained_bytes
        subjects = []
        for subject in tuple(pending):
            size = subject.retained_bytes
            if available_entries <= 0 or size > available_bytes:
                _deny(pending.pop(subject), ReserveDecision.CAPACITY_EXHAUSTED, results)
                continue
            subjects.append(subject)
            available_entries -= 1
            available_bytes -= size
        return subjects

    def _usable(self, cursor: _GrantCursor) -> bool:
        return (
            cursor.remaining > 0
            and cursor.grant.expires_at > datetime.now(UTC) + self._minimum_validity
        )

    def _discard(self, subject: PermitSubject) -> None:
        cursor = self._cursors.pop(subject, None)
        if cursor is not None:
            self._available -= cursor.remaining
            self._retained_bytes -= cursor.retained_bytes
            increment_accounting_permit_action("retire", "success")

    def _prune_expired(self) -> None:
        # Rotate at most one batch of subjects. A full bank does not scan its
        # entire configured capacity on the request event loop.
        for _ in range(min(256, len(self._cursors))):
            subject = next(iter(self._cursors))
            cursor = self._cursors[subject]
            if not self._usable(cursor):
                self._discard(subject)
            else:
                self._cursors.move_to_end(subject)

    def _refresh_metrics(self) -> None:
        set_accounting_permit_bank(
            self._lane,
            subjects=self.active_subjects,
            available=self.available_permits,
            retained_bytes=self.retained_bytes,
        )


def _deny(
    items: Sequence[AccountingReservation],
    decision: ReserveDecision,
    results: dict[UUID, DispatchPermit],
) -> None:
    increment_accounting_permit_action("reject", decision.value, count=len(items))
    for item in items:
        results[item.operation_id] = DispatchPermit(
            protocol_generation=item.protocol_generation,
            operation_id=item.operation_id,
            decision=decision,
        )


def _merge_claims(
    claims: Sequence[PreissuedPermitClaim],
    permits: Sequence[DispatchPermit],
    results: dict[UUID, DispatchPermit],
) -> None:
    if len(claims) != len(permits):
        raise ValueError("permit claim returned an incomplete batch")
    for claim, permit in zip(claims, permits, strict=True):
        item = claim.reservation
        if (
            permit.operation_id != item.operation_id
            or permit.protocol_generation != item.protocol_generation
            or permit.decision not in {ReserveDecision.DISPATCH, ReserveDecision.REPLAY}
            or (
                permit.decision is ReserveDecision.DISPATCH
                and (
                    permit.dispatch_token != item.owner_token
                    or permit.accounting_partition != claim.grant.accounting_partition
                )
            )
        ):
            raise ValueError("permit claim returned a different identity")
        results[item.operation_id] = permit
