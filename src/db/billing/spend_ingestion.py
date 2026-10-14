from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from src.db.runtime.client import is_prisma_transaction_client
from src.db.runtime.telemetry_acceptance import AcceptanceObservation, TelemetryDatabaseUnavailable
from src.metrics.telemetry_acceptance import AcceptancePhase, TelemetryQueue


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
        with AcceptanceObservation(
            TelemetryQueue.SPEND,
            owns_transaction=not is_prisma_transaction_client(self.prisma),
            event_id=event_id,
        ) as observation:
            with observation.phase(AcceptancePhase.PREPARE):
                if self.prisma is None:
                    raise TelemetryDatabaseUnavailable("spend ingestion database is unavailable")
                payload_json = json.dumps(payload, default=str)
                observation.retain_serialized(payload_json)
            # One statement takes the queue lock and commits on its own, so a
            # stalled caller never holds the lock across a client round trip.
            with observation.phase(AcceptancePhase.LOCK):
                result = await self._admit(
                    event_id=event_id,
                    event_type=event_type,
                    payload_json=payload_json,
                    max_attempts=max_attempts,
                    max_pending_events=max_pending_events,
                )
            observation.record_result([result.status])
            return result

    async def _admit(
        self,
        *,
        event_id: str,
        event_type: str,
        payload_json: str,
        max_attempts: int,
        max_pending_events: int,
    ) -> SpendEnqueueResult:
        if self.prisma is None:
            raise RuntimeError("spend ingestion database is unavailable")
        rows = await self.prisma.query_raw(
            "SELECT duplicate, accepted, pending_count "
            "FROM deltallm_admit_spend_event($1, $2, $3::jsonb, $4::int, $5::bigint)",
            event_id,
            event_type,
            payload_json,
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
        release_capacity: bool = True,
    ) -> int:
        """Complete fenced claims and, unless deferred, return their queue capacity.

        Inside a longer worker transaction pass ``release_capacity=False`` and call
        ``release_completed_capacity`` after the commit: every replica's admission
        updates the capacity row, so it must not stay locked until a later COMMIT.
        """
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
                  AND $4::boolean
                RETURNING pending_count
            )
            SELECT event_id FROM completed
            """,
            event_ids,
            worker_id,
            claim_token,
            release_capacity,
        )
        return len(rows)

    async def release_completed_capacity(self, count: int) -> None:
        """Return capacity for events a committed worker batch completed.

        One self-committing relative update: admission re-reads the row after its
        queue lock, and startup reconciliation corrects a release that never ran.
        """
        if self.prisma is None or count <= 0:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_telemetry_ingestion_capacity
            SET pending_count = GREATEST(0, pending_count - $1::bigint),
                updated_at = NOW()
            WHERE queue_name = 'spend'
            """,
            int(count),
        )

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
        # One self-committing statement takes the queue lock, locks the capacity
        # row and counts, so the lock never spans a client round trip.
        rows = await self.prisma.query_raw(
            "SELECT deltallm_reconcile_spend_capacity()::bigint AS pending_count"
        )
        if not rows:
            raise RuntimeError("spend ingestion capacity reconciliation returned no row")
        return int(rows[0].get("pending_count") or 0)

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
              AND (operation_state IS NULL OR operation_state='accepted')
            RETURNING event_id
            """,
            event_id,
            replayed_by,
        )
        return bool(rows)
