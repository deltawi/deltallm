from __future__ import annotations

from datetime import datetime
from typing import Any

from src.batch.models import (
    BatchWebhookEventType,
    BatchWebhookOutboxCreate,
    BatchWebhookOutboxRecord,
    normalize_batch_webhook_last_error,
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
                $5::jsonb,
                $6,
                $7,
                $8,
                $9,
                COALESCE($10::timestamp, NOW()),
                $11,
                $12,
                NOW(),
                NOW()
            )
            ON CONFLICT (batch_id, event_type) DO NOTHING
            RETURNING *
            """,
            event.event_id,
            event.batch_id,
            event.event_type.value,
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

        # A worker that disappeared during its final allowed attempt cannot be
        # reclaimed without exceeding the configured attempt bound. Expire it
        # first so it cannot remain in processing forever.
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_batch_webhook_outbox
            SET status = 'failed',
                last_error = 'max_attempts_exhausted_after_lease_expiry',
                locked_by = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            WHERE status = 'processing'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW()
              AND attempt_count >= max_attempts
            """
        )

        rows = await self.prisma.query_raw(
            """
            WITH due AS (
                SELECT event_id
                FROM deltallm_batch_webhook_outbox
                WHERE attempt_count < max_attempts
                  AND (
                        (
                            status IN ('queued', 'retrying')
                            AND next_attempt_at <= NOW()
                        )
                        OR (
                            status = 'processing'
                            AND lease_expires_at IS NOT NULL
                            AND lease_expires_at < NOW()
                        )
                  )
                ORDER BY next_attempt_at ASC, created_at ASC, event_id ASC
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
            RETURNING o.*
            """,
            max(1, min(int(limit), 500)),
            worker_id,
            max(1, int(lease_seconds)),
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
