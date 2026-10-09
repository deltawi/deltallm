"""Fund at most two bulk rounds, then issue one complete local batch."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol
from uuid import uuid4

from src.billing.accounting.permits.accounting_local_admission import LocalAdmissionOwner
from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_funding import (
    LocalFundingDemand,
    freeze_local_reservations,
    local_operation_order,
    preflight_local_capacity,
    prepare_local_demands,
    select_local_prefixes,
)
from src.billing.accounting.permits.accounting_local_issue import LocalIssueCommit, LocalIssuedBatch
from src.billing.accounting.permits.accounting_local_leases import LocalPermitGrant
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.accounting_protocol import (
    AccountingReservation,
    PreissuedPermitAllocation,
    ReserveDecision,
)
from src.billing.accounting.durable_microbatch import DurableBatchClosed, DurableBatchFull
from src.db.accounting_permit_results import invalid_result
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.telemetry_acceptance import AcceptanceFailure
from src.telemetry.lifecycle import WorkerHealthSource, WorkerState


class LocalFundingPersistence(Protocol):
    async def allocate_batch(
        self, allocations: Sequence[PreissuedPermitAllocation], *, expires_at: float
    ) -> Sequence[LocalPermitGrant | ReserveDecision]: ...


class LocalPermitIssuer:
    """Inactive until terminal proof, replay, and return owners are integrated."""

    def __init__(
        self,
        persistence: LocalFundingPersistence,
        cursors: LocalCursorStore,
        receipts: LocalReceiptStore,
        *,
        target_operations: int = 32,
        minimum_validity_seconds: float = 0.1,
        admission_health: WorkerHealthSource | None = None,
    ) -> None:
        if type(target_operations) is not int or not 1 <= target_operations <= 1024:
            raise ValueError("local funding target must be between 1 and 1024")
        if cursors.staged_grants:
            raise ValueError("local admission cannot take another owner's staged grants")
        self._persistence = persistence
        self._cursors = cursors
        self._receipts = receipts
        self._target = target_operations
        self._minimum_validity = minimum_validity_seconds
        self._gate = LocalAdmissionOwner()
        self._issue = LocalIssueCommit(
            cursors, receipts, minimum_validity_seconds=minimum_validity_seconds
        )
        self._closed = False
        self._admission_health = admission_health

    @property
    def admission(self) -> LocalAdmissionOwner:
        return self._gate

    @property
    def generation(self) -> int:
        return self._cursors.generation

    @property
    def receipt_store(self) -> LocalReceiptStore:
        return self._receipts

    def owns_cursors(self, cursors: LocalCursorStore) -> bool:
        return self._cursors is cursors

    def stop_admission(self) -> None:
        self._closed = True

    @property
    def admission_ready(self) -> bool:
        return not self._closed and (
            self._admission_health is None
            or self._admission_health.worker_health.state is WorkerState.READY
        )

    async def reserve_batch(
        self, values: Sequence[AccountingReservation], *, expires_at: float
    ) -> LocalIssuedBatch:
        self._check_open()
        items = freeze_local_reservations(values, generation=self._cursors.generation)
        await self._gate.acquire(expires_at=expires_at)
        try:
            self._check_open()
            demands, replays = prepare_local_demands(
                items,
                self._cursors,
                self._receipts,
                now=asyncio.get_running_loop().time(),
                minimum_validity_seconds=self._minimum_validity,
            )
            groups = tuple(demands.values())
            preflight_local_capacity(groups, self._cursors, self._receipts)
            for _ in range(2):
                needed = [demand for demand in groups if demand.needed]
                if not needed:
                    break
                await self._fund(needed, expires_at=expires_at)
            proposed, denied = select_local_prefixes(groups)
            self._check_open()
            return self._issue.commit(
                proposed,
                expires_at=expires_at,
                non_dispatch=replays + denied,
                operation_order=local_operation_order(items),
            )
        except BaseException:
            # No warm prefix was issued before the last await. Only received,
            # fenced funding proofs move to return-only state at ordinal zero.
            self._cursors.abort_staging()
            raise
        finally:
            self._gate.release()

    async def _fund(self, demands: Sequence[LocalFundingDemand], *, expires_at: float) -> None:
        allocations = tuple(
            PreissuedPermitAllocation(
                reservation=demand.items[0],
                fence_token=uuid4(),
                target_operations=max(demand.needed, self._target if len(demands) == 1 else 1),
            )
            for demand in demands
        )
        results = await self._persistence.allocate_batch(allocations, expires_at=expires_at)
        if len(results) != len(allocations):
            raise invalid_result()
        for demand, allocation, result in zip(demands, allocations, results, strict=True):
            if isinstance(result, LocalPermitGrant):
                if (
                    result.fence_token != allocation.fence_token
                    or result.operation_limit > allocation.target_operations
                ):
                    raise invalid_result()
                if not self._cursors.stage(demand.subject, result):
                    raise DurableBatchFull("local funded cursor capacity is full")
                demand.funded.append(result)
            elif isinstance(result, ReserveDecision) and result in {
                ReserveDecision.BUDGET_EXHAUSTED,
                ReserveDecision.CAPACITY_EXHAUSTED,
            }:
                demand.denial = result
            else:
                raise invalid_result()

    def _check_open(self) -> None:
        if self._closed:
            raise DurableBatchClosed("local admission is closed")
        if not self.admission_ready:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DATABASE_UNAVAILABLE)
