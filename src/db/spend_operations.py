"""Reserved spend slots and owner-fenced receipt replacement in the existing outbox."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from src.billing.spend_operations import OperationHandle, SpendPersistenceUnavailable
from src.db.billing_operations import BillingOperationRepository
from src.db.spend_ingestion import SpendIngestionRepository

if TYPE_CHECKING:
    from prisma import Prisma


class SpendOperationRepository:
    def __init__(self, db: Prisma) -> None:
        self.db = db
        self.transactions = BillingOperationRepository(db)

    async def verify_schema(self) -> None:
        await self.db.query_raw(
            "SELECT operation_owner,operation_state,operation_intent,operation_expires_at "
            "FROM deltallm_spend_ingestion_outbox LIMIT 0"
        )

    async def begin(
        self, handle: OperationHandle, *, capacity: int, max_attempts: int, expires_at: float
    ) -> int:
        """Reserve or append one dispatch using a fresh post-admission-lock snapshot.

        An exact repeated dispatch is rejected, including ambiguous prior commits.
        The durable event ID prevents redispatch after transient outbox retention.
        """
        intent = handle.intent.model_dump_json()
        if len(intent.encode()) > 65_536:
            raise SpendPersistenceUnavailable()
        previous = handle.intent.model_dump(mode="json")
        previous["attempts"] = previous["attempts"][:-1]
        async with self.transactions._transaction(expires_at) as tx:
            # Keep this lock-only statement separate: the mutation must read a
            # new PostgreSQL snapshot after an earlier admission waiter commits.
            await SpendIngestionRepository(tx)._lock_enqueue_admission()
            rows = await tx.query_raw(
                _BEGIN_OPERATION,
                str(handle.event_id),
                str(handle.owner_token),
                intent,
                handle.expires_at.isoformat(),
                max_attempts,
                capacity,
                json.dumps(previous),
                len(handle.intent.attempts),
            )
            if not rows or not rows[0]["accepted"]:
                raise SpendPersistenceUnavailable()
            return int(rows[0]["pending_count"])

    async def accept(
        self,
        *,
        event_id: str,
        owner_token: str,
        payload: dict[str, object],
        expires_at: float,
    ) -> None:
        encoded = json.dumps(payload, default=str, allow_nan=False, sort_keys=True)
        if len(encoded.encode()) > 262_144:
            raise SpendPersistenceUnavailable()
        async with self.transactions._transaction(expires_at) as tx:
            rows = await tx.query_raw(
                "UPDATE deltallm_spend_ingestion_outbox SET "
                "payload_json=$3::jsonb,operation_state='accepted',"
                "status=CASE WHEN operation_state='accepted' THEN status ELSE 'queued' END,"
                "blocked_at=CASE WHEN operation_state='accepted' THEN blocked_at ELSE NULL END,"
                "last_error=CASE WHEN operation_state='accepted' THEN last_error ELSE NULL END,"
                "updated_at=NOW() "
                "WHERE event_id=$1 AND operation_owner=$2 "
                "AND (operation_state IN ('dispatched','unknown') OR "
                "(operation_state='accepted' AND payload_json=$3::jsonb)) "
                "AND operation_intent->'principal' = $4::jsonb "
                "AND operation_intent->>'model'=$5 AND operation_intent->>'call_type'=$6 "
                "RETURNING event_id",
                event_id,
                owner_token,
                encoded,
                json.dumps(
                    {
                        name: payload.get(name)
                        for name in (
                            "api_key",
                            "user_id",
                            "team_id",
                            "organization_id",
                            "owner_account_id",
                        )
                    }
                ),
                payload.get("model"),
                payload.get("call_type"),
            )
            if not rows:
                raise SpendPersistenceUnavailable()

    async def mark_unknown(self, *, event_id: str, owner_token: str, expires_at: float) -> bool:
        async with self.transactions._transaction(expires_at) as tx:
            rows = await tx.query_raw(
                "UPDATE deltallm_spend_ingestion_outbox SET operation_state='unknown',"
                "last_error='operation_outcome_unknown',updated_at=NOW() "
                "WHERE event_id=$1 AND operation_owner=$2 AND operation_state='dispatched' RETURNING event_id",
                event_id,
                owner_token,
            )
            return bool(rows)

    async def recover_expired(self, *, limit: int = 100) -> int:
        rows = await self.db.query_raw(
            "WITH candidates AS MATERIALIZED (SELECT ctid FROM deltallm_spend_ingestion_outbox "
            "WHERE operation_state='dispatched' AND operation_expires_at<=NOW() "
            "ORDER BY operation_expires_at,event_id FOR UPDATE SKIP LOCKED LIMIT $1) "
            "UPDATE deltallm_spend_ingestion_outbox o SET operation_state='unknown',"
            "last_error='operation_outcome_unknown',updated_at=NOW() "
            "WHERE o.ctid=ANY(ARRAY(SELECT ctid FROM candidates)) RETURNING o.event_id",
            max(1, min(limit, 100)),
        )
        return len(rows)

    async def unknown_count(self) -> int:
        # The existing blocked-record partial index bounds work by queue capacity,
        # not retained completed-event history. Background observation only.
        rows = await self.db.query_raw(
            "SELECT count(*)::bigint AS count FROM deltallm_spend_ingestion_outbox "
            "WHERE status='blocked' AND operation_state='unknown'"
        )
        return int(rows[0]["count"])


_BEGIN_OPERATION = """
WITH existing AS MATERIALIZED (
    SELECT event_id FROM deltallm_spend_ingestion_outbox WHERE event_id=$1
), settled AS MATERIALIZED (
    SELECT id FROM deltallm_spendlog_events WHERE id=$1
), capacity AS MATERIALIZED (
    SELECT pending_count FROM deltallm_telemetry_ingestion_capacity WHERE queue_name='spend'
), inserted AS (
    INSERT INTO deltallm_spend_ingestion_outbox (
        event_id,event_type,payload_json,max_attempts,status,blocked_at,last_error,
        operation_owner,operation_intent,operation_state,operation_expires_at
    )
    SELECT $1,'spend',($3::jsonb->'principal') || jsonb_build_object(
        'model',$3::jsonb->>'model','call_type',$3::jsonb->>'call_type'
    ),$5,'blocked',NOW(),'operation_receipt_pending',$2,$3::jsonb,'dispatched',$4::timestamptz
    FROM capacity WHERE pending_count<$6 AND $8::int=1
        AND NOT EXISTS (SELECT 1 FROM existing) AND NOT EXISTS (SELECT 1 FROM settled)
    ON CONFLICT (event_id) DO NOTHING RETURNING event_id
), advanced AS (
    UPDATE deltallm_spend_ingestion_outbox SET operation_intent=$3::jsonb,updated_at=NOW()
    WHERE event_id=$1 AND operation_owner=$2 AND operation_state='dispatched'
        AND operation_intent=$7::jsonb AND $8::int>1
        AND NOT EXISTS (SELECT 1 FROM settled)
    RETURNING event_id
), bumped AS (
    UPDATE deltallm_telemetry_ingestion_capacity SET pending_count=pending_count+1,updated_at=NOW()
    WHERE queue_name='spend' AND EXISTS (SELECT 1 FROM inserted) RETURNING pending_count
)
SELECT (EXISTS (SELECT 1 FROM inserted) OR EXISTS (SELECT 1 FROM advanced)) AS accepted,
    COALESCE((SELECT pending_count FROM bumped),(SELECT pending_count FROM capacity)) AS pending_count
"""
