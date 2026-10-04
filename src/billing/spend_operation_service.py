"""One request-operation adapter of the existing spend ingestion lifecycle."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.billing.spend_operations import OperationHandle, SpendPersistenceUnavailable
from src.db.spend_operations import SpendOperationRepository
from src.metrics.spend_ingestion import (
    increment_spend_ingestion_failure,
    set_spend_ingestion_backlog,
    set_spend_ingestion_capacity_utilization,
)
from src.metrics.spend_operations import record_operation_transition, observe_unknown_operations

if TYPE_CHECKING:
    from prisma import Prisma


class SpendOperationService:
    def __init__(self, *, admission: Prisma, settlement: Prisma, worker: Prisma) -> None:
        self.admission = SpendOperationRepository(admission)
        self.settlement = SpendOperationRepository(settlement)
        self.worker = SpendOperationRepository(worker)
        self._next_observation = 0.0

    async def initialize(self) -> None:
        async with asyncio.timeout(0.25):
            await self.worker.verify_schema()

    async def begin(
        self, handle: OperationHandle, *, capacity: int, max_attempts: int, expires_at: float
    ) -> None:
        try:
            pending = await self.admission.begin(
                handle, capacity=capacity, max_attempts=max_attempts, expires_at=expires_at
            )
            set_spend_ingestion_backlog(pending)
            set_spend_ingestion_capacity_utilization(pending=pending, capacity=capacity)
            record_operation_transition("dispatched")
        except Exception:
            increment_spend_ingestion_failure("operation_admission")
            raise SpendPersistenceUnavailable() from None

    async def accept(self, handle: OperationHandle, payload: dict[str, object]) -> None:
        # Admission capacity/global locks are absent from this reserved-slot write.
        frozen = dict(payload)
        metadata = dict(frozen.get("metadata") or {})
        metadata["operation_intent"] = handle.intent.model_dump(mode="json")
        metadata["operation_outcome"] = "receipt_accepted"
        # A usable final response cannot prove earlier failed attempts were free.
        metadata["unresolved_prior_attempts"] = max(0, len(handle.intent.attempts) - 1)
        frozen["metadata"] = metadata
        try:
            await self.settlement.accept(
                event_id=str(handle.event_id),
                owner_token=str(handle.owner_token),
                payload=frozen,
                expires_at=asyncio.get_running_loop().time() + 0.25,
            )
            record_operation_transition("accepted")
        except Exception:
            increment_spend_ingestion_failure("operation_receipt")
            raise SpendPersistenceUnavailable() from None

    async def unknown(self, handle: OperationHandle) -> None:
        try:
            changed = await self.settlement.mark_unknown(
                event_id=str(handle.event_id),
                owner_token=str(handle.owner_token),
                expires_at=asyncio.get_running_loop().time() + 0.25,
            )
            record_operation_transition("unknown", int(changed))
        except Exception:
            increment_spend_ingestion_failure("operation_unknown")
            raise SpendPersistenceUnavailable() from None

    async def recover(self) -> None:
        try:
            async with asyncio.timeout(0.25):
                recovered = await self.worker.recover_expired()
                record_operation_transition("unknown", recovered)
                now = asyncio.get_running_loop().time()
                if now >= self._next_observation:
                    self._next_observation = now + 10.0
                    observe_unknown_operations(await self.worker.unknown_count())
        except Exception:
            # The previously committed intent is the recovery record. Do not
            # starve ordinary receipt consumption when this bounded slice fails.
            increment_spend_ingestion_failure("operation_recovery")
