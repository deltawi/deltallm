from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from src.batch.models import BatchItemCreate, BatchItemRecord, BatchItemStatus
from src.batch.repositories.mappers import item_from_row


class BatchItemRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def create_items(self, batch_id: str, items: list[BatchItemCreate]) -> int:
        if self.prisma is None or not items:
            return 0
        inserted = 0
        for item in items:
            item_id = str(uuid4())
            rows = await self.prisma.query_raw(
                """
                INSERT INTO deltallm_batch_item (item_id, batch_id, line_number, custom_id, status, request_body)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (batch_id, line_number) DO NOTHING
                RETURNING item_id
                """,
                item_id,
                batch_id,
                item.line_number,
                item.custom_id,
                BatchItemStatus.PENDING,
                json.dumps(item.request_body),
            )
            inserted += len(rows)
        return inserted

    async def claim_items(
        self,
        *,
        batch_id: str,
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = 60,
    ) -> list[BatchItemRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH candidate AS (
                SELECT item_id
                FROM deltallm_batch_item
                WHERE batch_id = $1
                  AND (
                        (status = 'pending' AND (lease_expires_at IS NULL OR lease_expires_at < NOW()))
                     OR (status = 'in_progress' AND lease_expires_at < NOW())
                  )
                ORDER BY line_number ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $2
            )
            UPDATE deltallm_batch_item i
            SET status = 'in_progress',
                locked_by = $3,
                lease_expires_at = NOW() + ($4 || ' seconds')::interval,
                attempts = attempts + 1,
                started_at = COALESCE(i.started_at, NOW())
            FROM candidate
            WHERE i.item_id = candidate.item_id
            RETURNING i.*
            """,
            batch_id,
            max(1, min(limit, 200)),
            worker_id,
            lease_seconds,
        )
        return [item_from_row(row) for row in rows]

    async def mark_item_completed(
        self,
        *,
        item_id: str,
        worker_id: str | None,
        response_body: dict[str, Any],
        usage: dict[str, Any] | None,
        provider_cost: float,
        billed_cost: float,
    ) -> bool:
        if self.prisma is None:
            return False
        if worker_id is None:
            rows = await self.prisma.query_raw(
                """
                UPDATE deltallm_batch_item
                SET status = 'completed',
                    response_body = $2::jsonb,
                    usage = $3::jsonb,
                    provider_cost = $4,
                    billed_cost = $5,
                    lease_expires_at = NULL,
                    locked_by = NULL,
                    completed_at = NOW()
                WHERE item_id = $1
                  AND status = 'in_progress'
                RETURNING item_id
                """,
                item_id,
                json.dumps(response_body),
                json.dumps(usage or {}),
                provider_cost,
                billed_cost,
            )
            return bool(rows)
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_item
            SET status = 'completed',
                response_body = $3::jsonb,
                usage = $4::jsonb,
                provider_cost = $5,
                billed_cost = $6,
                lease_expires_at = NULL,
                locked_by = NULL,
                completed_at = NOW()
            WHERE item_id = $1
              AND locked_by = $2
              AND status = 'in_progress'
            RETURNING item_id
            """,
            item_id,
            worker_id,
            json.dumps(response_body),
            json.dumps(usage or {}),
            provider_cost,
            billed_cost,
        )
        return bool(rows)

    async def mark_item_failed(
        self,
        *,
        item_id: str,
        worker_id: str | None,
        error_body: dict[str, Any],
        last_error: str,
        retryable: bool,
        retry_delay_seconds: int = 0,
    ) -> bool:
        if self.prisma is None:
            return False
        if retryable:
            if worker_id is None:
                rows = await self.prisma.query_raw(
                    """
                    UPDATE deltallm_batch_item
                    SET status = 'pending',
                        error_body = $2::jsonb,
                        last_error = $3,
                        lease_expires_at = NOW() + ($4 || ' seconds')::interval,
                        locked_by = NULL
                    WHERE item_id = $1
                      AND status = 'in_progress'
                    RETURNING item_id
                    """,
                    item_id,
                    json.dumps(error_body),
                    last_error,
                    max(0, retry_delay_seconds),
                )
                return bool(rows)
            rows = await self.prisma.query_raw(
                """
                UPDATE deltallm_batch_item
                SET status = 'pending',
                    error_body = $2::jsonb,
                    last_error = $3,
                    lease_expires_at = NOW() + ($4 || ' seconds')::interval,
                    locked_by = NULL
                WHERE item_id = $1
                  AND locked_by = $5
                  AND status = 'in_progress'
                RETURNING item_id
                """,
                item_id,
                json.dumps(error_body),
                last_error,
                max(0, retry_delay_seconds),
                worker_id,
            )
            return bool(rows)
        if worker_id is None:
            rows = await self.prisma.query_raw(
                """
                UPDATE deltallm_batch_item
                SET status = 'failed',
                    error_body = $2::jsonb,
                    last_error = $3,
                    lease_expires_at = NULL,
                    locked_by = NULL,
                    completed_at = NOW()
                WHERE item_id = $1
                  AND status = 'in_progress'
                RETURNING item_id
                """,
                item_id,
                json.dumps(error_body),
                last_error,
            )
            return bool(rows)
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_item
            SET status = 'failed',
                error_body = $3::jsonb,
                last_error = $4,
                lease_expires_at = NULL,
                locked_by = NULL,
                completed_at = NOW()
            WHERE item_id = $1
              AND locked_by = $2
              AND status = 'in_progress'
            RETURNING item_id
            """,
            item_id,
            worker_id,
            json.dumps(error_body),
            last_error,
        )
        return bool(rows)

    async def renew_item_lease(self, *, item_id: str, worker_id: str, lease_seconds: int) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_item
            SET lease_expires_at = NOW() + ($3 || ' seconds')::interval
            WHERE item_id = $1
              AND locked_by = $2
              AND status = 'in_progress'
            RETURNING item_id
            """,
            item_id,
            worker_id,
            lease_seconds,
        )
        return bool(rows)

    async def mark_pending_items_cancelled(self, batch_id: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_batch_item
            SET status = 'cancelled',
                lease_expires_at = NULL,
                locked_by = NULL,
                completed_at = NOW()
            WHERE batch_id = $1
              AND status = 'pending'
            """,
            batch_id,
        )

    async def list_items(self, batch_id: str) -> list[BatchItemRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_item
            WHERE batch_id = $1
            ORDER BY line_number ASC
            """,
            batch_id,
        )
        return [item_from_row(row) for row in rows]
