from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from src.batch.models import BatchCompletionOutboxCreate, BatchCompletionOutboxRecord


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return None


def _parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _record_from_row(row: dict[str, Any]) -> BatchCompletionOutboxRecord:
    return BatchCompletionOutboxRecord(
        completion_id=str(row.get("completion_id") or ""),
        batch_id=str(row.get("batch_id") or ""),
        item_id=str(row.get("item_id") or ""),
        payload_json=_parse_payload(row.get("payload_json")),
        status=str(row.get("status") or ""),
        attempt_count=int(row.get("attempt_count") or 0),
        max_attempts=int(row.get("max_attempts") or 0),
        next_attempt_at=_parse_datetime(row.get("next_attempt_at")),
        last_error=str(row.get("last_error")) if row.get("last_error") is not None else None,
        created_at=_parse_datetime(row.get("created_at")) or datetime.now(tz=UTC),
        updated_at=_parse_datetime(row.get("updated_at")) or datetime.now(tz=UTC),
        processed_at=_parse_datetime(row.get("processed_at")),
        locked_by=str(row.get("locked_by")) if row.get("locked_by") is not None else None,
        lease_expires_at=_parse_datetime(row.get("lease_expires_at")),
    )


class BatchCompletionOutboxRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def enqueue_many(self, records: list[BatchCompletionOutboxCreate]) -> list[str]:
        if self.prisma is None or not records:
            return []

        values_sql: list[str] = []
        params: list[Any] = []
        param_index = 1
        for record in records:
            values_sql.append(
                f"(${param_index}, ${param_index + 1}, ${param_index + 2}::jsonb, "
                f"${param_index + 3}, ${param_index + 4}, ${param_index + 5}, ${param_index + 6}::timestamptz, "
                f"${param_index + 7}, NOW(), NOW())"
            )
            params.extend(
                [
                    record.batch_id,
                    record.item_id,
                    json.dumps(record.payload_json),
                    record.status,
                    record.attempt_count,
                    record.max_attempts,
                    record.next_attempt_at or datetime.now(tz=UTC),
                    record.last_error,
                ]
            )
            param_index += 8

        rows = await self.prisma.query_raw(
            f"""
            INSERT INTO deltallm_batch_completion_outbox (
                batch_id,
                item_id,
                payload_json,
                status,
                attempt_count,
                max_attempts,
                next_attempt_at,
                last_error,
                created_at,
                updated_at
            )
            VALUES {", ".join(values_sql)}
            ON CONFLICT (item_id) DO NOTHING
            RETURNING completion_id
            """,
            *params,
        )
        return [str(row["completion_id"]) for row in rows]

    async def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 25,
    ) -> list[BatchCompletionOutboxRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH due AS (
                SELECT completion_id
                FROM deltallm_batch_completion_outbox
                WHERE (
                        status IN ('queued', 'retrying')
                    AND next_attempt_at <= NOW()
                ) OR (
                        status = 'processing'
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at < NOW()
                )
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE deltallm_batch_completion_outbox o
            SET status = 'processing',
                attempt_count = o.attempt_count + 1,
                locked_by = $2,
                lease_expires_at = NOW() + ($3 || ' seconds')::interval,
                updated_at = NOW()
            FROM due
            WHERE o.completion_id = due.completion_id
            RETURNING
                o.completion_id,
                o.batch_id,
                o.item_id,
                o.payload_json,
                o.status,
                o.attempt_count,
                o.max_attempts,
                o.next_attempt_at,
                o.last_error,
                o.created_at,
                o.updated_at,
                o.processed_at,
                o.locked_by,
                o.lease_expires_at
            """,
            max(1, min(limit, 500)),
            worker_id,
            max(1, lease_seconds),
        )
        return [_record_from_row(dict(row)) for row in rows]

    async def mark_sent(self, completion_id: str, *, worker_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_completion_outbox
            SET status = 'sent',
                last_error = NULL,
                locked_by = NULL,
                lease_expires_at = NULL,
                processed_at = NOW(),
                updated_at = NOW()
            WHERE completion_id = $1
              AND status = 'processing'
              AND locked_by = $2
            RETURNING completion_id
            """,
            completion_id,
            worker_id,
        )
        return bool(rows)

    async def mark_retry(
        self,
        completion_id: str,
        *,
        worker_id: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_completion_outbox
            SET status = 'retrying',
                last_error = $2,
                next_attempt_at = $3::timestamptz,
                locked_by = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            WHERE completion_id = $1
              AND status = 'processing'
              AND locked_by = $4
            RETURNING completion_id
            """,
            completion_id,
            error[:4000],
            next_attempt_at,
            worker_id,
        )
        return bool(rows)

    async def mark_failed(self, completion_id: str, *, worker_id: str, error: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_completion_outbox
            SET status = 'failed',
                last_error = $2,
                locked_by = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            WHERE completion_id = $1
              AND status = 'processing'
              AND locked_by = $3
            RETURNING completion_id
            """,
            completion_id,
            error[:4000],
            worker_id,
        )
        return bool(rows)

    async def renew_lease(self, completion_id: str, *, worker_id: str, lease_seconds: int) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_completion_outbox
            SET lease_expires_at = NOW() + ($3 || ' seconds')::interval,
                updated_at = NOW()
            WHERE completion_id = $1
              AND status = 'processing'
              AND locked_by = $2
            RETURNING completion_id
            """,
            completion_id,
            worker_id,
            max(1, lease_seconds),
        )
        return bool(rows)

    async def list_by_item_ids(self, item_ids: list[str]) -> list[BatchCompletionOutboxRecord]:
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
                o.completion_id,
                o.batch_id,
                o.item_id,
                o.payload_json,
                o.status,
                o.attempt_count,
                o.max_attempts,
                o.next_attempt_at,
                o.last_error,
                o.created_at,
                o.updated_at,
                o.processed_at,
                o.locked_by,
                o.lease_expires_at
            FROM deltallm_batch_completion_outbox o
            JOIN payload p ON p.item_id = o.item_id
            ORDER BY o.item_id ASC
            """,
            *params,
        )
        return [_record_from_row(dict(row)) for row in rows]

    async def count_pending(self) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_batch_completion_outbox
            WHERE status IN ('queued', 'retrying')
            """
        )
        if not rows:
            return 0
        return int(rows[0].get("count") or 0)
