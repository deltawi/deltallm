from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import json
import logging
from typing import TYPE_CHECKING, Literal

from src.billing.operation_reservation import BillingOperationUnavailable
from src.db.billing_operations import BillingOperationRepository
from src.db.spend_ingestion import SpendIngestionRepository
from src.metrics.spend_ingestion import increment_spend_ingestion_failure

if TYPE_CHECKING:
    from prisma import Prisma


logger = logging.getLogger(__name__)


class RecoveryOutcome(StrEnum):
    RECOVERED = "recovered"
    RECEIPT_CONFLICT = "receipt_conflict"
    INTEGRITY_FAILURE = "integrity_failure"


@dataclass(frozen=True, slots=True)
class RecoveryAttempt:
    operation_id: str | None
    unavailable: bool = False


class BillingOperationRecovery:
    """Bounded recovery slice driven only by the existing spend worker.

    A retained receipt is forwarded through the canonical spend outbox. Missing
    receipts become pending reconciliation; recovery never calls a provider.
    """

    def __init__(
        self, operations: BillingOperationRepository, *, max_pending_events: int, max_attempts: int
    ) -> None:
        self.operations = operations
        self.max_pending_events = max_pending_events
        self.max_attempts = max_attempts

    async def recover(self) -> int:
        # Prioritize one known receipt, then rotate one other open operation. Each
        # owns a short transaction: a slow final row cannot roll back a whole batch.
        receipt = await self._recover_one("receipts")
        opened = await self._recover_one("open", skip_id=receipt.operation_id)
        if receipt.unavailable or opened.unavailable:
            # Report after both independent lanes; the spend worker still drains
            # its outbox. Cancellation is never converted into a lane failure.
            raise BillingOperationUnavailable()
        return int(receipt.operation_id is not None) + int(opened.operation_id is not None)

    async def _recover_one(
        self, lane: Literal["receipts", "open"], *, skip_id: str | None = None
    ) -> RecoveryAttempt:
        deadline = asyncio.get_running_loop().time() + 0.25
        predicate = "selector_state='accepted'" if lane == "receipts" else "closed_at IS NULL"
        operation_id = None
        try:
            async with self.operations._transaction(deadline) as tx:
                rows = await tx.query_raw(
                    "SELECT operation_id,selector_event_id,selector_state,selector_receipt "
                    f"FROM deltallm_billing_operations WHERE {predicate} "
                    "AND recovery_blocked_at IS NULL "
                    "AND ($1::text IS NULL OR operation_id<>$1) "
                    "ORDER BY updated_at,operation_id FOR UPDATE SKIP LOCKED LIMIT 1",
                    skip_id,
                )
                if not rows:
                    return RecoveryAttempt(None)
                row = rows[0]
                operation_id = str(row["operation_id"])
                # Account and operation-capacity locks precede outbox admission.
                results = await tx.query_raw(
                    "SELECT deltallm_recover_operation_isolated($1) AS outcome", operation_id
                )
                outcome = RecoveryOutcome(str(results[0]["outcome"]))
                if outcome is RecoveryOutcome.RECOVERED and row["selector_state"] == "accepted":
                    receipt = row["selector_receipt"]
                    payload = json.loads(receipt) if isinstance(receipt, str) else receipt
                    # Full outbox: retain the authoritative receipt for a later slice.
                    await SpendIngestionRepository(tx).enqueue(
                        event_id=str(row["selector_event_id"]),
                        event_type="spend",
                        payload=payload,
                        max_attempts=self.max_attempts,
                        max_pending_events=self.max_pending_events,
                    )
        except BillingOperationUnavailable:
            return RecoveryAttempt(operation_id, unavailable=True)
        if outcome is not RecoveryOutcome.RECOVERED:
            # A committed quarantine is visible without leaking receipt/tenant data.
            increment_spend_ingestion_failure(f"operation_recovery_{outcome.value}")
            logger.warning("billing_operation_recovery_blocked", extra={"cause": outcome.value})
        return RecoveryAttempt(operation_id)

    @staticmethod
    async def lock_for_events(tx: Prisma, event_ids: list[str]) -> None:
        if len(event_ids) > 1000:
            raise ValueError("billing settlement batch must be bounded")
        await tx.query_raw(
            "SELECT operation_id FROM deltallm_billing_operations "
            "WHERE operation_id=ANY($1::text[]) OR selector_event_id=ANY($1::text[]) "
            "ORDER BY operation_id FOR UPDATE",
            event_ids,
        )

    @staticmethod
    async def settle_events(tx: Prisma, event_ids: list[str]) -> None:
        await tx.query_raw(
            "SELECT deltallm_recover_operation(operation_id)::text FROM deltallm_billing_operations "
            "WHERE operation_id=ANY($1::text[]) OR selector_event_id=ANY($1::text[]) "
            "ORDER BY operation_id",
            event_ids,
        )
