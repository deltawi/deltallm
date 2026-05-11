from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.batch.models import (
    BatchJobRecord,
    BatchJobStatus,
    BatchModelBacklogRecord,
    BatchModelInFlightRecord,
    BatchWorkClaim,
    normalize_batch_job_status,
)
from src.batch.repositories.mappers import job_from_row, parse_datetime
from src.batch.scheduling import (
    API_KEY_TENANT_SCOPE_PREFIX,
    ESTIMATOR_VERSION,
    build_scheduling_dimensions,
    resolve_model_group,
    stable_tenant_scope_id,
)
from src.metrics import (
    increment_batch_item_reclaim,
    observe_batch_claim_wait_by_model,
    observe_batch_estimated_work_units,
    observe_batch_queue_wait,
)

logger = logging.getLogger(__name__)


class BatchJobRepository:
    def __init__(self, prisma_client: Any | None = None, *, model_group_resolver: Any | None = None) -> None:
        self.prisma = prisma_client
        self.model_group_resolver = model_group_resolver

    async def acquire_scope_advisory_lock(self, *, scope_type: str, scope_id: str) -> None:
        if self.prisma is None:
            return
        executor = getattr(self.prisma, "execute_raw", None)
        if callable(executor):
            await executor(
                """
                SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))
                """,
                scope_type,
                scope_id,
            )
            return
        await self.prisma.query_raw(
            """
            WITH scope_lock AS (
                SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))
            )
            SELECT 1::int AS locked FROM scope_lock
            """,
            scope_type,
            scope_id,
        )

    async def create_job(
        self,
        *,
        batch_id: str | None = None,
        endpoint: str,
        input_file_id: str,
        model: str | None,
        metadata: dict[str, Any] | None,
        created_by_api_key: str | None,
        created_by_user_id: str | None,
        created_by_team_id: str | None,
        created_by_organization_id: str | None = None,
        expires_at: datetime | None = None,
        execution_mode: str = "managed_internal",
        status: str | BatchJobStatus = BatchJobStatus.QUEUED,
        total_items: int = 0,
        scheduler_version: str | None = None,
        scheduling_model: str | None = None,
        scheduling_model_group: str | None = None,
        scheduling_endpoint: str | None = None,
        tenant_scope_type: str | None = None,
        tenant_scope_id: str | None = None,
        service_tier: str | None = None,
        estimated_work_units: int | None = None,
        remaining_work_units: int | None = None,
        size_class: str | None = None,
        queue_entered_at: datetime | None = None,
        scheduler_debug: dict[str, Any] | None = None,
    ) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        batch_id = str(batch_id or uuid4())
        normalized_status = normalize_batch_job_status(status)
        fallback_model = scheduling_model or model
        dimensions = build_scheduling_dimensions(
            endpoint=scheduling_endpoint or endpoint,
            model=fallback_model,
            model_group=scheduling_model_group
            or resolve_model_group(fallback_model, self.model_group_resolver),
            organization_id=created_by_organization_id,
            team_id=created_by_team_id,
            api_key=created_by_api_key,
            user_id=created_by_user_id,
            service_tier=service_tier,
            estimated_work_units=(
                max(0, int(estimated_work_units))
                if estimated_work_units is not None
                else max(0, int(total_items))
            ),
            remaining_work_units=remaining_work_units,
            scheduler_version=scheduler_version,
            estimator_version=ESTIMATOR_VERSION,
            scheduler_debug=scheduler_debug,
        )
        effective_size_class = size_class or dimensions.size_class
        effective_queue_entered_at = queue_entered_at or datetime.now(tz=UTC)
        effective_tenant_scope_type = tenant_scope_type or dimensions.tenant_scope_type
        effective_tenant_scope_id = tenant_scope_id or dimensions.tenant_scope_id
        if effective_tenant_scope_type == "api_key":
            effective_tenant_scope_id = stable_tenant_scope_id(
                scope_type=effective_tenant_scope_type,
                scope_id=effective_tenant_scope_id,
            )
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_batch_job (
                batch_id, endpoint, status, execution_mode, input_file_id, model, metadata,
                total_items, scheduler_version, scheduling_model, scheduling_model_group,
                scheduling_endpoint, tenant_scope_type, tenant_scope_id, service_tier,
                estimated_work_units, remaining_work_units, size_class, queue_entered_at,
                scheduler_debug, created_by_api_key, created_by_user_id, created_by_team_id,
                created_by_organization_id, expires_at
            )
            VALUES (
                $1,
                $2,
                $3::"DeltaLLM_BatchJobStatus",
                $4,
                $5,
                $6,
                $7::jsonb,
                $8,
                $9,
                $10,
                $11,
                $12,
                $13,
                $14,
                $15,
                $16,
                $17,
                $18,
                $19::timestamp,
                $20::jsonb,
                $21,
                $22,
                $23,
                $24,
                $25::timestamp
            )
            RETURNING *
            """,
            batch_id,
            endpoint,
            normalized_status.value,
            execution_mode,
            input_file_id,
            model,
            json.dumps(metadata or {}),
            max(0, int(total_items)),
            dimensions.scheduler_version,
            dimensions.scheduling_model,
            dimensions.scheduling_model_group,
            dimensions.scheduling_endpoint,
            effective_tenant_scope_type,
            effective_tenant_scope_id,
            dimensions.service_tier,
            dimensions.estimated_work_units,
            dimensions.remaining_work_units,
            effective_size_class,
            effective_queue_entered_at,
            json.dumps(dimensions.scheduler_debug or {}),
            created_by_api_key,
            created_by_user_id,
            created_by_team_id,
            created_by_organization_id,
            expires_at,
        )
        if not rows:
            return None
        record = job_from_row(rows[0])
        observe_batch_estimated_work_units(work_units=record.estimated_work_units)
        return record

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
        created_by_organization_id: str | None = None,
    ) -> list[BatchJobRecord]:
        if self.prisma is None:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if after is not None:
            params.append(after)
            clauses.append(f"created_at > ${len(params)}")
        if created_by_api_key and created_by_team_id and created_by_organization_id:
            params.append(created_by_api_key)
            api_key_idx = len(params)
            params.append(created_by_team_id)
            team_idx = len(params)
            params.append(created_by_organization_id)
            org_idx = len(params)
            clauses.append(
                f"(created_by_api_key = ${api_key_idx} OR created_by_team_id = ${team_idx} OR created_by_organization_id = ${org_idx})"
            )
        elif created_by_api_key and created_by_team_id:
            params.append(created_by_api_key)
            api_key_idx = len(params)
            params.append(created_by_team_id)
            team_idx = len(params)
            clauses.append(f"(created_by_api_key = ${api_key_idx} OR created_by_team_id = ${team_idx})")
        elif created_by_api_key and created_by_organization_id:
            params.append(created_by_api_key)
            api_key_idx = len(params)
            params.append(created_by_organization_id)
            org_idx = len(params)
            clauses.append(f"(created_by_api_key = ${api_key_idx} OR created_by_organization_id = ${org_idx})")
        elif created_by_team_id and created_by_organization_id:
            params.append(created_by_team_id)
            team_idx = len(params)
            params.append(created_by_organization_id)
            org_idx = len(params)
            clauses.append(f"(created_by_team_id = ${team_idx} OR created_by_organization_id = ${org_idx})")
        elif created_by_api_key:
            params.append(created_by_api_key)
            clauses.append(f"created_by_api_key = ${len(params)}")
        elif created_by_team_id:
            params.append(created_by_team_id)
            clauses.append(f"created_by_team_id = ${len(params)}")
        elif created_by_organization_id:
            params.append(created_by_organization_id)
            clauses.append(f"created_by_organization_id = ${len(params)}")
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

    async def count_active_jobs_for_scope(
        self,
        *,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
    ) -> int:
        if self.prisma is None:
            return 0
        terminal = "'completed', 'failed', 'cancelled', 'expired'"
        if created_by_team_id:
            rows = await self.prisma.query_raw(
                f"""
                SELECT COUNT(*) AS total
                FROM deltallm_batch_job
                WHERE created_by_team_id = $1
                  AND status NOT IN ({terminal})
                """,
                created_by_team_id,
            )
        elif created_by_api_key:
            rows = await self.prisma.query_raw(
                f"""
                SELECT COUNT(*) AS total
                FROM deltallm_batch_job
                WHERE created_by_api_key = $1
                  AND status NOT IN ({terminal})
                """,
                created_by_api_key,
            )
        else:
            return 0
        return int((rows[0] if rows else {}).get("total") or 0)

    async def summarize_runtime_statuses(self) -> dict[str, int]:
        if self.prisma is None:
            return {"queued": 0, "in_progress": 0, "finalizing": 0}
        rows = await self.prisma.query_raw(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'queued') AS queued,
                COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress,
                COUNT(*) FILTER (WHERE status = 'finalizing') AS finalizing
            FROM deltallm_batch_job
            """
        )
        row = dict(rows[0]) if rows else {}
        return {
            "queued": int(row.get("queued") or 0),
            "in_progress": int(row.get("in_progress") or 0),
            "finalizing": int(row.get("finalizing") or 0),
        }

    async def summarize_scheduler_queues(self, *, now: datetime) -> dict[str, Any]:
        if self.prisma is None:
            return {
                "scheduler_queue_rows": [],
                "scheduler_missing_dimensions": {},
            }
        queue_rows = await self.prisma.query_raw(
            """
            SELECT
                status::text AS status,
                COALESCE(NULLIF(scheduling_model_group, ''), 'unknown') AS model_group,
                COALESCE(NULLIF(tenant_scope_type, ''), 'unknown') AS tenant_scope_type,
                COALESCE(NULLIF(service_tier, ''), 'standard') AS service_tier,
                COALESCE(NULLIF(size_class, ''), 'unknown') AS size_class,
                COUNT(*)::int AS jobs,
                COALESCE(SUM(remaining_work_units), 0)::int AS work_units,
                COALESCE(EXTRACT(EPOCH FROM ($1::timestamp - MIN(queue_entered_at))), 0) AS oldest_job_age_seconds
            FROM deltallm_batch_job
            WHERE status IN ('queued', 'in_progress', 'finalizing')
            GROUP BY status, model_group, tenant_scope_type, service_tier, size_class
            """,
            now,
        )
        missing_rows = await self.prisma.query_raw(
            """
            SELECT
                COUNT(*) FILTER (WHERE scheduling_model IS NULL OR scheduling_model = '') AS scheduling_model,
                COUNT(*) FILTER (WHERE scheduling_model_group IS NULL OR scheduling_model_group = '') AS scheduling_model_group,
                COUNT(*) FILTER (WHERE scheduling_endpoint IS NULL OR scheduling_endpoint = '') AS scheduling_endpoint,
                COUNT(*) FILTER (WHERE tenant_scope_type IS NULL OR tenant_scope_type = '') AS tenant_scope_type,
                COUNT(*) FILTER (WHERE tenant_scope_id IS NULL OR tenant_scope_id = '') AS tenant_scope_id,
                COUNT(*) FILTER (WHERE queue_entered_at IS NULL) AS queue_entered_at
            FROM deltallm_batch_job
            WHERE status IN ('queued', 'in_progress', 'finalizing')
            """
        )
        missing_row = dict(missing_rows[0]) if missing_rows else {}
        return {
            "scheduler_queue_rows": [dict(row) for row in queue_rows],
            "scheduler_missing_dimensions": {
                str(key): int(value or 0)
                for key, value in missing_row.items()
            },
        }

    async def set_job_queued(self, batch_id: str, total_items: int) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET status = $2::"DeltaLLM_BatchJobStatus",
                total_items = $3,
                scheduler_version = COALESCE(scheduler_version, 'fifo_v1'),
                scheduling_model = COALESCE(scheduling_model, model),
                scheduling_model_group = COALESCE(scheduling_model_group, model),
                scheduling_endpoint = COALESCE(scheduling_endpoint, endpoint),
                tenant_scope_type = COALESCE(
                    NULLIF(tenant_scope_type, ''),
                    CASE
                    WHEN created_by_organization_id IS NOT NULL AND created_by_organization_id <> '' THEN 'organization'
                    WHEN created_by_team_id IS NOT NULL AND created_by_team_id <> '' THEN 'team'
                    WHEN created_by_api_key IS NOT NULL AND created_by_api_key <> '' THEN 'api_key'
                    WHEN created_by_user_id IS NOT NULL AND created_by_user_id <> '' THEN 'user'
                    ELSE 'anonymous'
                    END
                ),
                tenant_scope_id = COALESCE(
                    NULLIF(tenant_scope_id, ''),
                    CASE
                    WHEN created_by_organization_id IS NOT NULL AND created_by_organization_id <> '' THEN created_by_organization_id
                    WHEN created_by_team_id IS NOT NULL AND created_by_team_id <> '' THEN created_by_team_id
                    WHEN created_by_api_key IS NOT NULL AND created_by_api_key <> '' THEN NULL
                    WHEN created_by_user_id IS NOT NULL AND created_by_user_id <> '' THEN created_by_user_id
                    ELSE 'anonymous'
                    END
                ),
                estimated_work_units = CASE
                    WHEN estimated_work_units <= 0 THEN GREATEST($3, 0)
                    ELSE estimated_work_units
                    END,
                remaining_work_units = CASE
                    WHEN remaining_work_units <= 0 THEN GREATEST($3, 0)
                    ELSE remaining_work_units
                    END,
                size_class = CASE
                    WHEN GREATEST(estimated_work_units, $3, 0) <= 10 THEN 'xs'
                    WHEN GREATEST(estimated_work_units, $3, 0) <= 100 THEN 's'
                    WHEN GREATEST(estimated_work_units, $3, 0) <= 1000 THEN 'm'
                    WHEN GREATEST(estimated_work_units, $3, 0) <= 10000 THEN 'l'
                    ELSE 'xl'
                    END,
                queue_entered_at = COALESCE(queue_entered_at, NOW()),
                status_last_updated_at = NOW()
            WHERE batch_id = $1
            RETURNING *
            """,
            batch_id,
            BatchJobStatus.QUEUED.value,
            total_items,
        )
        if not rows:
            return None
        record = job_from_row(rows[0])
        if record.tenant_scope_type != "api_key":
            return record

        existing_scope_id = str(record.tenant_scope_id or "").strip()
        if existing_scope_id.startswith(API_KEY_TENANT_SCOPE_PREFIX):
            return record

        raw_scope_id = existing_scope_id or str(record.created_by_api_key or "").strip()
        if not raw_scope_id:
            return record

        stable_scope_id = stable_tenant_scope_id(scope_type="api_key", scope_id=raw_scope_id)
        repaired_rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET tenant_scope_id = $2
            WHERE batch_id = $1
              AND tenant_scope_type = 'api_key'
              AND (
                  tenant_scope_id IS NULL
                  OR tenant_scope_id = ''
                  OR tenant_scope_id NOT LIKE $3
              )
            RETURNING *
            """,
            record.batch_id,
            stable_scope_id,
            f"{API_KEY_TENANT_SCOPE_PREFIX}%",
        )
        if not repaired_rows:
            return record
        return job_from_row(repaired_rows[0])

    async def request_cancel(self, batch_id: str) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET cancel_requested_at = NOW(),
                status = (
                    CASE
                    WHEN status IN ('completed', 'failed', 'cancelled', 'expired') THEN status
                    WHEN status = 'finalizing' THEN status
                    ELSE 'in_progress'
                    END
                )::"DeltaLLM_BatchJobStatus",
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
                SELECT
                    batch_id,
                    status AS previous_status,
                    first_claimed_at AS previous_first_claimed_at
                FROM deltallm_batch_job
                WHERE status IN ('queued', 'in_progress', 'finalizing')
                  AND (lease_expires_at IS NULL OR lease_expires_at < NOW())
                ORDER BY CASE WHEN status = 'finalizing' THEN 1 ELSE 0 END, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE deltallm_batch_job j
            SET locked_by = $1,
                lease_expires_at = NOW() + ($2 || ' seconds')::interval,
                status = (
                    CASE
                    WHEN j.status = 'queued' THEN 'in_progress'
                    ELSE j.status
                    END
                )::"DeltaLLM_BatchJobStatus",
                started_at = COALESCE(j.started_at, NOW()),
                first_claimed_at = COALESCE(j.first_claimed_at, NOW()),
                last_claimed_at = NOW(),
                last_scheduled_at = NOW(),
                status_last_updated_at = NOW()
            FROM candidate
            WHERE j.batch_id = candidate.batch_id
            RETURNING j.*, candidate.previous_status, candidate.previous_first_claimed_at
            """,
            worker_id,
            lease_seconds,
        )
        if not rows:
            return None
        row = dict(rows[0])
        record = job_from_row(row)
        previous_status = str(row.get("previous_status") or "")
        previous_first_claimed_at = parse_datetime(row.get("previous_first_claimed_at"))
        if (
            previous_status == BatchJobStatus.QUEUED.value
            and previous_first_claimed_at is None
            and record.queue_entered_at is not None
        ):
            observe_batch_queue_wait(
                model_group=record.scheduling_model_group or "unknown",
                service_tier=record.service_tier,
                size_class=record.size_class,
                wait_seconds=max(
                    0.0,
                    (datetime.now(tz=UTC) - record.queue_entered_at).total_seconds(),
                ),
            )
        return record

    async def claim_next_finalization(self, *, worker_id: str, lease_seconds: int = 30) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            WITH candidate AS (
                SELECT batch_id
                FROM deltallm_batch_job
                WHERE status = 'finalizing'
                  AND (lease_expires_at IS NULL OR lease_expires_at < NOW())
                ORDER BY COALESCE(queue_entered_at, created_at) ASC,
                         created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE deltallm_batch_job j
            SET locked_by = $1,
                lease_expires_at = NOW() + ($2 || ' seconds')::interval,
                last_claimed_at = NOW(),
                last_scheduled_at = NOW(),
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

    async def list_model_group_backlog(self) -> list[BatchModelBacklogRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH runnable_jobs AS (
                SELECT
                    j.batch_id,
                    COALESCE(NULLIF(j.scheduling_model_group, ''), 'unknown') AS model_group,
                    COALESCE(NULLIF(j.service_tier, ''), 'standard') AS service_tier,
                    COALESCE(NULLIF(j.size_class, ''), 'unknown') AS size_class,
                    GREATEST(COALESCE(j.remaining_work_units, j.estimated_work_units, j.total_items, 0), 0)::int
                        AS remaining_work_units,
                    COALESCE(j.queue_entered_at, j.created_at) AS queue_entered_at
                FROM deltallm_batch_job j
                WHERE j.status IN ('queued', 'in_progress')
                  AND (j.locked_by IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at < NOW())
                  AND j.scheduling_model_group IS NOT NULL
                  AND j.scheduling_model_group <> ''
                  AND EXISTS (
                      SELECT 1
                      FROM deltallm_batch_item i
                      WHERE i.batch_id = j.batch_id
                        AND (
                            (
                                i.status = 'pending'
                                AND (i.lease_expires_at IS NULL OR i.lease_expires_at < NOW())
                                AND (i.not_before_at IS NULL OR i.not_before_at <= NOW())
                            )
                            OR (i.status = 'in_progress' AND i.lease_expires_at < NOW())
                        )
                  )
            )
            SELECT
                model_group,
                service_tier,
                size_class,
                COUNT(*)::int AS queued_jobs,
                COALESCE(SUM(remaining_work_units), 0)::int AS queued_work_units,
                MIN(queue_entered_at) AS oldest_queue_entered_at
            FROM runnable_jobs
            GROUP BY model_group, service_tier, size_class
            ORDER BY MIN(queue_entered_at) ASC, model_group ASC, service_tier ASC, size_class ASC
            """,
        )
        records: list[BatchModelBacklogRecord] = []
        for row in rows:
            row_dict = dict(row)
            records.append(
                BatchModelBacklogRecord(
                    model_group=str(row_dict.get("model_group") or "unknown"),
                    service_tier=str(row_dict.get("service_tier") or "standard"),
                    size_class=str(row_dict.get("size_class") or "unknown"),
                    queued_jobs=int(row_dict.get("queued_jobs") or 0),
                    queued_work_units=int(row_dict.get("queued_work_units") or 0),
                    oldest_queue_entered_at=parse_datetime(row_dict.get("oldest_queue_entered_at")),
                )
            )
        return records

    async def list_model_group_in_flight(self) -> list[BatchModelInFlightRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                COALESCE(NULLIF(i.scheduling_model_group, ''), NULLIF(j.scheduling_model_group, ''), 'unknown')
                    AS model_group,
                COALESCE(NULLIF(j.service_tier, ''), 'standard') AS service_tier,
                COUNT(*)::int AS in_flight_items,
                COALESCE(SUM(GREATEST(COALESCE(i.estimated_work_units, 1), 1)), 0)::int
                    AS in_flight_work_units
            FROM deltallm_batch_item i
            JOIN deltallm_batch_job j ON j.batch_id = i.batch_id
            WHERE i.status = 'in_progress'
              AND (i.lease_expires_at IS NULL OR i.lease_expires_at > NOW())
              AND COALESCE(NULLIF(i.scheduling_model_group, ''), NULLIF(j.scheduling_model_group, '')) IS NOT NULL
            GROUP BY model_group, service_tier
            ORDER BY model_group ASC, service_tier ASC
            """,
        )
        records: list[BatchModelInFlightRecord] = []
        for row in rows:
            row_dict = dict(row)
            records.append(
                BatchModelInFlightRecord(
                    model_group=str(row_dict.get("model_group") or "unknown"),
                    service_tier=str(row_dict.get("service_tier") or "standard"),
                    in_flight_items=int(row_dict.get("in_flight_items") or 0),
                    in_flight_work_units=int(row_dict.get("in_flight_work_units") or 0),
                )
            )
        return records

    async def claim_next_work(
        self,
        *,
        worker_id: str,
        max_items: int,
        max_work_units: int,
        lease_seconds: int,
        allowed_model_groups: list[str] | None = None,
        service_tier: str | None = None,
        legacy_only: bool = False,
        claim_order: str = "round_robin",
        capacity_model_group: str | None = None,
        capacity_service_tier: str | None = None,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
        allow_oversized_first_item: bool = True,
    ) -> BatchWorkClaim | None:
        if self.prisma is None:
            return None
        normalized_capacity_model_group = str(capacity_model_group or "").strip()
        normalized_capacity_service_tier = (
            str(capacity_service_tier or service_tier or "standard").strip() or "standard"
        )
        if capacity_max_in_flight_items is not None and normalized_capacity_model_group:
            capacity_max_in_flight = max(0, int(capacity_max_in_flight_items))
            if capacity_max_in_flight <= 0:
                return None
            capacity_max_work_units = (
                max(0, int(capacity_max_in_flight_work_units))
                if capacity_max_in_flight_work_units is not None
                else None
            )
            if capacity_max_work_units is not None and capacity_max_work_units <= 0:
                return None
            tx_factory = getattr(self.prisma, "tx", None)
            if not callable(tx_factory):
                logger.warning(
                    "batch capacity claim requires transaction-capable prisma client model_group=%s",
                    normalized_capacity_model_group,
                )
                return None
            async with tx_factory() as tx:
                await self._acquire_model_capacity_lock(
                    tx,
                    model_group=normalized_capacity_model_group,
                    service_tier=normalized_capacity_service_tier,
                )
                return await self._claim_next_work_with_client(
                    tx,
                    worker_id=worker_id,
                    max_items=max_items,
                    max_work_units=max_work_units,
                    lease_seconds=lease_seconds,
                    allowed_model_groups=allowed_model_groups,
                    service_tier=service_tier,
                    legacy_only=legacy_only,
                    claim_order=claim_order,
                    capacity_model_group=normalized_capacity_model_group,
                    capacity_service_tier=normalized_capacity_service_tier,
                    capacity_max_in_flight_items=capacity_max_in_flight,
                    capacity_max_in_flight_work_units=capacity_max_work_units,
                    allow_oversized_first_item=allow_oversized_first_item,
                )
        return await self._claim_next_work_with_client(
            self.prisma,
            worker_id=worker_id,
            max_items=max_items,
            max_work_units=max_work_units,
            lease_seconds=lease_seconds,
            allowed_model_groups=allowed_model_groups,
            service_tier=service_tier,
            legacy_only=legacy_only,
            claim_order=claim_order,
            capacity_model_group=capacity_model_group,
            capacity_service_tier=capacity_service_tier,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
            allow_oversized_first_item=allow_oversized_first_item,
        )

    async def _acquire_model_capacity_lock(
        self,
        db: Any,
        *,
        model_group: str,
        service_tier: str,
    ) -> None:
        await db.query_raw(
            """
            WITH capacity_lock AS (
                SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))
            )
            SELECT 1::int AS locked FROM capacity_lock
            """,
            model_group,
            service_tier,
        )

    async def _claim_next_work_with_client(
        self,
        db: Any,
        *,
        worker_id: str,
        max_items: int,
        max_work_units: int,
        lease_seconds: int,
        allowed_model_groups: list[str] | None = None,
        service_tier: str | None = None,
        legacy_only: bool = False,
        claim_order: str = "round_robin",
        capacity_model_group: str | None = None,
        capacity_service_tier: str | None = None,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
        allow_oversized_first_item: bool = True,
    ) -> BatchWorkClaim | None:
        bounded_max_items = max(1, min(int(max_items), 200))
        bounded_max_work_units = max(1, int(max_work_units))
        normalized_model_groups = [
            str(model_group).strip()
            for model_group in (allowed_model_groups or [])
            if str(model_group or "").strip()
        ]
        params: list[Any] = [
            worker_id,
            bounded_max_items,
            bounded_max_work_units,
            lease_seconds,
        ]
        model_group_filter_sql = ""
        if normalized_model_groups:
            params.append(normalized_model_groups)
            model_group_filter_sql = f"AND j.scheduling_model_group = ANY(${len(params)}::text[])"
        service_tier_filter_sql = ""
        normalized_service_tier = str(service_tier or "").strip()
        if normalized_service_tier:
            params.append(normalized_service_tier)
            service_tier_filter_sql = f"AND j.service_tier = ${len(params)}"
        legacy_filter_sql = ""
        if legacy_only:
            legacy_filter_sql = """
                  AND COALESCE(NULLIF(j.scheduler_version, ''), 'fifo_v1') = 'fifo_v1'
                  AND (j.scheduling_model_group IS NULL OR j.scheduling_model_group = '')
            """
        capacity_cte_sql = ""
        capacity_filter_sql = ""
        locked_items_limit_sql = "$2"
        work_unit_claim_limit_sql = "$3"
        normalized_capacity_model_group = str(capacity_model_group or "").strip()
        normalized_capacity_service_tier = (
            str(capacity_service_tier or service_tier or "standard").strip() or "standard"
        )
        if capacity_max_in_flight_items is not None and normalized_capacity_model_group:
            capacity_max_in_flight = max(0, int(capacity_max_in_flight_items))
            params.append(normalized_capacity_model_group)
            capacity_model_param = len(params)
            params.append(normalized_capacity_service_tier)
            capacity_service_tier_param = len(params)
            params.append(capacity_max_in_flight)
            capacity_max_in_flight_param = len(params)
            remaining_work_units_sql = "$3::int"
            if capacity_max_in_flight_work_units is not None:
                capacity_max_work_units = max(0, int(capacity_max_in_flight_work_units))
                params.append(capacity_max_work_units)
                capacity_max_work_units_param = len(params)
                remaining_work_units_sql = (
                    f"GREATEST(${capacity_max_work_units_param}::int "
                    "- COALESCE(capacity_usage.in_flight_work_units, 0), 0)::int"
                )
            capacity_cte_sql = f"""
            capacity_usage AS (
                SELECT
                    COUNT(*)::int AS in_flight_items,
                    COALESCE(SUM(GREATEST(COALESCE(capacity_item.estimated_work_units, 1), 1)), 0)::int
                        AS in_flight_work_units
                FROM deltallm_batch_item capacity_item
                JOIN deltallm_batch_job capacity_job
                  ON capacity_job.batch_id = capacity_item.batch_id
                WHERE capacity_item.status = 'in_progress'
                  AND (
                      capacity_item.lease_expires_at IS NULL
                      OR capacity_item.lease_expires_at > NOW()
                  )
                  AND COALESCE(
                      NULLIF(capacity_item.scheduling_model_group, ''),
                      NULLIF(capacity_job.scheduling_model_group, '')
                  ) = ${capacity_model_param}
                  AND COALESCE(NULLIF(capacity_job.service_tier, ''), 'standard')
                      = ${capacity_service_tier_param}
            ),
            capacity_state AS (
                SELECT
                    GREATEST(
                        ${capacity_max_in_flight_param}::int - COALESCE(capacity_usage.in_flight_items, 0),
                        0
                    )::int AS remaining_slots,
                    {remaining_work_units_sql} AS remaining_work_units
                FROM capacity_usage
            ),
            """
            capacity_filter_sql = """
                  AND (SELECT remaining_slots FROM capacity_state) > 0
                  AND (SELECT remaining_work_units FROM capacity_state) > 0
            """
            locked_items_limit_sql = "LEAST($2, (SELECT remaining_slots FROM capacity_state))"
            work_unit_claim_limit_sql = "LEAST($3, (SELECT remaining_work_units FROM capacity_state))"
        if claim_order == "fifo":
            job_order_sql = """
                ORDER BY COALESCE(j.queue_entered_at, j.created_at) ASC,
                         j.created_at ASC,
                         j.batch_id ASC
            """
        else:
            job_order_sql = """
                -- last_scheduled_at NULLS FIRST keeps never-scheduled jobs
                -- ahead of jobs that have already taken a slice (round-robin
                -- against head-of-line); among scheduled jobs, oldest first.
                -- COALESCE keeps the FIFO tiebreak working during the
                -- queue_entered_at backfill window.
                ORDER BY j.last_scheduled_at ASC NULLS FIRST,
                         COALESCE(j.queue_entered_at, j.created_at) ASC,
                         j.created_at ASC
            """
        work_unit_filter_sql = f"cumulative_work_units <= {work_unit_claim_limit_sql}"
        selected_job_head_work_filter_sql = "TRUE"
        if allow_oversized_first_item:
            work_unit_filter_sql = f"{work_unit_filter_sql} OR claim_rank = 1"
        else:
            selected_job_head_work_filter_sql = f"head_item.estimated_work_units <= {work_unit_claim_limit_sql}"
        with_selected_job_sql = f"WITH {capacity_cte_sql}selected_job AS"
        # selected_job picks one job under FOR KEY SHARE so multiple workers can
        # slice the same job concurrently. The CTE chain runs in a single
        # statement, so two workers can both hold FOR KEY SHARE on the same row;
        # when each later upgrades to FOR NO KEY UPDATE for the updated_job
        # UPDATE, Postgres serializes the upgrades (FOR NO KEY UPDATE is
        # compatible with another tx's FOR KEY SHARE per the row-lock matrix),
        # so workers do not deadlock — they take turns updating the job row
        # while their item slices stay disjoint via FOR UPDATE SKIP LOCKED.
        #
        # updated_items derives from updated_job (FROM ... updated_job uj) so
        # the data-modifying CTEs are forced to execute in order. If
        # updated_job's WHERE filter fails (status flipped to 'finalizing'
        # between snapshot and UPDATE), updated_job's RETURNING is empty, the
        # join produces no rows, and no item is flipped to in_progress.
        rows = await db.query_raw(
            f"""
            {with_selected_job_sql} (
                SELECT
                    j.batch_id,
                    j.status AS previous_status
                FROM deltallm_batch_job j
                WHERE j.status IN ('queued', 'in_progress')
                  AND (j.locked_by IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at < NOW())
                  {model_group_filter_sql}
                  {service_tier_filter_sql}
                  {legacy_filter_sql}
                  {capacity_filter_sql}
                  AND EXISTS (
                      SELECT 1
                      FROM (
                          -- The head runnable item controls whether this job
                          -- can produce a bounded claim. Capacity mode disables
                          -- the Phase 2 first-item oversize fallback, so jobs
                          -- with an oversized head item must not block newer
                          -- eligible jobs for the same model group.
                          SELECT GREATEST(COALESCE(i.estimated_work_units, 1), 1)::int
                              AS estimated_work_units
                          FROM deltallm_batch_item i
                          WHERE i.batch_id = j.batch_id
                            AND (
                                (
                                    i.status = 'pending'
                                    AND (i.lease_expires_at IS NULL OR i.lease_expires_at < NOW())
                                    AND (i.not_before_at IS NULL OR i.not_before_at <= NOW())
                                )
                                OR (i.status = 'in_progress' AND i.lease_expires_at < NOW())
                            )
                          ORDER BY i.line_number ASC
                          LIMIT 1
                      ) head_item
                      WHERE {selected_job_head_work_filter_sql}
                  )
                {job_order_sql}
                FOR KEY SHARE SKIP LOCKED
                LIMIT 1
            ),
            locked_items AS (
                SELECT
                    i.item_id,
                    i.status AS previous_status,
                    i.line_number,
                    GREATEST(COALESCE(i.estimated_work_units, 1), 1)::int AS estimated_work_units
                FROM selected_job sj
                JOIN deltallm_batch_item i ON i.batch_id = sj.batch_id
                -- Same runnable-item predicate as selected_job's EXISTS.
                WHERE (
                    (
                        i.status = 'pending'
                        AND (i.lease_expires_at IS NULL OR i.lease_expires_at < NOW())
                        AND (i.not_before_at IS NULL OR i.not_before_at <= NOW())
                    )
                    OR (i.status = 'in_progress' AND i.lease_expires_at < NOW())
                )
                ORDER BY i.line_number ASC
                FOR UPDATE SKIP LOCKED
                LIMIT {locked_items_limit_sql}
            ),
            candidate_items AS (
                SELECT
                    item_id,
                    previous_status,
                    line_number,
                    estimated_work_units,
                    ROW_NUMBER() OVER (ORDER BY line_number ASC) AS claim_rank,
                    SUM(estimated_work_units) OVER (ORDER BY line_number ASC) AS cumulative_work_units
                FROM locked_items
            ),
            eligible_items AS (
                -- locked_items already enforces the max-items cap via LIMIT $2
                -- and any capacity slot cap. Here we enforce the work-unit cap,
                -- with the Phase 2 first-item fallback only when enabled.
                SELECT item_id, previous_status, line_number, estimated_work_units
                FROM candidate_items
                WHERE {work_unit_filter_sql}
            ),
            updated_job AS (
                UPDATE deltallm_batch_job j
                SET status = (
                        CASE
                        WHEN j.status = 'queued' THEN 'in_progress'
                        ELSE j.status
                        END
                    )::"DeltaLLM_BatchJobStatus",
                    started_at = COALESCE(j.started_at, NOW()),
                    first_claimed_at = COALESCE(j.first_claimed_at, NOW()),
                    last_claimed_at = NOW(),
                    last_scheduled_at = NOW(),
                    locked_by = NULL,
                    lease_expires_at = NULL,
                    status_last_updated_at = NOW()
                FROM selected_job sj
                WHERE j.batch_id = sj.batch_id
                  AND j.status IN ('queued', 'in_progress')
                  AND (j.locked_by IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at < NOW())
                  AND EXISTS (SELECT 1 FROM eligible_items)
                RETURNING
                    j.batch_id,
                    j.endpoint,
                    j.scheduling_model_group,
                    j.tenant_scope_type,
                    j.tenant_scope_id,
                    j.service_tier,
                    j.size_class,
                    j.queue_entered_at,
                    sj.previous_status,
                    (j.first_claimed_at = j.last_claimed_at) AS queue_wait_observed
            ),
            updated_items AS (
                UPDATE deltallm_batch_item i
                SET status = 'in_progress',
                    locked_by = $1,
                    lease_expires_at = NOW() + ($4 || ' seconds')::interval,
                    attempts = attempts + 1,
                    started_at = COALESCE(i.started_at, NOW()),
                    not_before_at = NULL,
                    last_scheduled_at = NOW()
                FROM eligible_items s, updated_job uj
                WHERE i.item_id = s.item_id
                  AND i.batch_id = uj.batch_id
                RETURNING
                    i.item_id,
                    i.line_number,
                    i.estimated_work_units,
                    i.lease_expires_at,
                    s.previous_status
            )
            SELECT
                j.batch_id,
                j.endpoint,
                j.scheduling_model_group AS model_group,
                j.tenant_scope_type,
                j.tenant_scope_id,
                j.service_tier,
                j.size_class,
                j.queue_entered_at,
                j.previous_status,
                j.queue_wait_observed,
                ARRAY_AGG(u.item_id ORDER BY u.line_number ASC) AS item_ids,
                COALESCE(SUM(GREATEST(COALESCE(u.estimated_work_units, 1), 1)), 0)::int AS claimed_work_units,
                MAX(u.lease_expires_at) AS lease_expires_at,
                COUNT(*) FILTER (WHERE u.previous_status = 'in_progress')::int AS reclaimed_items
            FROM updated_job j
            JOIN updated_items u ON TRUE
            GROUP BY
                j.batch_id,
                j.endpoint,
                j.scheduling_model_group,
                j.tenant_scope_type,
                j.tenant_scope_id,
                j.service_tier,
                j.size_class,
                j.queue_entered_at,
                j.previous_status,
                j.queue_wait_observed
            """,
            *params,
        )
        if not rows:
            return None
        row = dict(rows[0])
        item_ids_value = row.get("item_ids") or []
        if isinstance(item_ids_value, str):
            item_ids = [value for value in item_ids_value.strip("{}").split(",") if value]
        else:
            item_ids = [str(item_id) for item_id in item_ids_value]
        if not item_ids:
            return None

        parsed_lease = parse_datetime(row.get("lease_expires_at"))
        if parsed_lease is None:
            logger.error(
                "batch work claim missing lease_expires_at batch_id=%s item_count=%s",
                row.get("batch_id"),
                len(item_ids),
            )
            return None

        reclaimed_count = int(row.get("reclaimed_items") or 0)
        for _ in range(reclaimed_count):
            increment_batch_item_reclaim()

        queue_entered_at = parse_datetime(row.get("queue_entered_at"))
        if bool(row.get("queue_wait_observed")) and queue_entered_at is not None:
            wait_seconds = max(
                0.0,
                (datetime.now(tz=UTC) - queue_entered_at).total_seconds(),
            )
            observe_batch_queue_wait(
                model_group=str(row.get("model_group") or "unknown"),
                service_tier=str(row.get("service_tier") or "standard"),
                size_class=str(row.get("size_class") or "unknown"),
                wait_seconds=wait_seconds,
            )
            observe_batch_claim_wait_by_model(
                model_group=str(row.get("model_group") or "unknown"),
                service_tier=str(row.get("service_tier") or "standard"),
                wait_seconds=wait_seconds,
            )

        return BatchWorkClaim(
            claim_id=str(uuid4()),
            worker_id=worker_id,
            batch_id=str(row["batch_id"]),
            endpoint=str(row.get("endpoint") or ""),
            model_group=row.get("model_group"),
            tenant_scope_type=row.get("tenant_scope_type"),
            tenant_scope_id=row.get("tenant_scope_id"),
            service_tier=str(row.get("service_tier") or "standard"),
            item_ids=item_ids,
            claimed_work_units=int(row.get("claimed_work_units") or len(item_ids)),
            lease_expires_at=parsed_lease,
        )

    async def diagnose_model_group_work_claim_empty(
        self,
        *,
        model_group: str,
        service_tier: str,
        max_work_units: int,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
    ) -> str:
        if self.prisma is None:
            return "empty_after_selection"
        normalized_model_group = str(model_group or "").strip()
        normalized_service_tier = str(service_tier or "standard").strip() or "standard"
        bounded_max_work_units = max(1, int(max_work_units))
        if not normalized_model_group:
            return "empty_after_selection"
        if capacity_max_in_flight_items is not None and int(capacity_max_in_flight_items) <= 0:
            return "capacity_full_after_lock"
        if capacity_max_in_flight_work_units is not None and int(capacity_max_in_flight_work_units) <= 0:
            return "capacity_work_units_full_after_lock"

        params: list[Any] = [normalized_model_group, normalized_service_tier, bounded_max_work_units]
        head_work_threshold_sql = "$3"
        if capacity_max_in_flight_work_units is not None:
            params.append(max(0, int(capacity_max_in_flight_work_units)))
            capacity_max_work_units_param = len(params)
            head_work_threshold_sql = (
                f"LEAST($3, GREATEST(${capacity_max_work_units_param}::int "
                "- COALESCE((SELECT in_flight_work_units FROM in_flight), 0), 0))"
            )

        rows = await self.prisma.query_raw(
            f"""
            WITH in_flight AS (
                SELECT
                    COUNT(*)::int AS in_flight_items,
                    COALESCE(SUM(GREATEST(COALESCE(i.estimated_work_units, 1), 1)), 0)::int
                        AS in_flight_work_units
                FROM deltallm_batch_item i
                JOIN deltallm_batch_job j ON j.batch_id = i.batch_id
                WHERE i.status = 'in_progress'
                  AND (i.lease_expires_at IS NULL OR i.lease_expires_at > NOW())
                  AND COALESCE(
                      NULLIF(i.scheduling_model_group, ''),
                      NULLIF(j.scheduling_model_group, '')
                  ) = $1
                  AND COALESCE(NULLIF(j.service_tier, ''), 'standard') = $2
            ),
            runnable_head_items AS (
                SELECT DISTINCT ON (j.batch_id)
                    j.batch_id,
                    GREATEST(COALESCE(i.estimated_work_units, 1), 1)::int AS estimated_work_units
                FROM deltallm_batch_job j
                JOIN deltallm_batch_item i ON i.batch_id = j.batch_id
                WHERE j.status IN ('queued', 'in_progress')
                  AND (j.locked_by IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at < NOW())
                  AND j.scheduling_model_group = $1
                  AND COALESCE(NULLIF(j.service_tier, ''), 'standard') = $2
                  AND (
                      (
                          i.status = 'pending'
                          AND (i.lease_expires_at IS NULL OR i.lease_expires_at < NOW())
                          AND (i.not_before_at IS NULL OR i.not_before_at <= NOW())
                      )
                      OR (i.status = 'in_progress' AND i.lease_expires_at < NOW())
                  )
                ORDER BY j.batch_id ASC, i.line_number ASC
            )
            SELECT
                COALESCE((SELECT in_flight_items FROM in_flight), 0)::int AS in_flight_items,
                COALESCE((SELECT in_flight_work_units FROM in_flight), 0)::int AS in_flight_work_units,
                EXISTS (SELECT 1 FROM runnable_head_items) AS has_runnable_head_item,
                EXISTS (
                    SELECT 1
                    FROM runnable_head_items
                    WHERE estimated_work_units <= {head_work_threshold_sql}
                ) AS has_fitting_head_item
            """,
            *params,
        )
        row = dict(rows[0]) if rows else {}
        capacity_max = (
            int(capacity_max_in_flight_items)
            if capacity_max_in_flight_items is not None
            else None
        )
        if capacity_max is not None and int(row.get("in_flight_items") or 0) >= capacity_max:
            return "capacity_full_after_lock"
        capacity_work_units_max = (
            int(capacity_max_in_flight_work_units)
            if capacity_max_in_flight_work_units is not None
            else None
        )
        if (
            capacity_work_units_max is not None
            and int(row.get("in_flight_work_units") or 0) >= capacity_work_units_max
        ):
            return "capacity_work_units_full_after_lock"
        if bool(row.get("has_runnable_head_item")) and not bool(row.get("has_fitting_head_item")):
            return "oversized_head_item"
        if not bool(row.get("has_runnable_head_item")):
            return "no_runnable_items_after_selection"
        return "empty_after_selection"

    async def diagnose_empty_work_claim(self) -> str:
        if self.prisma is None:
            return "no_available_work"
        rows = await self.prisma.query_raw(
            """
            WITH candidate_jobs AS (
                SELECT
                    j.batch_id,
                    j.locked_by,
                    j.lease_expires_at,
                    j.last_scheduled_at,
                    j.queue_entered_at,
                    j.created_at
                FROM deltallm_batch_job j
                WHERE j.status IN ('queued', 'in_progress')
                ORDER BY j.last_scheduled_at ASC NULLS FIRST,
                         COALESCE(j.queue_entered_at, j.created_at) ASC,
                         j.created_at ASC
                LIMIT 100
            ),
            unleased_jobs AS (
                SELECT *
                FROM candidate_jobs
                WHERE locked_by IS NULL
                   OR lease_expires_at IS NULL
                   OR lease_expires_at < NOW()
            ),
            claimable_probe AS (
                SELECT i.item_id
                FROM unleased_jobs j
                JOIN deltallm_batch_item i ON i.batch_id = j.batch_id
                -- Runnable-item predicate. Mirror in claim_next_work (and
                -- vice versa) — Phase 3 changes here must update both call
                -- sites.
                WHERE (
                    (
                        i.status = 'pending'
                        AND (i.lease_expires_at IS NULL OR i.lease_expires_at < NOW())
                        AND (i.not_before_at IS NULL OR i.not_before_at <= NOW())
                    )
                    OR (i.status = 'in_progress' AND i.lease_expires_at < NOW())
                )
                ORDER BY j.last_scheduled_at ASC NULLS FIRST,
                         COALESCE(j.queue_entered_at, j.created_at) ASC,
                         j.created_at ASC,
                         i.line_number ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            SELECT
                EXISTS (SELECT 1 FROM candidate_jobs) AS active_jobs,
                EXISTS (SELECT 1 FROM unleased_jobs) AS unleased_jobs,
                EXISTS (
                    SELECT 1
                    FROM unleased_jobs j
                    JOIN deltallm_batch_item i ON i.batch_id = j.batch_id
                    WHERE i.status IN ('pending', 'in_progress')
                    LIMIT 1
                ) AS pending_or_in_progress_items,
                EXISTS (
                    SELECT 1
                    FROM unleased_jobs j
                    JOIN deltallm_batch_item i ON i.batch_id = j.batch_id
                    WHERE i.status = 'pending'
                    LIMIT 1
                ) AS pending_items,
                EXISTS (
                    SELECT 1
                    FROM unleased_jobs j
                    JOIN deltallm_batch_item i ON i.batch_id = j.batch_id
                    WHERE i.status = 'in_progress'
                    LIMIT 1
                ) AS in_progress_items,
                EXISTS (
                    SELECT 1
                    FROM unleased_jobs j
                    JOIN deltallm_batch_item i ON i.batch_id = j.batch_id
                    WHERE i.status = 'pending'
                      AND (i.not_before_at IS NULL OR i.not_before_at <= NOW())
                    LIMIT 1
                ) AS due_pending_items,
                EXISTS (
                    SELECT 1
                    FROM unleased_jobs j
                    JOIN deltallm_batch_item i ON i.batch_id = j.batch_id
                    WHERE i.status = 'pending'
                      AND i.not_before_at IS NOT NULL
                      AND i.not_before_at > NOW()
                    LIMIT 1
                ) AS future_pending_items,
                EXISTS (
                    SELECT 1
                    FROM unleased_jobs j
                    JOIN deltallm_batch_item i ON i.batch_id = j.batch_id
                    WHERE (
                        (
                            i.status = 'pending'
                            AND (i.lease_expires_at IS NULL OR i.lease_expires_at < NOW())
                            AND (i.not_before_at IS NULL OR i.not_before_at <= NOW())
                        )
                        OR (i.status = 'in_progress' AND i.lease_expires_at < NOW())
                    )
                    LIMIT 1
                ) AS runnable_items,
                EXISTS (SELECT 1 FROM claimable_probe) AS claimable_items
            """
        )
        if not rows:
            return "no_available_work"
        row = dict(rows[0])
        active_jobs = bool(row.get("active_jobs"))
        unleased_jobs = bool(row.get("unleased_jobs"))
        pending_or_in_progress_items = bool(row.get("pending_or_in_progress_items"))
        pending_items = bool(row.get("pending_items"))
        in_progress_items = bool(row.get("in_progress_items"))
        due_pending_items = bool(row.get("due_pending_items"))
        future_pending_items = bool(row.get("future_pending_items"))
        runnable_items = bool(row.get("runnable_items"))
        claimable_items = bool(row.get("claimable_items"))

        if not active_jobs or not unleased_jobs:
            return "job_terminal_or_leased"
        if not pending_or_in_progress_items:
            return "no_pending_items"
        if not runnable_items:
            if pending_items and future_pending_items and not due_pending_items and not in_progress_items:
                return "not_before_future"
            return "all_items_locked"
        if not claimable_items:
            return "all_items_locked"
        return "no_available_work"

    async def renew_job_lease(self, *, batch_id: str, worker_id: str, lease_seconds: int) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET lease_expires_at = NOW() + ($3 || ' seconds')::interval
            WHERE batch_id = $1
              AND locked_by = $2
              AND status IN ('in_progress', 'finalizing')
            RETURNING batch_id
            """,
            batch_id,
            worker_id,
            lease_seconds,
        )
        return bool(rows)

    async def reschedule_finalization(
        self,
        *,
        batch_id: str,
        worker_id: str,
        retry_delay_seconds: int,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET lease_expires_at = NOW() + ($3 || ' seconds')::interval,
                locked_by = NULL,
                status_last_updated_at = NOW()
            WHERE batch_id = $1
              AND locked_by = $2
              AND status = 'finalizing'
            RETURNING batch_id
            """,
            batch_id,
            worker_id,
            max(1, retry_delay_seconds),
        )
        return bool(rows)

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
                    COUNT(*) FILTER (WHERE status = 'cancelled')::int AS cancelled_items,
                    COALESCE(
                        SUM(estimated_work_units) FILTER (WHERE status IN ('pending', 'in_progress')),
                        0
                    )::int AS remaining_work_units
                FROM deltallm_batch_item
                WHERE batch_id = $1
            )
            UPDATE deltallm_batch_job j
            SET total_items = s.total_items,
                in_progress_items = s.in_progress_items,
                completed_items = s.completed_items,
                failed_items = s.failed_items,
                cancelled_items = s.cancelled_items,
                remaining_work_units = s.remaining_work_units,
                status = (
                    CASE
                    WHEN j.status IN ('completed', 'failed', 'cancelled', 'expired') THEN j.status
                    WHEN s.pending_items = 0 AND s.in_progress_items = 0 THEN 'finalizing'
                    WHEN s.in_progress_items > 0 OR s.completed_items > 0 OR s.failed_items > 0 OR s.cancelled_items > 0 THEN 'in_progress'
                    ELSE j.status
                    END
                )::"DeltaLLM_BatchJobStatus",
                completed_at = CASE WHEN j.status IN ('completed', 'failed', 'cancelled', 'expired') THEN COALESCE(j.completed_at, NOW()) ELSE NULL END,
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
        final_status: str | BatchJobStatus,
        worker_id: str | None = None,
    ) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        normalized_final_status = normalize_batch_job_status(final_status)
        if worker_id is None:
            rows = await self.prisma.query_raw(
                """
                UPDATE deltallm_batch_job
                SET output_file_id = $2,
                    error_file_id = $3,
                    status = $4::"DeltaLLM_BatchJobStatus",
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
                normalized_final_status.value,
            )
        else:
            rows = await self.prisma.query_raw(
                """
                UPDATE deltallm_batch_job
                SET output_file_id = $3,
                    error_file_id = $4,
                    status = $5::"DeltaLLM_BatchJobStatus",
                    completed_at = COALESCE(completed_at, NOW()),
                    lease_expires_at = NULL,
                    locked_by = NULL,
                    status_last_updated_at = NOW()
                WHERE batch_id = $1
                  AND locked_by = $2
                  AND status = 'finalizing'
                RETURNING *
                """,
                batch_id,
                worker_id,
                output_file_id,
                error_file_id,
                normalized_final_status.value,
            )
        if not rows:
            return None
        return job_from_row(rows[0])

    async def retry_finalization_now(self, batch_id: str) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET lease_expires_at = NULL,
                locked_by = NULL,
                status_last_updated_at = NOW()
            WHERE batch_id = $1
              AND status = 'finalizing'
            RETURNING *
            """,
            batch_id,
        )
        if not rows:
            return None
        return job_from_row(rows[0])

    async def set_provider_error(self, *, batch_id: str, provider_error: str | None) -> BatchJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_batch_job
            SET provider_error = $2,
                status_last_updated_at = NOW()
            WHERE batch_id = $1
            RETURNING *
            """,
            batch_id,
            provider_error,
        )
        if not rows:
            return None
        return job_from_row(rows[0])
