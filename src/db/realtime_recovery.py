from __future__ import annotations

import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from src.db.billing_transaction import billing_transaction
from src.db.spend_ingestion import SpendIngestionRepository

if TYPE_CHECKING:
    from prisma import Prisma


class RealtimeBillingRecovery:
    """A bounded slice of the existing spend worker, never a provider retry."""

    def __init__(self, db: Prisma, *, max_pending_events: int, max_attempts: int) -> None:
        self.db = db
        self.max_pending_events = max_pending_events
        self.max_attempts = max_attempts

    async def recover(self) -> None:
        async with billing_transaction(self.db, asyncio.get_running_loop().time() + 0.25) as tx:
            rows = await tx.query_raw(
                "SELECT operation_id,event_id,spend_payload FROM deltallm_realtime_billing_intents "
                "WHERE state='accepted' ORDER BY updated_at,operation_id FOR UPDATE SKIP LOCKED LIMIT 2"
            )
            for row in rows:
                payload = row["spend_payload"]
                payload = json.loads(payload) if isinstance(payload, str) else payload
                if await self._settle_written(tx, row, payload):
                    continue
                await SpendIngestionRepository(tx).enqueue(
                    event_id=str(row["event_id"]),
                    event_type="spend",
                    payload=payload,
                    max_attempts=self.max_attempts,
                    max_pending_events=self.max_pending_events,
                )
                # Rotate even when outbox admission is full. The receipt remains
                # authoritative until settlement commits in the ledger transaction.
                await tx.execute_raw(
                    "UPDATE deltallm_realtime_billing_intents SET updated_at=NOW() WHERE operation_id=$1",
                    str(row["operation_id"]),
                )
        async with billing_transaction(self.db, asyncio.get_running_loop().time() + 0.25) as tx:
            await tx.execute_raw(
                "WITH expired AS (SELECT operation_id FROM deltallm_realtime_billing_intents "
                "WHERE state='dispatched' AND expires_at<NOW() ORDER BY expires_at,operation_id "
                "FOR UPDATE SKIP LOCKED LIMIT 10) "
                "UPDATE deltallm_realtime_billing_intents i SET state='pending', "
                "pending_reason='owner_expired',updated_at=NOW() FROM expired e WHERE i.operation_id=e.operation_id"
            )
            # Only settled facts expire. Missing usage retains capacity and must
            # be reconciled; it is never guessed to be a zero-cost operation.
            await tx.execute_raw(
                "DELETE FROM deltallm_realtime_billing_intents WHERE operation_id IN "
                "(SELECT operation_id FROM deltallm_realtime_billing_intents WHERE state='settled' "
                "AND updated_at<NOW()-INTERVAL '30 days' ORDER BY updated_at,operation_id "
                "FOR UPDATE SKIP LOCKED LIMIT 10)"
            )

    @staticmethod
    async def _settle_written(tx: Prisma, row: dict, payload: dict) -> bool:
        # During rolling upgrades or on a batch worker, the existing ledger owner
        # may commit a Realtime event without this journal extension installed.
        # Its atomic ledger write is authoritative; do not wait for outbox replay.
        written = await tx.query_raw(
            "SELECT api_key,model,user_id,team_id,organization_id,owner_account_id,request_id,call_type,"
            "spend_exact::text AS cost,provider_cost_exact::text AS provider_cost "
            "FROM deltallm_spendlog_events WHERE id=$1",
            str(row["event_id"]),
        )
        if not written:
            return False
        event = written[0]
        try:
            matches = all(
                event.get(key) == payload.get(key)
                for key in (
                    "api_key",
                    "model",
                    "user_id",
                    "team_id",
                    "organization_id",
                    "owner_account_id",
                    "request_id",
                    "call_type",
                )
            ) and (
                Decimal(event["cost"]) == Decimal(payload["cost_exact"])
                and Decimal(event["provider_cost"]) == Decimal(payload["provider_cost_exact"])
            )
        except (InvalidOperation, TypeError, ValueError):
            matches = False
        if not matches:
            await tx.execute_raw(
                "UPDATE deltallm_realtime_billing_intents SET state='pending', "
                "pending_reason='ledger_conflict',updated_at=NOW() WHERE operation_id=$1",
                str(row["operation_id"]),
            )
            return True
        await RealtimeBillingRecovery.settle_events(tx, [str(row["event_id"])])
        return True

    @staticmethod
    async def lock_for_events(tx: Prisma, event_ids: list[str]) -> None:
        if len(event_ids) > 1000:
            raise ValueError("Realtime settlement batch must be bounded")
        await tx.query_raw(
            "SELECT operation_id FROM deltallm_realtime_billing_intents WHERE event_id=ANY($1::text[]) "
            "ORDER BY operation_id FOR UPDATE",
            event_ids,
        )

    @staticmethod
    async def settle_events(tx: Prisma, event_ids: list[str]) -> None:
        # Called only after the canonical ledger writer in the same transaction.
        await tx.execute_raw(
            "WITH settled AS (UPDATE deltallm_realtime_billing_intents SET state='settled',updated_at=NOW() "
            "WHERE event_id=ANY($1::text[]) AND state='accepted' RETURNING operation_id) "
            "UPDATE deltallm_telemetry_ingestion_capacity SET pending_count=pending_count-(SELECT COUNT(*) FROM settled) "
            "WHERE queue_name='realtime_billing'",
            event_ids,
        )
