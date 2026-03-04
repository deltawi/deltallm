from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.batch.models import BatchJobRecord, BatchJobStatus
from src.batch.repositories.mappers import job_from_row


class BatchJobRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

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
        return job_from_row(rows[0])

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
        return job_from_row(rows[0])

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
        return [job_from_row(row) for row in rows]

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
        return job_from_row(rows[0])

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
        return job_from_row(rows[0])

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
        return job_from_row(rows[0])

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
        return job_from_row(rows[0])

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
        return job_from_row(rows[0])
