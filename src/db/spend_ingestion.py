from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from src.db.client import is_prisma_transaction_client


@dataclass(frozen=True, slots=True)
class SpendOutboxRecord:
    event_id: str
    event_type: str
    payload: dict[str, Any]
    attempt_count: int
    max_attempts: int = 10
    claim_token: str = "unfenced-test-claim"


@dataclass(frozen=True, slots=True)
class SpendEnqueueResult:
    status: str
    pending_count: int


class SpendIngestionRepository:
    """Raw PostgreSQL contract for bounded spend ingestion."""

    def __init__(self, prisma_client: Any | None) -> None:
        self.prisma = prisma_client

    def with_db(self, prisma_client: Any | None) -> SpendIngestionRepository:
        return SpendIngestionRepository(prisma_client)

    async def enqueue(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        max_attempts: int,
        max_pending_events: int,
    ) -> SpendEnqueueResult:
        if self.prisma is None:
            raise RuntimeError("spend ingestion database is unavailable")
        async with self._transaction() as tx:
            transactional = self.with_db(tx)
            await transactional._lock_enqueue_admission()
            return await transactional._enqueue_under_lock(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                max_attempts=max_attempts,
                max_pending_events=max_pending_events,
            )

    async def _lock_enqueue_admission(self) -> None:
        if self.prisma is None:
            raise RuntimeError("spend ingestion database is unavailable")
        await self.prisma.query_raw(
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended('deltallm:spend-ingestion-capacity', 0)
            )::text AS locked
            """
        )

    async def _enqueue_under_lock(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        max_attempts: int,
        max_pending_events: int,
    ) -> SpendEnqueueResult:
        if self.prisma is None:
            raise RuntimeError("spend ingestion database is unavailable")
        rows = await self.prisma.query_raw(
            """
            WITH existing AS MATERIALIZED (
                SELECT 1
                FROM deltallm_spend_ingestion_outbox
                WHERE event_id = $1
            ),
            capacity AS MATERIALIZED (
                SELECT pending_count
                FROM deltallm_telemetry_ingestion_capacity
                WHERE queue_name = 'spend'
            ),
            inserted AS (
                INSERT INTO deltallm_spend_ingestion_outbox (
                    event_id, event_type, payload_json, max_attempts
                )
                SELECT $1, $2, $3::jsonb, $4
                FROM capacity
                WHERE pending_count < $5
                  AND NOT EXISTS (SELECT 1 FROM existing)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
            ),
            bumped AS (
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = pending_count + 1,
                    updated_at = NOW()
                WHERE queue_name = 'spend'
                  AND EXISTS (SELECT 1 FROM inserted)
                RETURNING pending_count
            )
            SELECT
                EXISTS (SELECT 1 FROM existing) AS duplicate,
                EXISTS (SELECT 1 FROM inserted) AS accepted,
                COALESCE(
                    (SELECT pending_count FROM bumped),
                    (SELECT pending_count FROM capacity),
                    0
                )::bigint AS pending_count
            """,
            event_id,
            event_type,
            json.dumps(payload, default=str),
            max(1, int(max_attempts)),
            max(1, int(max_pending_events)),
        )
        row = rows[0] if rows else {}
        if bool(row.get("accepted")):
            status = "accepted"
        elif bool(row.get("duplicate")):
            status = "duplicate"
        else:
            status = "full"
        return SpendEnqueueResult(status=status, pending_count=int(row.get("pending_count") or 0))

    async def claim_batch(
        self,
        *,
        limit: int,
        worker_id: str,
        claim_token: str,
        lease_seconds: int,
    ) -> list[SpendOutboxRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH exhausted_candidates AS (
                SELECT event_id
                FROM deltallm_spend_ingestion_outbox
                WHERE status = 'processing'
                  AND lease_expires_at <= NOW()
                  AND attempt_count >= max_attempts
                ORDER BY lease_expires_at, event_id
                FOR UPDATE SKIP LOCKED
                LIMIT $1
            ), exhausted AS (
                UPDATE deltallm_spend_ingestion_outbox o
                SET status = 'blocked', blocked_at = NOW(), processed_at = NOW(),
                    last_error = COALESCE(last_error, 'max_attempts_exhausted_after_lease_expiry'),
                    locked_by = NULL, claim_token = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                FROM exhausted_candidates candidate
                WHERE o.event_id = candidate.event_id
                RETURNING o.event_id
            ), candidates AS (
                SELECT event_id
                FROM deltallm_spend_ingestion_outbox
                WHERE (
                    status IN ('queued', 'retry')
                    AND next_attempt_at <= NOW()
                    AND attempt_count < max_attempts
                ) OR (
                    status = 'processing'
                    AND lease_expires_at <= NOW()
                    AND attempt_count < max_attempts
                )
                ORDER BY created_at, event_id
                FOR UPDATE SKIP LOCKED
                LIMIT $1
            )
            UPDATE deltallm_spend_ingestion_outbox o
            SET status = 'processing',
                attempt_count = o.attempt_count + 1,
                locked_by = $2,
                claim_token = $3,
                lease_expires_at = NOW() + make_interval(secs => $4),
                updated_at = NOW()
            FROM candidates c
            WHERE o.event_id = c.event_id
            RETURNING o.event_id, o.event_type, o.payload_json, o.attempt_count,
                o.max_attempts, o.claim_token
            """,
            max(1, int(limit)),
            worker_id,
            claim_token,
            max(1, int(lease_seconds)),
        )
        return [
            SpendOutboxRecord(
                event_id=str(row["event_id"]),
                event_type=str(row["event_type"]),
                payload=dict(row.get("payload_json") or {}),
                attempt_count=int(row.get("attempt_count") or 1),
                max_attempts=int(row.get("max_attempts") or 1),
                claim_token=str(row["claim_token"]),
            )
            for row in rows
        ]

    async def mark_completed(
        self,
        *,
        event_ids: list[str],
        worker_id: str,
        claim_token: str,
    ) -> int:
        if self.prisma is None or not event_ids:
            return 0
        rows = await self.prisma.query_raw(
            """
            WITH completed AS (
                UPDATE deltallm_spend_ingestion_outbox
                SET status = 'completed', processed_at = NOW(), locked_by = NULL,
                    claim_token = NULL, lease_expires_at = NULL, last_error = NULL,
                    updated_at = NOW()
                WHERE event_id = ANY($1::text[])
                  AND status = 'processing'
                  AND locked_by = $2
                  AND claim_token = $3
                RETURNING event_id
            ),
            adjusted AS (
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = GREATEST(
                        0,
                        pending_count - (SELECT COUNT(*) FROM completed)
                    ),
                    updated_at = NOW()
                WHERE queue_name = 'spend'
                RETURNING pending_count
            )
            SELECT event_id FROM completed
            """,
            event_ids,
            worker_id,
            claim_token,
        )
        return len(rows)

    async def renew_lease(
        self,
        *,
        event_ids: list[str],
        worker_id: str,
        claim_token: str,
        lease_seconds: int,
    ) -> int:
        if self.prisma is None or not event_ids:
            return 0
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_spend_ingestion_outbox
            SET lease_expires_at = NOW() + make_interval(secs => $4),
                updated_at = NOW()
            WHERE event_id = ANY($1::text[])
              AND status = 'processing'
              AND locked_by = $2
              AND claim_token = $3
              AND lease_expires_at > NOW()
            RETURNING event_id
            """,
            event_ids,
            worker_id,
            claim_token,
            max(1, int(lease_seconds)),
        )
        return len(rows)

    async def mark_retry(
        self,
        *,
        record: SpendOutboxRecord,
        worker_id: str,
        error: str,
    ) -> bool:
        if self.prisma is None:
            return False
        terminal = record.attempt_count >= record.max_attempts
        delay = min(60, 2 ** min(record.attempt_count, 6))
        rows = await self.prisma.query_raw(
            """
            WITH transitioned AS (
                UPDATE deltallm_spend_ingestion_outbox
                SET status = $3,
                    next_attempt_at = $4::timestamp,
                    processed_at = CASE WHEN $3 = 'blocked' THEN NOW() ELSE processed_at END,
                    blocked_at = CASE WHEN $3 = 'blocked' THEN NOW() ELSE blocked_at END,
                    last_error = $5,
                    locked_by = NULL,
                    claim_token = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE event_id = $1
                  AND status = 'processing'
                  AND locked_by = $2
                  AND claim_token = $6
                RETURNING event_id
            )
            SELECT event_id FROM transitioned
            """,
            record.event_id,
            worker_id,
            "blocked" if terminal else "retry",
            datetime.now(tz=UTC) + timedelta(seconds=delay),
            str(error)[:2000],
            record.claim_token,
        )
        return bool(rows) and terminal

    async def pending_stats(self) -> tuple[int, float]:
        if self.prisma is None:
            return 0, 0.0
        rows = await self.prisma.query_raw(
            """
            SELECT
                COUNT(*)::bigint AS count,
                COALESCE(EXTRACT(EPOCH FROM (NOW() - MIN(created_at))), 0)::float8 AS oldest_age
            FROM deltallm_spend_ingestion_outbox
            WHERE status IN ('queued', 'retry', 'processing', 'blocked', 'failed')
            """
        )
        row = rows[0] if rows else {}
        return int(row.get("count") or 0), float(row.get("oldest_age") or 0.0)

    async def drainable_count(self) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::bigint AS count
            FROM deltallm_spend_ingestion_outbox
            WHERE status IN ('queued', 'retry', 'processing')
            """
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def reconcile_capacity(self) -> int:
        if self.prisma is None:
            return 0
        async with self._transaction() as tx:
            await tx.query_raw(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:spend-ingestion-capacity', 0)
                )::text AS locked
                """
            )
            capacity_rows = await tx.query_raw(
                """
                SELECT pending_count
                FROM deltallm_telemetry_ingestion_capacity
                WHERE queue_name = 'spend'
                FOR UPDATE
                """
            )
            if not capacity_rows:
                raise RuntimeError("spend ingestion capacity row is missing")
            count_rows = await tx.query_raw(
                """
                SELECT COUNT(*)::bigint AS count
                FROM deltallm_spend_ingestion_outbox
                WHERE status IN ('queued', 'retry', 'processing', 'blocked', 'failed')
                """
            )
            actual = int((count_rows[0] if count_rows else {}).get("count") or 0)
            rows = await tx.query_raw(
                """
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = $1, updated_at = NOW()
                WHERE queue_name = 'spend'
                RETURNING pending_count
                """,
                actual,
            )
            if not rows:
                raise RuntimeError("spend ingestion capacity row disappeared")
            return int(rows[0].get("pending_count") or 0)

    @asynccontextmanager
    async def _transaction(self):  # noqa: ANN202
        if self.prisma is None:
            raise RuntimeError("spend ingestion database is unavailable")
        if is_prisma_transaction_client(self.prisma):
            yield self.prisma
            return
        tx_factory = getattr(self.prisma, "tx", None)
        if callable(tx_factory):
            async with tx_factory() as tx:
                yield tx
            return
        raise RuntimeError("spend ingestion database does not support transactions")

    async def cleanup_terminal(
        self,
        *,
        completed_retention_hours: int,
        limit: int,
    ) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS (
                SELECT event_id
                FROM deltallm_spend_ingestion_outbox
                WHERE (
                    status = 'completed'
                    AND processed_at <= NOW() - make_interval(hours => $1::int)
                )
                ORDER BY COALESCE(processed_at, updated_at), event_id
                LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            DELETE FROM deltallm_spend_ingestion_outbox o
            USING candidates c
            WHERE o.event_id = c.event_id
            RETURNING o.event_id
            """,
            max(0, int(completed_retention_hours)),
            max(1, int(limit)),
        )
        return len(rows)

    async def replay_blocked(self, *, event_id: str, replayed_by: str) -> bool:
        """Requeue one blocked economic event without changing its stable identity or payload."""

        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_spend_ingestion_outbox
            SET status = 'retry', attempt_count = 0, next_attempt_at = NOW(),
                last_error = NULL, locked_by = NULL, claim_token = NULL,
                lease_expires_at = NULL, processed_at = NULL, blocked_at = NULL,
                replay_count = replay_count + 1, last_replayed_at = NOW(),
                last_replayed_by = $2, updated_at = NOW()
            WHERE event_id = $1
              AND status IN ('blocked', 'failed')
            RETURNING event_id
            """,
            event_id,
            replayed_by,
        )
        return bool(rows)
