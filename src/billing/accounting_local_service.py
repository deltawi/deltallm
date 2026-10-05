"""Keep local issue proofs in the shared, byte-bounded accounting queues."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from time import perf_counter

from src.billing.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting_local_leases import LocalAccountingHandle, LocalPermitFinalization
from src.billing.accounting_local_terminal import LocalTerminalOwner, local_terminal_bytes
from src.billing.accounting_protocol import (
    AccountingFinalization,
    AccountingOperationHandle,
    AccountingReservation,
    DispatchPermit,
)
from src.billing.accounting_service import AccountingProtocolService, _record_batch_failure
from src.billing.accounting_terminal_receipts import TerminalReceipt
from src.billing.durable_microbatch import DurableBatchClosed, DurableBatchFull
from src.db.accounting_protocol import AccountingProtocolRepository
from src.metrics.accounting import (
    increment_accounting_decision,
    increment_accounting_failure,
    observe_accounting_batch,
)


class LocalAccountingService(AccountingProtocolService):
    """Runtime must start the shared return owner before selecting this service."""

    def __init__(
        self,
        repository: AccountingProtocolRepository,
        *,
        generation: int,
        issuer: LocalPermitIssuer,
        terminal: LocalTerminalOwner,
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
        if (
            issuer.generation != generation
            or terminal.generation != generation
            or not terminal.owns_receipts(issuer.receipt_store)
        ):
            raise ValueError("local accounting owners must share generation and issued proofs")
        self._issuer = issuer
        self._terminal = terminal
        super().__init__(
            repository,
            generation=generation,
            max_batch_size=max_batch_size,
            dwell_seconds=dwell_seconds,
            max_pending_reservations=max_pending_reservations,
            max_pending_finalizations=max_pending_finalizations,
            max_reservation_retained_bytes=max_reservation_retained_bytes,
            max_finalization_retained_bytes=max_finalization_retained_bytes,
            statement_budget_seconds=statement_budget_seconds,
            reservation_ack_budget_seconds=reservation_ack_budget_seconds,
            finalization_ack_budget_seconds=finalization_ack_budget_seconds,
        )

    @property
    def requires_local_proof(self) -> bool:
        return True

    async def close(self, *, timeout_seconds: float = 5.0) -> None:
        self._issuer.stop_admission()
        await super().close(timeout_seconds=timeout_seconds)

    async def finalize(self, finalization: AccountingFinalization) -> TerminalReceipt:
        raise ValueError("local accounting requires the complete issue proof")

    async def finalize_operation(
        self, operation: AccountingOperationHandle, finalization: AccountingFinalization
    ) -> TerminalReceipt:
        if not isinstance(operation, LocalAccountingHandle):
            raise ValueError("local accounting requires the complete issue proof")
        value = LocalPermitFinalization(receipt=operation.proof, finalization=finalization)
        encoded = local_terminal_bytes(value, generation=self.generation)
        try:
            return await self.finalizations.submit(encoded)
        except DurableBatchFull:
            increment_accounting_failure("finalization", "queue", "queue_full")
            raise
        except DurableBatchClosed:
            increment_accounting_failure("finalization", "queue", "queue_closed")
            raise

    async def _reserve(self, values: Sequence[bytes]) -> list[DispatchPermit]:
        started = perf_counter()
        try:
            result = await self._issuer.reserve_batch(
                [AccountingReservation.model_validate_json(value) for value in values],
                expires_at=asyncio.get_running_loop().time() + self._reservation_ack_budget_seconds,
            )
        except BaseException as exc:
            self._failed_batch("reservation", len(values), started, exc)
            raise
        observe_accounting_batch("reservation", len(values), perf_counter() - started, "success")
        for permit in result.permits:
            increment_accounting_decision(permit.decision.value)
        return list(result.permits)

    async def _finalize(self, values: Sequence[bytes]) -> list[TerminalReceipt]:
        started = perf_counter()
        try:
            results = await self._terminal.finalize_batch(
                [LocalPermitFinalization.model_validate_json(value) for value in values],
                expires_at=asyncio.get_running_loop().time()
                + self._finalization_ack_budget_seconds,
            )
        except BaseException as exc:
            self._failed_batch("finalization", len(values), started, exc)
            raise
        observe_accounting_batch("finalization", len(values), perf_counter() - started, "success")
        return list(results)

    @staticmethod
    def _failed_batch(phase: str, size: int, started: float, exc: BaseException) -> None:
        outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        observe_accounting_batch(phase, size, perf_counter() - started, outcome)
        _record_batch_failure(phase, exc)
