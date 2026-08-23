from __future__ import annotations

from datetime import datetime
from typing import Any

from src.batch.models import (
    BATCH_WEBHOOK_DELIVERY_STATUSES,
    BatchWebhookEventType,
    BatchWebhookQueueSummary,
    BatchWebhookReplayResult,
    BatchWebhookOwnershipConflictError,
    BatchWebhookOutboxCreate,
    BatchWebhookOutboxRecord,
    normalize_batch_webhook_last_error,
    normalize_batch_webhook_delivery_status,
    normalize_batch_webhook_event_type,
)
from src.batch.repositories.mappers import webhook_outbox_from_row
from src.batch.webhooks.events import canonical_batch_webhook_event_bytes


class BatchWebhookOutboxRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def insert_event(
        self,
        event: BatchWebhookOutboxCreate,
    ) -> BatchWebhookOutboxRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_batch_webhook_outbox (
                event_id,
                batch_id,
                event_type,
                created_by_team_id,
                created_by_organization_id,
                target_config_ciphertext,
                payload_json,
                payload_sha256,
                status,
                attempt_count,
                max_attempts,
                next_attempt_at,
                last_status_code,
                last_error,
                created_at,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7::jsonb,
                $8,
                $9,
                $10,
                $11,
                COALESCE($12::timestamp, NOW()),
                $13,
                $14,
                NOW(),
                NOW()
            )
            ON CONFLICT (batch_id, event_type) DO NOTHING
            RETURNING *
            """,
            event.event_id,
            event.batch_id,
            event.event_type.value,
            event.created_by_team_id,
            event.created_by_organization_id,
            event.target_config_ciphertext,
            canonical_batch_webhook_event_bytes(event.payload_json).decode("utf-8"),
            event.payload_sha256,
            event.status.value,
            event.attempt_count,
            event.max_attempts,
            event.next_attempt_at,
            event.last_status_code,
            event.last_error,
        )
        if not rows:
            return None
        return webhook_outbox_from_row(dict(rows[0]))

    async def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int,
    ) -> list[BatchWebhookOutboxRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            WITH due AS (
                SELECT event_id, status AS previous_status
                FROM deltallm_batch_webhook_outbox webhook
                WHERE webhook.attempt_count < webhook.max_attempts
                  AND CASE
                        WHEN webhook.created_by_organization_id IS NOT NULL THEN EXISTS (
                            SELECT 1
                            FROM deltallm_organizationtable organization
                            WHERE organization.organization_id =
                                  webhook.created_by_organization_id
                              AND organization.lifecycle_state = 'active'
                        )
                        WHEN webhook.created_by_team_id IS NOT NULL THEN EXISTS (
                            SELECT 1
                            FROM deltallm_teamtable team
                            LEFT JOIN deltallm_organizationtable organization
                              ON organization.organization_id = team.organization_id
                            WHERE team.team_id = webhook.created_by_team_id
                              AND (
                                  team.organization_id IS NULL
                                  OR organization.lifecycle_state = 'active'
                              )
                        )
                        ELSE TRUE
                      END
                  AND (
                        (
                            webhook.status IN ('queued', 'retrying')
                            AND webhook.next_attempt_at <= NOW()
                        )
                        OR (
                            webhook.status = 'processing'
                            AND webhook.lease_expires_at IS NOT NULL
                            AND webhook.lease_expires_at < NOW()
                        )
                  )
                ORDER BY webhook.next_attempt_at ASC, webhook.created_at ASC,
                         webhook.event_id ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE deltallm_batch_webhook_outbox o
            SET status = 'processing',
                attempt_count = o.attempt_count + 1,
                locked_by = $2,
                lease_expires_at = NOW() + ($3 || ' seconds')::interval,
                updated_at = NOW()
            FROM due
            WHERE o.event_id = due.event_id
            RETURNING o.*, (due.previous_status = 'processing') AS recovered_from_expired_lease
            """,
            max(1, min(int(limit), 500)),
            worker_id,
            max(1, int(lease_seconds)),
        )
        return [webhook_outbox_from_row(dict(row)) for row in rows]

    async def fail_exhausted_expired_leases(
        self,
        *,
        limit: int = 100,
    ) -> list[BatchWebhookOutboxRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH exhausted AS (
                SELECT event_id
                FROM deltallm_batch_webhook_outbox
                WHERE status = 'processing'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < NOW()
                  AND attempt_count >= max_attempts
                ORDER BY lease_expires_at ASC, event_id ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE deltallm_batch_webhook_outbox o
            SET status = 'failed',
                last_error = 'max_attempts_exhausted_after_lease_expiry',
                locked_by = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            FROM exhausted
            WHERE o.event_id = exhausted.event_id
            RETURNING o.*
            """,
            max(1, min(int(limit), 500)),
        )
        return [webhook_outbox_from_row(dict(row)) for row in rows]

    async def renew_lease(
        self,
        event_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        lease_seconds: int,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_webhook_outbox
            SET lease_expires_at = NOW() + ($4 || ' seconds')::interval,
                updated_at = NOW()
            WHERE event_id = $1
              AND status = 'processing'
              AND locked_by = $2
              AND attempt_count = $3
            RETURNING event_id
            """,
            event_id,
            worker_id,
            int(attempt_count),
            max(1, int(lease_seconds)),
        )
        return bool(rows)

    async def mark_delivered(
        self,
        event_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        status_code: int,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_webhook_outbox
            SET status = 'delivered',
                last_status_code = $4,
                last_error = NULL,
                locked_by = NULL,
                lease_expires_at = NULL,
                delivered_at = NOW(),
                updated_at = NOW()
            WHERE event_id = $1
              AND status = 'processing'
              AND locked_by = $2
              AND attempt_count = $3
            RETURNING event_id
            """,
            event_id,
            worker_id,
            int(attempt_count),
            int(status_code),
        )
        return bool(rows)

    async def mark_retrying(
        self,
        event_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        status_code: int | None,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_webhook_outbox
            SET status = 'retrying',
                last_status_code = $4,
                last_error = $5,
                next_attempt_at = $6::timestamptz,
                locked_by = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND status = 'processing'
              AND locked_by = $2
              AND attempt_count = $3
              AND attempt_count < max_attempts
            RETURNING event_id
            """,
            event_id,
            worker_id,
            int(attempt_count),
            int(status_code) if status_code is not None else None,
            normalize_batch_webhook_last_error(error),
            next_attempt_at,
        )
        return bool(rows)

    async def mark_failed(
        self,
        event_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        status_code: int | None,
        error: str,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_webhook_outbox
            SET status = 'failed',
                last_status_code = $4,
                last_error = $5,
                locked_by = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND status = 'processing'
              AND locked_by = $2
              AND attempt_count = $3
            RETURNING event_id
            """,
            event_id,
            worker_id,
            int(attempt_count),
            int(status_code) if status_code is not None else None,
            normalize_batch_webhook_last_error(error),
        )
        return bool(rows)

    async def get_by_batch_and_event_type(
        self,
        *,
        batch_id: str,
        event_type: str | BatchWebhookEventType,
    ) -> BatchWebhookOutboxRecord | None:
        if self.prisma is None:
            return None
        normalized_event_type = normalize_batch_webhook_event_type(event_type)
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_webhook_outbox
            WHERE batch_id = $1
              AND event_type = $2
            LIMIT 1
            """,
            batch_id,
            normalized_event_type.value,
        )
        if not rows:
            return None
        return webhook_outbox_from_row(dict(rows[0]))

    async def fill_missing_ownership(
        self,
        *,
        batch_id: str,
        event_type: str | BatchWebhookEventType,
        created_by_team_id: str | None,
        created_by_organization_id: str | None,
    ) -> BatchWebhookOutboxRecord | None:
        """Fill ownership omitted by an older writer without changing retention state."""
        if self.prisma is None:
            return None
        normalized_event_type = normalize_batch_webhook_event_type(event_type)
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_webhook_outbox
            SET created_by_team_id = COALESCE(created_by_team_id, $3::text),
                created_by_organization_id = COALESCE(created_by_organization_id, $4::text)
            WHERE batch_id = $1
              AND event_type = $2
              AND (
                    (created_by_team_id IS NULL AND $3::text IS NOT NULL)
                    OR
                    (created_by_organization_id IS NULL AND $4::text IS NOT NULL)
              )
            RETURNING *
            """,
            batch_id,
            normalized_event_type.value,
            created_by_team_id,
            created_by_organization_id,
        )
        if not rows:
            return None
        return webhook_outbox_from_row(dict(rows[0]))

    async def backfill_missing_ownership_for_batches(
        self,
        *,
        batch_ids: list[str],
    ) -> int:
        """Snapshot ownership for a bounded cleanup page written by old binaries."""
        if self.prisma is None or not batch_ids:
            return 0
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_webhook_outbox o
            SET created_by_team_id = COALESCE(o.created_by_team_id, j.created_by_team_id),
                created_by_organization_id = COALESCE(
                    o.created_by_organization_id,
                    j.created_by_organization_id
                )
            FROM deltallm_batch_job j
            WHERE j.batch_id = o.batch_id
              AND o.batch_id::text = ANY($1::text[])
              AND (o.created_by_team_id IS NULL OR o.created_by_team_id = j.created_by_team_id)
              AND (
                    o.created_by_organization_id IS NULL
                    OR o.created_by_organization_id = j.created_by_organization_id
              )
              AND (
                    (o.created_by_team_id IS NULL AND j.created_by_team_id IS NOT NULL)
                    OR
                    (
                        o.created_by_organization_id IS NULL
                        AND j.created_by_organization_id IS NOT NULL
                    )
              )
            RETURNING o.event_id
            """,
            batch_ids,
        )
        return len(rows)

    async def assert_ownership_matches_jobs_for_batches(
        self,
        *,
        batch_ids: list[str],
    ) -> None:
        """Fail closed before cleanup could orphan a mismatched ownership snapshot."""
        if self.prisma is None or not batch_ids:
            return
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS conflict_count
            FROM deltallm_batch_webhook_outbox o
            JOIN deltallm_batch_job j ON j.batch_id = o.batch_id
            WHERE o.batch_id::text = ANY($1::text[])
              AND (
                    o.created_by_team_id IS DISTINCT FROM j.created_by_team_id
                    OR o.created_by_organization_id IS DISTINCT FROM j.created_by_organization_id
              )
            """,
            batch_ids,
        )
        conflict_count = int(dict(rows[0]).get("conflict_count") or 0) if rows else 0
        if conflict_count:
            raise BatchWebhookOwnershipConflictError(conflict_count)

    async def list_by_batch_id(self, *, batch_id: str) -> list[BatchWebhookOutboxRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_webhook_outbox
            WHERE batch_id = $1
            ORDER BY created_at ASC, event_id ASC
            """,
            batch_id,
        )
        return [webhook_outbox_from_row(dict(row)) for row in rows]

    async def replay_failed(
        self,
        *,
        batch_id: str,
        event_id: str,
    ) -> BatchWebhookReplayResult | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            WITH failed AS (
                SELECT event_id, attempt_count AS previous_attempt_count
                FROM deltallm_batch_webhook_outbox
                WHERE event_id = $1
                  AND batch_id = $2
                  AND status = 'failed'
                FOR UPDATE
            )
            UPDATE deltallm_batch_webhook_outbox o
            SET status = 'queued',
                attempt_count = 0,
                next_attempt_at = NOW(),
                last_status_code = NULL,
                last_error = NULL,
                locked_by = NULL,
                lease_expires_at = NULL,
                delivered_at = NULL,
                updated_at = NOW()
            FROM failed
            WHERE o.event_id = failed.event_id
            RETURNING o.*, failed.previous_attempt_count
            """,
            event_id,
            batch_id,
        )
        if not rows:
            return None
        row = dict(rows[0])
        return BatchWebhookReplayResult(
            record=webhook_outbox_from_row(row),
            previous_attempt_count=int(row.get("previous_attempt_count") or 0),
        )

    async def summarize(self) -> BatchWebhookQueueSummary:
        empty_counts = {status: 0 for status in BATCH_WEBHOOK_DELIVERY_STATUSES}
        if self.prisma is None:
            return BatchWebhookQueueSummary(
                counts=empty_counts,
                oldest_pending_age_seconds=0.0,
                due_count=0,
            )
        rows = await self.prisma.query_raw(
            """
            SELECT
                status,
                COUNT(*)::int AS count,
                CASE
                    WHEN status IN ('queued', 'processing', 'retrying')
                    THEN EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))
                    ELSE 0
                END AS oldest_age_seconds,
                COUNT(*) FILTER (
                    WHERE (
                        status IN ('queued', 'retrying')
                        AND attempt_count < max_attempts
                        AND next_attempt_at <= NOW()
                    ) OR (
                        status = 'processing'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at < NOW()
                    )
                )::int AS due_count
            FROM deltallm_batch_webhook_outbox
            GROUP BY status
            """
        )
        counts = dict(empty_counts)
        oldest_pending_age_seconds = 0.0
        due_count = 0
        for raw_row in rows:
            row = dict(raw_row)
            status = normalize_batch_webhook_delivery_status(row.get("status") or "")
            counts[status] = int(row.get("count") or 0)
            oldest_pending_age_seconds = max(
                oldest_pending_age_seconds,
                float(row.get("oldest_age_seconds") or 0.0),
            )
            due_count += max(0, int(row.get("due_count") or 0))
        return BatchWebhookQueueSummary(
            counts=counts,
            oldest_pending_age_seconds=max(0.0, oldest_pending_age_seconds),
            due_count=due_count,
        )

    async def delete_terminal_before(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> dict[str, int]:
        if self.prisma is None:
            return {"delivered": 0, "failed": 0}
        rows = await self.prisma.query_raw(
            """
            WITH expired AS (
                SELECT event_id
                FROM deltallm_batch_webhook_outbox
                WHERE status IN ('delivered', 'failed')
                  AND updated_at < $1::timestamp
                ORDER BY updated_at ASC, event_id ASC
                LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            DELETE FROM deltallm_batch_webhook_outbox o
            USING expired
            WHERE o.event_id = expired.event_id
            RETURNING o.status
            """,
            cutoff,
            max(1, min(int(limit), 1_000)),
        )
        deleted = {"delivered": 0, "failed": 0}
        for row in rows:
            status = str(dict(row).get("status") or "")
            if status in deleted:
                deleted[status] += 1
        return deleted
