from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.batch.models import (
    BatchFileRecord,
    BatchItemCreate,
    BatchItemRecord,
    BatchItemStatus,
    BatchJobRecord,
    BatchJobStatus,
)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def _parse_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, json.JSONDecodeError):
            return None
    return None


class BatchRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    def _file_from_row(self, row: dict[str, Any]) -> BatchFileRecord:
        return BatchFileRecord(
            file_id=str(row.get("file_id") or ""),
            purpose=str(row.get("purpose") or ""),
            filename=str(row.get("filename") or ""),
            bytes=int(row.get("bytes") or 0),
            status=str(row.get("status") or "processed"),
            storage_backend=str(row.get("storage_backend") or "local"),
            storage_key=str(row.get("storage_key") or ""),
            checksum=row.get("checksum"),
            created_by_api_key=row.get("created_by_api_key"),
            created_by_user_id=row.get("created_by_user_id"),
            created_by_team_id=row.get("created_by_team_id"),
            created_at=_parse_datetime(row.get("created_at")) or datetime.now(tz=UTC),
            expires_at=_parse_datetime(row.get("expires_at")),
        )

    def _job_from_row(self, row: dict[str, Any]) -> BatchJobRecord:
        return BatchJobRecord(
            batch_id=str(row.get("batch_id") or ""),
            endpoint=str(row.get("endpoint") or ""),
            status=str(row.get("status") or BatchJobStatus.VALIDATING),
            execution_mode=str(row.get("execution_mode") or "managed_internal"),
            input_file_id=str(row.get("input_file_id") or ""),
            output_file_id=row.get("output_file_id"),
            error_file_id=row.get("error_file_id"),
            model=row.get("model"),
            metadata=_parse_json(row.get("metadata")),
            provider_batch_id=row.get("provider_batch_id"),
            provider_status=row.get("provider_status"),
            provider_error=row.get("provider_error"),
            provider_last_sync_at=_parse_datetime(row.get("provider_last_sync_at")),
            total_items=int(row.get("total_items") or 0),
            in_progress_items=int(row.get("in_progress_items") or 0),
            completed_items=int(row.get("completed_items") or 0),
            failed_items=int(row.get("failed_items") or 0),
            cancelled_items=int(row.get("cancelled_items") or 0),
            locked_by=row.get("locked_by"),
            lease_expires_at=_parse_datetime(row.get("lease_expires_at")),
            cancel_requested_at=_parse_datetime(row.get("cancel_requested_at")),
            status_last_updated_at=_parse_datetime(row.get("status_last_updated_at")),
            created_by_api_key=row.get("created_by_api_key"),
            created_by_user_id=row.get("created_by_user_id"),
            created_by_team_id=row.get("created_by_team_id"),
            created_at=_parse_datetime(row.get("created_at")) or datetime.now(tz=UTC),
            started_at=_parse_datetime(row.get("started_at")),
            completed_at=_parse_datetime(row.get("completed_at")),
            expires_at=_parse_datetime(row.get("expires_at")),
        )

    def _item_from_row(self, row: dict[str, Any]) -> BatchItemRecord:
        return BatchItemRecord(
            item_id=str(row.get("item_id") or ""),
            batch_id=str(row.get("batch_id") or ""),
            line_number=int(row.get("line_number") or 0),
            custom_id=str(row.get("custom_id") or ""),
            status=str(row.get("status") or BatchItemStatus.PENDING),
            request_body=_parse_json(row.get("request_body")) or {},
            response_body=_parse_json(row.get("response_body")),
            error_body=_parse_json(row.get("error_body")),
            usage=_parse_json(row.get("usage")),
            provider_cost=float(row.get("provider_cost") or 0.0),
            billed_cost=float(row.get("billed_cost") or 0.0),
            attempts=int(row.get("attempts") or 0),
            last_error=row.get("last_error"),
            locked_by=row.get("locked_by"),
            lease_expires_at=_parse_datetime(row.get("lease_expires_at")),
            created_at=_parse_datetime(row.get("created_at")) or datetime.now(tz=UTC),
            started_at=_parse_datetime(row.get("started_at")),
            completed_at=_parse_datetime(row.get("completed_at")),
        )

    async def create_file(
        self,
        *,
        purpose: str,
        filename: str,
        bytes_size: int,
        storage_backend: str,
        storage_key: str,
        checksum: str | None = None,
        created_by_api_key: str | None = None,
        created_by_user_id: str | None = None,
        created_by_team_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> BatchFileRecord | None:
        if self.prisma is None:
            return None
        file_id = str(uuid4())
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_batch_file (
                file_id, purpose, filename, bytes, status, storage_backend, storage_key, checksum,
                created_by_api_key, created_by_user_id, created_by_team_id, expires_at
            )
            VALUES ($1, $2, $3, $4, 'processed', $5, $6, $7, $8, $9, $10, $11::timestamp)
            RETURNING *
            """,
            file_id,
            purpose,
            filename,
            bytes_size,
            storage_backend,
            storage_key,
            checksum,
            created_by_api_key,
            created_by_user_id,
            created_by_team_id,
            expires_at,
        )
        if not rows:
            return None
        return self._file_from_row(rows[0])

    async def get_file(self, file_id: str) -> BatchFileRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_file
            WHERE file_id = $1
            LIMIT 1
            """,
            file_id,
        )
        if not rows:
            return None
        return self._file_from_row(rows[0])

    async def create_job(
        self,
        *,
        endpoint: str,
        input_file_id: str,
        model: str | None,
        metadata: dict[str, Any] | None,
        created_by_api_key: str | None,
        created_by_user_id: str | None,
        created_by_team_id: str | None,
        expires_at: datetime | None,
        execution_mode: str = "managed_internal",
    ) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        batch_id = str(uuid4())
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_batch_job (
                batch_id, endpoint, status, execution_mode, input_file_id, model, metadata,
                created_by_api_key, created_by_user_id, created_by_team_id, expires_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11::timestamp)
            RETURNING *
            """,
            batch_id,
            endpoint,
            BatchJobStatus.VALIDATING,
            execution_mode,
            input_file_id,
            model,
            json.dumps(metadata or {}),
            created_by_api_key,
            created_by_user_id,
            created_by_team_id,
            expires_at,
        )
        if not rows:
            return None
        return self._job_from_row(rows[0])

    async def get_job(self, batch_id: str) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_job
            WHERE batch_id = $1
            LIMIT 1
            """,
            batch_id,
        )
        if not rows:
            return None
        return self._job_from_row(rows[0])

    async def list_jobs(
        self,
        *,
        limit: int = 20,
        after: datetime | None = None,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
    ) -> list[BatchJobRecord]:
        if self.prisma is None:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if after is not None:
            params.append(after)
            clauses.append(f"created_at > ${len(params)}")
        if created_by_api_key and created_by_team_id:
            params.append(created_by_api_key)
            api_key_idx = len(params)
            params.append(created_by_team_id)
            team_idx = len(params)
            clauses.append(f"(created_by_api_key = ${api_key_idx} OR created_by_team_id = ${team_idx})")
        elif created_by_api_key:
            params.append(created_by_api_key)
            clauses.append(f"created_by_api_key = ${len(params)}")
        elif created_by_team_id:
            params.append(created_by_team_id)
            clauses.append(f"created_by_team_id = ${len(params)}")
        params.append(max(1, min(limit, 200)))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.prisma.query_raw(
            f"""
            SELECT *
            FROM deltallm_batch_job
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        return [self._job_from_row(row) for row in rows]

    async def set_job_queued(self, batch_id: str, total_items: int) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET status = $2,
                total_items = $3,
                status_last_updated_at = NOW()
            WHERE batch_id = $1
            RETURNING *
            """,
            batch_id,
            BatchJobStatus.QUEUED,
            total_items,
        )
        if not rows:
            return None
        return self._job_from_row(rows[0])

    async def request_cancel(self, batch_id: str) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET cancel_requested_at = NOW(),
                status = CASE
                    WHEN status IN ('completed', 'failed', 'cancelled', 'expired') THEN status
                    ELSE 'in_progress'
                END,
                status_last_updated_at = NOW()
            WHERE batch_id = $1
            RETURNING *
            """,
            batch_id,
        )
        if not rows:
            return None
        return self._job_from_row(rows[0])

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

    async def claim_next_job(self, *, worker_id: str, lease_seconds: int = 30) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            WITH candidate AS (
                SELECT batch_id
                FROM deltallm_batch_job
                WHERE status IN ('queued', 'in_progress')
                  AND (lease_expires_at IS NULL OR lease_expires_at < NOW())
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE deltallm_batch_job j
            SET locked_by = $1,
                lease_expires_at = NOW() + ($2 || ' seconds')::interval,
                status = CASE
                    WHEN j.status = 'queued' THEN 'in_progress'
                    ELSE j.status
                END,
                started_at = COALESCE(j.started_at, NOW()),
                status_last_updated_at = NOW()
            FROM candidate
            WHERE j.batch_id = candidate.batch_id
            RETURNING j.*
            """,
            worker_id,
            lease_seconds,
        )
        if not rows:
            return None
        return self._job_from_row(rows[0])

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
                  AND status = 'pending'
                  AND (lease_expires_at IS NULL OR lease_expires_at < NOW())
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
        return [self._item_from_row(row) for row in rows]

    async def mark_item_completed(
        self,
        *,
        item_id: str,
        response_body: dict[str, Any],
        usage: dict[str, Any] | None,
        provider_cost: float,
        billed_cost: float,
    ) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
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
            """,
            item_id,
            json.dumps(response_body),
            json.dumps(usage or {}),
            provider_cost,
            billed_cost,
        )

    async def mark_item_failed(
        self,
        *,
        item_id: str,
        error_body: dict[str, Any],
        last_error: str,
        retryable: bool,
        retry_delay_seconds: int = 0,
    ) -> None:
        if self.prisma is None:
            return
        if retryable:
            await self.prisma.execute_raw(
                """
                UPDATE deltallm_batch_item
                SET status = 'pending',
                    error_body = $2::jsonb,
                    last_error = $3,
                    lease_expires_at = NOW() + ($4 || ' seconds')::interval,
                    locked_by = NULL
                WHERE item_id = $1
                """,
                item_id,
                json.dumps(error_body),
                last_error,
                max(0, retry_delay_seconds),
            )
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_batch_item
            SET status = 'failed',
                error_body = $2::jsonb,
                last_error = $3,
                lease_expires_at = NULL,
                locked_by = NULL,
                completed_at = NOW()
            WHERE item_id = $1
            """,
            item_id,
            json.dumps(error_body),
            last_error,
        )

    async def refresh_job_progress(self, batch_id: str) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            WITH stats AS (
                SELECT
                    COUNT(*)::int AS total_items,
                    COUNT(*) FILTER (WHERE status = 'pending')::int AS pending_items,
                    COUNT(*) FILTER (WHERE status = 'in_progress')::int AS in_progress_items,
                    COUNT(*) FILTER (WHERE status = 'completed')::int AS completed_items,
                    COUNT(*) FILTER (WHERE status = 'failed')::int AS failed_items,
                    COUNT(*) FILTER (WHERE status = 'cancelled')::int AS cancelled_items
                FROM deltallm_batch_item
                WHERE batch_id = $1
            )
            UPDATE deltallm_batch_job j
            SET total_items = s.total_items,
                in_progress_items = s.in_progress_items,
                completed_items = s.completed_items,
                failed_items = s.failed_items,
                cancelled_items = s.cancelled_items,
                status = CASE
                    WHEN j.status IN ('completed', 'failed', 'cancelled', 'expired') THEN j.status
                    WHEN j.cancel_requested_at IS NOT NULL AND s.pending_items = 0 AND s.in_progress_items = 0 THEN 'cancelled'
                    WHEN s.pending_items = 0 AND s.in_progress_items = 0 THEN 'completed'
                    WHEN s.in_progress_items > 0 OR s.completed_items > 0 OR s.failed_items > 0 THEN 'in_progress'
                    ELSE j.status
                END,
                completed_at = CASE
                    WHEN j.completed_at IS NOT NULL THEN j.completed_at
                    WHEN (
                        j.cancel_requested_at IS NOT NULL AND s.pending_items = 0 AND s.in_progress_items = 0
                    ) OR (s.pending_items = 0 AND s.in_progress_items = 0)
                    THEN NOW()
                    ELSE NULL
                END,
                status_last_updated_at = NOW()
            FROM stats s
            WHERE j.batch_id = $1
            RETURNING j.*
            """,
            batch_id,
        )
        if not rows:
            return None
        return self._job_from_row(rows[0])

    async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_batch_job
            SET lease_expires_at = NULL,
                locked_by = NULL
            WHERE batch_id = $1
              AND locked_by = $2
            """,
            batch_id,
            worker_id,
        )

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
        return [self._item_from_row(row) for row in rows]

    async def attach_artifacts_and_finalize(
        self,
        *,
        batch_id: str,
        output_file_id: str | None,
        error_file_id: str | None,
        final_status: str,
    ) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET output_file_id = $2,
                error_file_id = $3,
                status = $4,
                completed_at = COALESCE(completed_at, NOW()),
                lease_expires_at = NULL,
                locked_by = NULL,
                status_last_updated_at = NOW()
            WHERE batch_id = $1
            RETURNING *
            """,
            batch_id,
            output_file_id,
            error_file_id,
            final_status,
        )
        if not rows:
            return None
        return self._job_from_row(rows[0])

    async def list_expired_terminal_job_ids(self, *, now: datetime, limit: int = 100) -> list[str]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT batch_id
            FROM deltallm_batch_job
            WHERE expires_at IS NOT NULL
              AND expires_at < $1::timestamp
              AND status IN ('completed', 'failed', 'cancelled', 'expired')
            ORDER BY expires_at ASC
            LIMIT $2
            """,
            now,
            max(1, min(limit, 1000)),
        )
        return [str(row.get("batch_id") or "") for row in rows if row.get("batch_id")]

    async def delete_job_metadata(self, batch_id: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_batch_item
            WHERE batch_id = $1
            """,
            batch_id,
        )
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_batch_job
            WHERE batch_id = $1
            """,
            batch_id,
        )

    async def list_expired_unreferenced_files(self, *, now: datetime, limit: int = 100) -> list[BatchFileRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT f.*
            FROM deltallm_batch_file f
            WHERE f.expires_at IS NOT NULL
              AND f.expires_at < $1::timestamp
              AND NOT EXISTS (
                    SELECT 1
                    FROM deltallm_batch_job j
                    WHERE j.input_file_id = f.file_id
                       OR j.output_file_id = f.file_id
                       OR j.error_file_id = f.file_id
              )
            ORDER BY f.expires_at ASC
            LIMIT $2
            """,
            now,
            max(1, min(limit, 1000)),
        )
        return [self._file_from_row(row) for row in rows]

    async def delete_file(self, file_id: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_batch_file
            WHERE file_id = $1
            """,
            file_id,
        )
