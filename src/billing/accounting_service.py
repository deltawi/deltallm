"""Lifecycle owner for reservation and finalization microbatches."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from time import perf_counter

from src.billing.accounting_protocol import (
    AccountingFinalization,
    AccountingOperationHandle,
    AccountingReservation,
    DispatchPermit,
    FinalizationReceipt,
)
from src.billing.accounting_local_leases import LocalAccountingHandle
from src.billing.durable_microbatch import DurableBatchClosed, DurableBatchFull, DurableMicrobatcher
from src.billing.accounting_snapshots import finalization_bytes, reservation_bytes
from src.db.accounting_protocol import AccountingProtocolRepository, AccountingProtocolUnavailable
from src.metrics.accounting import (
    increment_accounting_failure,
    increment_accounting_decision,
    observe_accounting_batch,
    observe_accounting_queue_wait,
    set_accounting_queue_depth,
    set_accounting_queue_retained_bytes,
)
from src.telemetry.lifecycle import WorkerHealth, WorkerState, task_failure_detail


class AccountingProtocolService:
    """Separate queues reserve finalization capacity during admission bursts."""

    def __init__(
        self,
        repository: AccountingProtocolRepository,
        *,
        generation: int,
        max_batch_size: int = 8,
        dwell_seconds: float = 0.002,
        max_pending_reservations: int = 4096,
        max_pending_finalizations: int = 8192,
        max_reservation_retained_bytes: int = 8 * 1024 * 1024,
        max_finalization_retained_bytes: int = 8 * 1024 * 1024,
        statement_budget_seconds: float = 0.25,
        reservation_ack_budget_seconds: float = 1.0,
        finalization_ack_budget_seconds: float = 2.0,
    ) -> None:
        if reservation_ack_budget_seconds < 2 * statement_budget_seconds:
            raise ValueError(
                "reservation ACK budget must fit one statement attempt and one recovery query"
            )
        if finalization_ack_budget_seconds < 2 * statement_budget_seconds:
            raise ValueError(
                "finalization ACK budget must fit one statement attempt and one recovery query"
            )
        self._repository = repository
        self.generation = generation
        self._statement_budget_seconds = statement_budget_seconds
        self._reservation_ack_budget_seconds = reservation_ack_budget_seconds
        self._finalization_ack_budget_seconds = finalization_ack_budget_seconds
        self.reservations = DurableMicrobatcher(
            self._reserve,
            max_batch_size=max_batch_size,
            max_pending=max_pending_reservations,
            dwell_seconds=dwell_seconds,
            name="accounting-reservations",
            observe_queue_wait=lambda seconds: observe_accounting_queue_wait(
                "reservation", seconds
            ),
            set_queue_depth=lambda depth: set_accounting_queue_depth("reservation", depth),
            payload_size=len,
            max_batch_bytes=1_048_576,
            max_retained_bytes=max_reservation_retained_bytes,
            set_retained_bytes=lambda size: set_accounting_queue_retained_bytes(
                "reservation", size
            ),
        )
        self.finalizations = DurableMicrobatcher(
            self._finalize,
            max_batch_size=max_batch_size,
            max_pending=max_pending_finalizations,
            dwell_seconds=dwell_seconds,
            name="accounting-finalizations",
            observe_queue_wait=lambda seconds: observe_accounting_queue_wait(
                "finalization", seconds
            ),
            set_queue_depth=lambda depth: set_accounting_queue_depth("finalization", depth),
            payload_size=len,
            max_batch_bytes=1_048_576,
            max_retained_bytes=max_finalization_retained_bytes,
            set_retained_bytes=lambda size: set_accounting_queue_retained_bytes(
                "finalization", size
            ),
        )

    def start(self) -> tuple[asyncio.Task[None], asyncio.Task[None]]:
        return self.reservations.start(), self.finalizations.start()

    @property
    def requires_local_proof(self) -> bool:
        return False

    @property
    def worker_health(self) -> WorkerHealth:
        for name, batcher in (
            ("reservation", self.reservations),
            ("finalization", self.finalizations),
        ):
            detail = task_failure_detail(batcher.task)
            if detail is not None:
                return WorkerHealth(WorkerState.FAILED, f"{name} microbatch: {detail}")
            if batcher.task is None:
                return WorkerHealth(WorkerState.FAILED, f"{name} microbatch is missing")
        return WorkerHealth(WorkerState.READY)

    async def close(self, *, timeout_seconds: float = 5.0) -> None:
        # Stop new reservations first. Finalization keeps its independent queue
        # and drains after all already-admitted callers have had a chance to settle.
        await self.reservations.close(timeout_seconds=timeout_seconds)
        await self.finalizations.close(timeout_seconds=timeout_seconds)

    async def readiness_probe(self) -> bool:
        if not self.worker_health.ready:
            return False
        return await self._repository.protocol_ready(self.generation)

    async def reserve(self, reservation: AccountingReservation) -> DispatchPermit:
        if reservation.protocol_generation != self.generation:
            raise ValueError("reservation uses a stale accounting generation")
        try:
            return await self.reservations.submit(reservation_bytes(reservation))
        except DurableBatchFull:
            increment_accounting_failure("reservation", "queue", "queue_full")
            raise
        except DurableBatchClosed:
            increment_accounting_failure("reservation", "queue", "queue_closed")
            raise

    async def finalize(self, finalization: AccountingFinalization) -> FinalizationReceipt:
        if finalization.protocol_generation != self.generation:
            raise ValueError("finalization uses a stale accounting generation")
        try:
            return await self.finalizations.submit(finalization_bytes(finalization))
        except DurableBatchFull:
            increment_accounting_failure("finalization", "queue", "queue_full")
            raise
        except DurableBatchClosed:
            increment_accounting_failure("finalization", "queue", "queue_closed")
            raise

    async def finalize_operation(
        self, operation: AccountingOperationHandle, finalization: AccountingFinalization
    ) -> FinalizationReceipt:
        if isinstance(operation, LocalAccountingHandle):
            raise ValueError("assigned accounting cannot accept a local issue proof")
        return await self.finalize(finalization)

    async def _reserve(self, values: Sequence[bytes]) -> list[DispatchPermit]:
        started = perf_counter()
        try:
            results = await self._repository.reserve_batch(
                [AccountingReservation.model_validate_json(value) for value in values],
                expires_at=(
                    asyncio.get_running_loop().time() + self._reservation_ack_budget_seconds
                ),
            )
        except BaseException as exc:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            observe_accounting_batch("reservation", len(values), perf_counter() - started, outcome)
            _record_batch_failure("reservation", exc)
            raise
        observe_accounting_batch("reservation", len(values), perf_counter() - started, "success")
        for result in results:
            increment_accounting_decision(result.decision.value)
        return results

    async def _finalize(self, values: Sequence[bytes]) -> list[FinalizationReceipt]:
        started = perf_counter()
        try:
            results = await self._repository.finalize_batch(
                [AccountingFinalization.model_validate_json(value) for value in values],
                expires_at=(
                    asyncio.get_running_loop().time() + self._finalization_ack_budget_seconds
                ),
            )
        except BaseException as exc:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            observe_accounting_batch("finalization", len(values), perf_counter() - started, outcome)
            _record_batch_failure("finalization", exc)
            raise
        observe_accounting_batch("finalization", len(values), perf_counter() - started, "success")
        return results


def _record_batch_failure(queue: str, exc: BaseException) -> None:
    if isinstance(exc, asyncio.CancelledError):
        reason = "cancelled"
    elif isinstance(exc, AccountingProtocolUnavailable):
        reason = exc.reason
    else:
        reason = "unknown"
    increment_accounting_failure(queue, "database", reason)
