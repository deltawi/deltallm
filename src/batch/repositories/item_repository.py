from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.batch.models import BatchItemCreate, BatchItemRecord, BatchItemStatus
from src.batch.repositories.mappers import item_from_row
from src.metrics import increment_batch_item_reclaim

logger = logging.getLogger(__name__)


class BatchItemRepository:
    _MAX_BULK_INSERT_ROWS = 200

    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def create_items(self, batch_id: str, items: list[BatchItemCreate]) -> int:
        if self.prisma is None or not items:
            return 0
        if len(items) > self._MAX_BULK_INSERT_ROWS:
            inserted = 0
            for start in range(0, len(items), self._MAX_BULK_INSERT_ROWS):
                inserted += await self.create_items(batch_id, items[start : start + self._MAX_BULK_INSERT_ROWS])
            return inserted

        values_sql: list[str] = []
        params: list[Any] = []
        param_index = 1
        inserted = 0
        for item in items:
            item_id = str(uuid4())
            values_sql.append(
                f"(${param_index}, ${param_index + 1}, ${param_index + 2}, ${param_index + 3}, "
                f"${param_index + 4}, ${param_index + 5}::jsonb)"
            )
            params.extend(
                [
                    item_id,
                    batch_id,
                    item.line_number,
                    item.custom_id,
                    BatchItemStatus.PENDING,
                    json.dumps(item.request_body),
                ]
            )
            param_index += 6
        rows = await self.prisma.query_raw(
            f"""
            INSERT INTO deltallm_batch_item (item_id, batch_id, line_number, custom_id, status, request_body)
            VALUES {", ".join(values_sql)}
            ON CONFLICT (batch_id, line_number) DO NOTHING
            RETURNING item_id
            """,
            *params,
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
                SELECT item_id, status AS previous_status
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
            RETURNING i.*, candidate.previous_status
            """,
            batch_id,
            max(1, min(limit, 200)),
            worker_id,
            lease_seconds,
        )
        reclaimed_count = sum(1 for row in rows if row.get("previous_status") == "in_progress")
        for _ in range(reclaimed_count):
            increment_batch_item_reclaim()
        if reclaimed_count > 0:
            logger.info(
                "batch items reclaimed batch_id=%s worker_id=%s count=%s",
                batch_id,
                worker_id,
                reclaimed_count,
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

    async def mark_items_completed_bulk(
        self,
        *,
        items: list[dict[str, Any]],
        worker_id: str | None,
    ) -> bool:
        if self.prisma is None:
            return False
        if not items:
            return True

        values_sql: list[str] = []
        params: list[Any] = []
        param_index = 1
        for item in items:
            values_sql.append(
                f"(${param_index}, ${param_index + 1}::jsonb, ${param_index + 2}::jsonb, "
                f"${param_index + 3}, ${param_index + 4})"
            )
            params.extend(
                [
                    item["item_id"],
                    json.dumps(item["response_body"]),
                    json.dumps(item.get("usage") or {}),
                    item["provider_cost"],
                    item["billed_cost"],
                ]
            )
            param_index += 5

        if worker_id is None:
            expected_count_param = param_index
            params.append(len(items))
            rows = await self.prisma.query_raw(
                f"""
                WITH payload(item_id, response_body, usage, provider_cost, billed_cost) AS (
                    VALUES {", ".join(values_sql)}
                ),
                candidate AS (
                    SELECT i.item_id
                    FROM deltallm_batch_item i
                    JOIN payload p ON i.item_id = p.item_id
                    WHERE i.status = 'in_progress'
                    FOR UPDATE SKIP LOCKED
                ),
                eligible AS (
                    SELECT COUNT(*) AS eligible_count
                    FROM candidate
                ),
                updated AS (
                    UPDATE deltallm_batch_item i
                    SET status = 'completed',
                        response_body = p.response_body,
                        usage = p.usage,
                        provider_cost = p.provider_cost,
                        billed_cost = p.billed_cost,
                        lease_expires_at = NULL,
                        locked_by = NULL,
                        completed_at = NOW()
                    FROM payload p
                    JOIN candidate c ON c.item_id = p.item_id
                    CROSS JOIN eligible
                    WHERE i.item_id = c.item_id
                      AND eligible.eligible_count = ${expected_count_param}
                    RETURNING i.item_id
                )
                SELECT item_id FROM updated
                """,
                *params,
            )
            return len(rows) == len(items)

        worker_id_param = param_index
        expected_count_param = param_index + 1
        params.extend([worker_id, len(items)])
        rows = await self.prisma.query_raw(
            f"""
            WITH payload(item_id, response_body, usage, provider_cost, billed_cost) AS (
                VALUES {", ".join(values_sql)}
            ),
            candidate AS (
                SELECT i.item_id
                FROM deltallm_batch_item i
                JOIN payload p ON i.item_id = p.item_id
                WHERE i.status = 'in_progress'
                  AND i.locked_by = ${worker_id_param}
                FOR UPDATE SKIP LOCKED
            ),
            eligible AS (
                SELECT COUNT(*) AS eligible_count
                FROM candidate
            ),
            updated AS (
                UPDATE deltallm_batch_item i
                SET status = 'completed',
                    response_body = p.response_body,
                    usage = p.usage,
                    provider_cost = p.provider_cost,
                    billed_cost = p.billed_cost,
                    lease_expires_at = NULL,
                    locked_by = NULL,
                    completed_at = NOW()
                FROM payload p
                JOIN candidate c ON c.item_id = p.item_id
                CROSS JOIN eligible
                WHERE i.item_id = c.item_id
                  AND eligible.eligible_count = ${expected_count_param}
                RETURNING i.item_id
            )
            SELECT item_id FROM updated
            """,
            *params,
        )
        return len(rows) == len(items)

    async def release_items_for_retry(
        self,
        *,
        item_ids: list[str],
        worker_id: str,
    ) -> list[str]:
        if self.prisma is None or not item_ids:
            return []

        values_sql: list[str] = []
        params: list[Any] = []
        param_index = 1
        for item_id in item_ids:
            values_sql.append(f"(${param_index})")
            params.append(item_id)
            param_index += 1

        worker_id_param = param_index
        params.append(worker_id)
        rows = await self.prisma.query_raw(
            f"""
            WITH payload(item_id) AS (
                VALUES {", ".join(values_sql)}
            ),
            updated AS (
                UPDATE deltallm_batch_item i
                SET status = 'pending',
                    lease_expires_at = NULL,
                    locked_by = NULL
                FROM payload p
                WHERE i.item_id = p.item_id
                  AND i.locked_by = ${worker_id_param}
                  AND i.status = 'in_progress'
                RETURNING i.item_id
            )
            SELECT item_id FROM updated
            """,
            *params,
        )
        return [str(row["item_id"]) for row in rows]

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

    async def list_items_by_ids(self, item_ids: list[str]) -> list[BatchItemRecord]:
        if self.prisma is None or not item_ids:
            return []

        values_sql: list[str] = []
        params: list[Any] = []
        for index, item_id in enumerate(item_ids, start=1):
            values_sql.append(f"(${index})")
            params.append(item_id)

        rows = await self.prisma.query_raw(
            f"""
            WITH payload(item_id) AS (
                VALUES {", ".join(values_sql)}
            )
            SELECT
                i.item_id,
                i.batch_id,
                i.line_number,
                i.custom_id,
                i.status,
                i.request_body,
                i.response_body,
                i.error_body,
                i.usage,
                i.provider_cost,
                i.billed_cost,
                i.attempts,
                i.last_error,
                i.locked_by,
                i.lease_expires_at,
                i.created_at,
                i.started_at,
                i.completed_at
            FROM deltallm_batch_item i
            JOIN payload p ON p.item_id = i.item_id
            ORDER BY i.line_number ASC
            """,
            *params,
        )
        return [item_from_row(row) for row in rows]

    async def list_items_page(
        self,
        *,
        batch_id: str,
        limit: int = 500,
        after_line_number: int | None = None,
    ) -> list[BatchItemRecord]:
        if self.prisma is None:
            return []
        if after_line_number is None:
            rows = await self.prisma.query_raw(
                """
                SELECT *
                FROM deltallm_batch_item
                WHERE batch_id = $1
                ORDER BY line_number ASC
                LIMIT $2
                """,
                batch_id,
                max(1, min(limit, 5_000)),
            )
        else:
            rows = await self.prisma.query_raw(
                """
                SELECT *
                FROM deltallm_batch_item
                WHERE batch_id = $1
                  AND line_number > $2
                ORDER BY line_number ASC
                LIMIT $3
                """,
                batch_id,
                after_line_number,
                max(1, min(limit, 5_000)),
            )
        return [item_from_row(row) for row in rows]

    async def summarize_runtime_statuses(self, *, now: datetime) -> dict[str, float]:
        if self.prisma is None:
            return {
                "pending_items": 0,
                "in_progress_items": 0,
                "oldest_pending_item_age_seconds": 0.0,
                "oldest_in_progress_item_age_seconds": 0.0,
            }
        rows = await self.prisma.query_raw(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending') AS pending_items,
                COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress_items,
                COALESCE(
                    EXTRACT(EPOCH FROM ($1::timestamp - MIN(CASE WHEN status = 'pending' THEN created_at END))),
                    0
                ) AS oldest_pending_item_age_seconds,
                COALESCE(
                    EXTRACT(
                        EPOCH FROM (
                            $1::timestamp - MIN(CASE WHEN status = 'in_progress' THEN COALESCE(started_at, created_at) END)
                        )
                    ),
                    0
                ) AS oldest_in_progress_item_age_seconds
            FROM deltallm_batch_item
            """,
            now,
        )
        row = dict(rows[0]) if rows else {}
        return {
            "pending_items": int(row.get("pending_items") or 0),
            "in_progress_items": int(row.get("in_progress_items") or 0),
            "oldest_pending_item_age_seconds": float(row.get("oldest_pending_item_age_seconds") or 0.0),
            "oldest_in_progress_item_age_seconds": float(row.get("oldest_in_progress_item_age_seconds") or 0.0),
        }

    async def requeue_expired_in_progress_items(self, batch_id: str) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_item
            SET status = 'pending',
                locked_by = NULL,
                lease_expires_at = NULL
            WHERE batch_id = $1
              AND status = 'in_progress'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW()
            RETURNING item_id
            """,
            batch_id,
        )
        return len(rows)

    async def fail_nonterminal_items(self, *, batch_id: str, reason: str) -> int:
        if self.prisma is None:
            return 0
        error_payload = json.dumps({"message": reason, "type": "OperatorFailed"})
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_item
            SET status = 'failed',
                error_body = $2::jsonb,
                last_error = $3,
                locked_by = NULL,
                lease_expires_at = NULL,
                completed_at = NOW()
            WHERE batch_id = $1
              AND status IN ('pending', 'in_progress')
            RETURNING item_id
            """,
            batch_id,
            error_payload,
            reason,
        )
        return len(rows)
