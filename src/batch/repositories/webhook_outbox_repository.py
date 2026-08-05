from __future__ import annotations

from typing import Any

from src.batch.models import (
    BatchWebhookEventType,
    BatchWebhookOutboxCreate,
    BatchWebhookOutboxRecord,
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
