"""Deliver frozen native receipts through the existing batch outbox owner."""

from __future__ import annotations

import asyncio
from typing import Protocol

from src.batch.accounting_checkpoint import BatchAccountingUnavailable
from src.batch.accounting_native import decode_checkpoint
from src.batch.models import BatchCompletionOutboxRecord
from src.batch.selector_identity import batch_selector_operation_id
from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.accounting_service import AccountingProtocolService


class FencedCompletionDeliveryStore(Protocol):
    async def mark_completion_outbox_sent(
        self,
        completion_id: str,
        *,
        worker_id: str,
        attempt_count: int,
    ) -> bool: ...


class NativeBatchCompletionDelivery:
    def __init__(
        self,
        accounting: AccountingProtocolService | None,
        repository: FencedCompletionDeliveryStore,
    ) -> None:
        self.accounting = accounting
        self.repository = repository

    async def deliver(self, record: BatchCompletionOutboxRecord, *, worker_id: str) -> bool:
        checkpoint = decode_checkpoint(record.payload_json.get("native_accounting"))
        accounting = self.accounting
        if (
            checkpoint is None
            or checkpoint.terminal is None
            or accounting is None
            or checkpoint.operation_id
            != batch_selector_operation_id(record.batch_id, record.item_id)
            or str(checkpoint.operation_id) != record.completion_id
            or checkpoint.operation.reservation.protocol_generation != accounting.generation
        ):
            raise BatchAccountingUnavailable()
        async with asyncio.timeout(2):
            receipt = await accounting.finalize_operation(checkpoint.operation, checkpoint.terminal)
        if (
            receipt.operation_id != checkpoint.operation_id
            or receipt.protocol_generation != checkpoint.terminal.protocol_generation
            or receipt.outcome != checkpoint.terminal.outcome
        ):
            raise BatchAccountingUnavailable()
        # The money receipt is accepted before this short primary mutation.
        # No SQL transaction or lock spans the signed accounting call.
        sent = await self.repository.mark_completion_outbox_sent(
            record.completion_id,
            worker_id=worker_id,
            attempt_count=record.attempt_count,
        )
        return sent and checkpoint.terminal.outcome is AccountingOutcome.COMPLETED
