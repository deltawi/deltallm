from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.batch.models import (
    BatchJobRecord,
    BatchJobStatus,
    BatchFairShareClaimResult,
    BatchModelBacklogRecord,
    BatchModelInFlightRecord,
    BatchSchedulerFlowRecord,
    BatchWorkClaim,
    normalize_batch_job_status,
)
from src.batch.repositories.mappers import job_from_row, parse_datetime
from src.batch.scheduling import (
    API_KEY_TENANT_SCOPE_PREFIX,
    ESTIMATOR_VERSION,
    build_flow_id,
    build_scheduling_dimensions,
    default_flow_weight,
    max_deficit_for_flow,
    quantum_for_weight,
    resolve_model_group,
    stable_tenant_scope_id,
)
from src.metrics import (
    increment_batch_item_reclaim,
    increment_batch_scheduler_deficit_refill,
    increment_batch_scheduler_flow_claim,
    increment_batch_scheduler_flow_skip,
    increment_batch_scheduler_shadow_skip,
    observe_batch_claim_wait_by_model,
    observe_batch_estimated_work_units,
    observe_batch_scheduler_fairness_ratio,
    observe_batch_scheduler_flow_wait,
    observe_batch_queue_wait,
    publish_batch_scheduler_flows,
)

logger = logging.getLogger(__name__)

_SCHEDULER_FLOW_SKIP_REASONS = frozenset(
    {
        "empty_flow",
        "insufficient_deficit",
        "lock_busy",
        "no_active_flow",
        "oversized_head_item",
        "tenant_in_flight_full",
        "unknown",
    }
)


def _normalize_scheduler_flow_skip_reason(reason: object) -> str:
    normalized = str(reason or "").strip()
    if normalized in _SCHEDULER_FLOW_SKIP_REASONS:
        return normalized
    return "unknown"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _integer_reason_counts(value: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, raw_count in _json_object(value).items():
        normalized_key = _normalize_scheduler_flow_skip_reason(key)
        if not normalized_key:
            continue
        try:
            counts[normalized_key] = max(0, int(raw_count or 0))
        except (TypeError, ValueError):
            continue
    return counts


def flow_from_row(row: Any) -> BatchSchedulerFlowRecord:
    row_dict = dict(row)
    return BatchSchedulerFlowRecord(
        flow_id=str(row_dict.get("flow_id") or ""),
        service_tier=str(row_dict.get("service_tier") or "standard"),
        model_group=str(row_dict.get("model_group") or "unknown"),
        tenant_scope_type=str(row_dict.get("tenant_scope_type") or "anonymous"),
        tenant_scope_id=str(row_dict.get("tenant_scope_id") or "anonymous"),
        weight=max(1, int(row_dict.get("weight") or 1)),
        quantum_work_units=max(1, int(row_dict.get("quantum_work_units") or 1)),
        deficit_work_units=int(row_dict.get("deficit_work_units") or 0),
        active=bool(row_dict.get("active")),
        queued_jobs=max(0, int(row_dict.get("queued_jobs") or 0)),
        queued_work_units=max(0, int(row_dict.get("queued_work_units") or 0)),
        in_flight_work_units=max(0, int(row_dict.get("in_flight_work_units") or 0)),
        last_selected_at=parse_datetime(row_dict.get("last_selected_at")),
        last_refilled_at=parse_datetime(row_dict.get("last_refilled_at")),
        created_at=parse_datetime(row_dict.get("created_at")),
        updated_at=parse_datetime(row_dict.get("updated_at")),
        oldest_queue_entered_at=parse_datetime(row_dict.get("oldest_queue_entered_at")),
        next_item_work_units=max(1, int(row_dict.get("next_item_work_units") or 1)),
        skip_reasons=_integer_reason_counts(
            row_dict.get("skip_reason_summary") or row_dict.get("skip_reasons")
        ),
    )


@dataclass(frozen=True, slots=True)
class _SchedulerFlowSelection:
    selected: BatchSchedulerFlowRecord | None
    skip_reasons: dict[str, str]
    active_flow_count: int
    eligible_flow_count: int
    selected_uses_oversized_first_item: bool = False

    @property
    def single_eligible_flow(self) -> bool:
        return self.eligible_flow_count == 1


@dataclass(slots=True)
class _SchedulerFlowRefreshAggregate:
    service_tier: str
    model_group: str
    tenant_scope_type: str
    tenant_scope_id: str
    queued_jobs: int = 0
    queued_work_units: int = 0
    in_flight_work_units: int = 0
    oldest_queue_entered_at: datetime | None = None
    next_item_work_units: int = 1


def _normalize_scheduler_tenant_scope(
    *,
    tenant_scope_type: str | None,
    tenant_scope_id: str | None,
) -> tuple[str, str]:
    normalized_type = str(tenant_scope_type or "").strip() or "anonymous"
    normalized_id = str(tenant_scope_id or "").strip() or "anonymous"
    if normalized_type == "api_key":
        normalized_id = stable_tenant_scope_id(
            scope_type=normalized_type,
            scope_id=normalized_id,
        )
    return normalized_type, normalized_id


async def _repair_legacy_api_key_scope_ids_for_scheduler_refresh(
    executor: Any,
    *,
    service_tier: str | None,
    model_group: str | None,
    scope_repairs: Mapping[str, str],
) -> int:
    if not scope_repairs:
        return 0
    repaired = 0
    normalized_service_tier = str(service_tier or "").strip()
    normalized_model_group = str(model_group or "").strip()
    for raw_scope_id, stable_scope_id in scope_repairs.items():
        raw_scope = str(raw_scope_id or "").strip()
        stable_scope = str(stable_scope_id or "").strip()
        if (
            not raw_scope
            or raw_scope == "anonymous"
            or raw_scope.startswith(API_KEY_TENANT_SCOPE_PREFIX)
            or not stable_scope
        ):
            continue
        params: list[Any] = [
            raw_scope,
            stable_scope,
            f"{API_KEY_TENANT_SCOPE_PREFIX}%",
        ]
        model_filter_sql = ""
        if normalized_model_group:
            params.append(normalized_model_group)
            model_filter_sql = f"AND j.scheduling_model_group = ${len(params)}"
        service_tier_filter_sql = ""
        if normalized_service_tier:
            params.append(normalized_service_tier)
            service_tier_filter_sql = (
                f"AND COALESCE(NULLIF(j.service_tier, ''), 'standard') = ${len(params)}"
            )
        rows = await executor.query_raw(
            f"""
            UPDATE deltallm_batch_job j
            SET tenant_scope_id = $2
            WHERE j.tenant_scope_type = 'api_key'
              AND j.tenant_scope_id = $1
              AND j.tenant_scope_id NOT LIKE $3
              AND j.status IN ('queued', 'in_progress')
              {model_filter_sql}
              {service_tier_filter_sql}
            RETURNING j.batch_id
            """,
            *params,
        )
        repaired += len(rows)
    return repaired


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
        tenant_scope_preference: tuple[str, ...] | list[str] | None = None,
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
            tenant_scope_preference=tenant_scope_preference,
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
        if record.status == BatchJobStatus.QUEUED:
            await self.upsert_scheduler_flow_for_job(record)
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
            await self.upsert_scheduler_flow_for_job(record)
            return record

        existing_scope_id = str(record.tenant_scope_id or "").strip()
        if existing_scope_id.startswith(API_KEY_TENANT_SCOPE_PREFIX):
            await self.upsert_scheduler_flow_for_job(record)
            return record

        raw_scope_id = existing_scope_id or str(record.created_by_api_key or "").strip()
        if not raw_scope_id:
            await self.upsert_scheduler_flow_for_job(record)
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
        repaired = job_from_row(repaired_rows[0])
        await self.upsert_scheduler_flow_for_job(repaired)
        return repaired

    async def upsert_scheduler_flow_for_job(
        self,
        job: BatchJobRecord,
        *,
        db: Any | None = None,
        base_quantum_work_units: int = 16,
        max_deficit_multiplier: int = 8,
    ) -> BatchSchedulerFlowRecord | None:
        executor = db or self.prisma
        if executor is None:
            return None
        model_group = str(job.scheduling_model_group or "").strip()
        service_tier = str(job.service_tier or "standard").strip() or "standard"
        tenant_scope_type = str(job.tenant_scope_type or "anonymous").strip() or "anonymous"
        tenant_scope_id = str(job.tenant_scope_id or "anonymous").strip() or "anonymous"
        if not model_group:
            return None
        flow_id = build_flow_id(
            service_tier=service_tier,
            model_group=model_group,
            tenant_scope_type=tenant_scope_type,
            tenant_scope_id=tenant_scope_id,
        )
        weight = default_flow_weight(service_tier=service_tier)
        quantum = quantum_for_weight(
            base_quantum_work_units=base_quantum_work_units,
            weight=weight,
        )
        bounded_base_quantum = max(1, int(base_quantum_work_units or 1))
        bounded_max_deficit_multiplier = max(1, int(max_deficit_multiplier or 1))
        active = job.status in {BatchJobStatus.QUEUED, BatchJobStatus.IN_PROGRESS}
        queued_jobs = 1 if active else 0
        queued_work_units = max(0, int(job.remaining_work_units or job.estimated_work_units or job.total_items or 0))
        rows = await executor.query_raw(
            """
            INSERT INTO deltallm_batch_scheduler_flow (
                flow_id, service_tier, model_group, tenant_scope_type, tenant_scope_id,
                weight, quantum_work_units, deficit_work_units, active,
                queued_jobs, queued_work_units, in_flight_work_units, updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, 0, $8,
                $9, $10, 0, NOW()
            )
            ON CONFLICT (service_tier, model_group, tenant_scope_type, tenant_scope_id)
            DO UPDATE SET
                weight = GREATEST(deltallm_batch_scheduler_flow.weight, 1),
                quantum_work_units = LEAST(
                    256,
                    GREATEST(1, $11::int * GREATEST(deltallm_batch_scheduler_flow.weight, 1))
                ),
                deficit_work_units = LEAST(
                    deltallm_batch_scheduler_flow.deficit_work_units,
                    LEAST(
                        256,
                        GREATEST(1, $11::int * GREATEST(deltallm_batch_scheduler_flow.weight, 1))
                    ) * $12::int
                ),
                active = EXCLUDED.active,
                queued_jobs = GREATEST(deltallm_batch_scheduler_flow.queued_jobs, EXCLUDED.queued_jobs),
                queued_work_units = GREATEST(
                    deltallm_batch_scheduler_flow.queued_work_units,
                    EXCLUDED.queued_work_units
                ),
                updated_at = NOW()
            RETURNING *
            """,
            flow_id,
            service_tier,
            model_group,
            tenant_scope_type,
            tenant_scope_id,
            weight,
            quantum,
            active,
            queued_jobs,
            queued_work_units,
            bounded_base_quantum,
            bounded_max_deficit_multiplier,
        )
        if not rows:
            return None
        return flow_from_row(rows[0])

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

    async def refresh_scheduler_flows(
        self,
        *,
        service_tier: str | None = None,
        model_group: str | None = None,
        db: Any | None = None,
        base_quantum_work_units: int = 16,
        max_deficit_multiplier: int = 8,
    ) -> list[BatchSchedulerFlowRecord]:
        executor = db or self.prisma
        if executor is None:
            return []
        normalized_service_tier = str(service_tier or "").strip()
        normalized_model_group = str(model_group or "").strip()
        params: list[Any] = []
        model_filter_sql = ""
        if normalized_model_group:
            params.append(normalized_model_group)
            model_filter_sql = f"AND j.scheduling_model_group = ${len(params)}"
        service_tier_filter_sql = ""
        if normalized_service_tier:
            params.append(normalized_service_tier)
            service_tier_filter_sql = f"AND COALESCE(NULLIF(j.service_tier, ''), 'standard') = ${len(params)}"
        rows = await executor.query_raw(
            f"""
            WITH runnable_jobs AS (
                SELECT
                    j.batch_id,
                    COALESCE(NULLIF(j.service_tier, ''), 'standard') AS service_tier,
                    j.scheduling_model_group AS model_group,
                    COALESCE(NULLIF(j.tenant_scope_type, ''), 'anonymous') AS tenant_scope_type,
                    COALESCE(NULLIF(j.tenant_scope_id, ''), 'anonymous') AS tenant_scope_id,
                    runnable_items.runnable_work_units,
                    COALESCE(j.queue_entered_at, j.created_at) AS queue_entered_at,
                    j.created_at,
                    runnable_items.next_item_work_units
                FROM deltallm_batch_job j
                JOIN LATERAL (
                    SELECT
                        COUNT(*)::int AS runnable_item_count,
                        COALESCE(
                            SUM(GREATEST(COALESCE(i.estimated_work_units, 1), 1)),
                            0
                        )::int AS runnable_work_units,
                        (
                            ARRAY_AGG(
                                GREATEST(COALESCE(i.estimated_work_units, 1), 1)::int
                                ORDER BY i.line_number ASC
                            )
                        )[1]::int AS next_item_work_units
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
                ) runnable_items ON runnable_items.runnable_item_count > 0
                WHERE j.status IN ('queued', 'in_progress')
                  AND (j.locked_by IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at < NOW())
                  AND j.scheduling_model_group IS NOT NULL
                  AND j.scheduling_model_group <> ''
                  AND j.tenant_scope_type IS NOT NULL
                  AND j.tenant_scope_type <> ''
                  AND j.tenant_scope_id IS NOT NULL
                  AND j.tenant_scope_id <> ''
                  {model_filter_sql}
                  {service_tier_filter_sql}
            ),
            queued AS (
                SELECT
                    service_tier,
                    model_group,
                    tenant_scope_type,
                    tenant_scope_id,
                    COUNT(*)::int AS queued_jobs,
                    COALESCE(SUM(runnable_work_units), 0)::int AS queued_work_units,
                    MIN(queue_entered_at) AS oldest_queue_entered_at,
                    (ARRAY_AGG(next_item_work_units ORDER BY queue_entered_at ASC, created_at ASC, batch_id ASC))[1]::int
                        AS next_item_work_units
                FROM runnable_jobs
                GROUP BY service_tier, model_group, tenant_scope_type, tenant_scope_id
            ),
            in_flight AS (
                SELECT
                    COALESCE(NULLIF(j.service_tier, ''), 'standard') AS service_tier,
                    COALESCE(NULLIF(i.scheduling_model_group, ''), NULLIF(j.scheduling_model_group, '')) AS model_group,
                    COALESCE(NULLIF(j.tenant_scope_type, ''), 'anonymous') AS tenant_scope_type,
                    COALESCE(NULLIF(j.tenant_scope_id, ''), 'anonymous') AS tenant_scope_id,
                    COALESCE(SUM(GREATEST(COALESCE(i.estimated_work_units, 1), 1)), 0)::int
                        AS in_flight_work_units
                FROM deltallm_batch_item i
                JOIN deltallm_batch_job j ON j.batch_id = i.batch_id
                WHERE i.status = 'in_progress'
                  AND (i.lease_expires_at IS NULL OR i.lease_expires_at > NOW())
                  AND COALESCE(NULLIF(i.scheduling_model_group, ''), NULLIF(j.scheduling_model_group, '')) IS NOT NULL
                  {model_filter_sql}
                  {service_tier_filter_sql}
                GROUP BY service_tier, model_group, tenant_scope_type, tenant_scope_id
            )
            SELECT
                COALESCE(q.service_tier, f.service_tier) AS service_tier,
                COALESCE(q.model_group, f.model_group) AS model_group,
                COALESCE(q.tenant_scope_type, f.tenant_scope_type) AS tenant_scope_type,
                COALESCE(q.tenant_scope_id, f.tenant_scope_id) AS tenant_scope_id,
                COALESCE(q.queued_jobs, 0)::int AS queued_jobs,
                COALESCE(q.queued_work_units, 0)::int AS queued_work_units,
                COALESCE(f.in_flight_work_units, 0)::int AS in_flight_work_units,
                q.oldest_queue_entered_at,
                COALESCE(q.next_item_work_units, 1)::int AS next_item_work_units
            FROM queued q
            FULL OUTER JOIN in_flight f
              ON f.service_tier = q.service_tier
             AND f.model_group = q.model_group
             AND f.tenant_scope_type = q.tenant_scope_type
             AND f.tenant_scope_id = q.tenant_scope_id
            ORDER BY q.oldest_queue_entered_at ASC NULLS LAST,
                     COALESCE(q.model_group, f.model_group) ASC,
                     COALESCE(q.tenant_scope_type, f.tenant_scope_type) ASC,
                     COALESCE(q.tenant_scope_id, f.tenant_scope_id) ASC
            """,
            *params,
        )
        aggregates: dict[
            tuple[str, str, str, str],
            _SchedulerFlowRefreshAggregate,
        ] = {}
        legacy_api_key_scope_repairs: dict[str, str] = {}
        for row in rows:
            row_dict = dict(row)
            row_service_tier = str(row_dict.get("service_tier") or "").strip() or "standard"
            row_model_group = str(row_dict.get("model_group") or "").strip()
            if not row_model_group:
                continue
            raw_tenant_scope_type = str(row_dict.get("tenant_scope_type") or "anonymous").strip()
            raw_tenant_scope_id = str(row_dict.get("tenant_scope_id") or "anonymous").strip()
            tenant_scope_type, tenant_scope_id = _normalize_scheduler_tenant_scope(
                tenant_scope_type=raw_tenant_scope_type,
                tenant_scope_id=raw_tenant_scope_id,
            )
            if (
                raw_tenant_scope_type == "api_key"
                and raw_tenant_scope_id
                and raw_tenant_scope_id != "anonymous"
                and not raw_tenant_scope_id.startswith(API_KEY_TENANT_SCOPE_PREFIX)
            ):
                legacy_api_key_scope_repairs[raw_tenant_scope_id] = tenant_scope_id
            key = (row_service_tier, row_model_group, tenant_scope_type, tenant_scope_id)
            aggregate = aggregates.setdefault(
                key,
                _SchedulerFlowRefreshAggregate(
                    service_tier=row_service_tier,
                    model_group=row_model_group,
                    tenant_scope_type=tenant_scope_type,
                    tenant_scope_id=tenant_scope_id,
                ),
            )
            aggregate.queued_jobs += max(0, int(row_dict.get("queued_jobs") or 0))
            aggregate.queued_work_units += max(0, int(row_dict.get("queued_work_units") or 0))
            aggregate.in_flight_work_units += max(0, int(row_dict.get("in_flight_work_units") or 0))
            row_oldest_queue_entered_at = parse_datetime(row_dict.get("oldest_queue_entered_at"))
            if row_oldest_queue_entered_at is not None and (
                aggregate.oldest_queue_entered_at is None
                or row_oldest_queue_entered_at < aggregate.oldest_queue_entered_at
            ):
                aggregate.oldest_queue_entered_at = row_oldest_queue_entered_at
                aggregate.next_item_work_units = max(
                    1,
                    int(row_dict.get("next_item_work_units") or 1),
                )
        active_flow_ids: list[str] = []
        refreshed: list[BatchSchedulerFlowRecord] = []
        await _repair_legacy_api_key_scope_ids_for_scheduler_refresh(
            executor,
            service_tier=normalized_service_tier,
            model_group=normalized_model_group,
            scope_repairs=legacy_api_key_scope_repairs,
        )
        ordered_aggregates = sorted(
            aggregates.values(),
            key=lambda aggregate: (
                aggregate.oldest_queue_entered_at or datetime.max.replace(tzinfo=UTC),
                aggregate.model_group,
                aggregate.tenant_scope_type,
                aggregate.tenant_scope_id,
            ),
        )
        for aggregate in ordered_aggregates:
            flow_id = build_flow_id(
                service_tier=aggregate.service_tier,
                model_group=aggregate.model_group,
                tenant_scope_type=aggregate.tenant_scope_type,
                tenant_scope_id=aggregate.tenant_scope_id,
            )
            active_flow_ids.append(flow_id)
            weight = default_flow_weight(service_tier=aggregate.service_tier)
            quantum = quantum_for_weight(
                base_quantum_work_units=base_quantum_work_units,
                weight=weight,
            )
            bounded_base_quantum = max(1, int(base_quantum_work_units or 1))
            bounded_max_deficit_multiplier = max(1, int(max_deficit_multiplier or 1))
            active = aggregate.queued_jobs > 0
            upserted = await executor.query_raw(
                """
                INSERT INTO deltallm_batch_scheduler_flow (
                    flow_id, service_tier, model_group, tenant_scope_type, tenant_scope_id,
                    weight, quantum_work_units, deficit_work_units, active,
                    queued_jobs, queued_work_units, in_flight_work_units, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, 0, $8,
                    $9, $10, $11, NOW()
                )
                ON CONFLICT (service_tier, model_group, tenant_scope_type, tenant_scope_id)
                DO UPDATE SET
                    weight = GREATEST(deltallm_batch_scheduler_flow.weight, 1),
                    quantum_work_units = LEAST(
                        256,
                        GREATEST(1, $12::int * GREATEST(deltallm_batch_scheduler_flow.weight, 1))
                    ),
                    deficit_work_units = LEAST(
                        deltallm_batch_scheduler_flow.deficit_work_units,
                        LEAST(
                            256,
                            GREATEST(1, $12::int * GREATEST(deltallm_batch_scheduler_flow.weight, 1))
                        ) * $13::int
                    ),
                    active = EXCLUDED.active,
                    queued_jobs = EXCLUDED.queued_jobs,
                    queued_work_units = EXCLUDED.queued_work_units,
                    in_flight_work_units = EXCLUDED.in_flight_work_units,
                    updated_at = NOW()
                RETURNING
                    *,
                    $14::timestamp AS oldest_queue_entered_at,
                    $15::int AS next_item_work_units
                """,
                flow_id,
                aggregate.service_tier,
                aggregate.model_group,
                aggregate.tenant_scope_type,
                aggregate.tenant_scope_id,
                weight,
                quantum,
                active,
                aggregate.queued_jobs,
                aggregate.queued_work_units,
                aggregate.in_flight_work_units,
                bounded_base_quantum,
                bounded_max_deficit_multiplier,
                aggregate.oldest_queue_entered_at,
                aggregate.next_item_work_units,
            )
            if upserted:
                flow = flow_from_row(upserted[0])
                refreshed.append(flow)
        deactivated = await self._deactivate_stale_scheduler_flows(
            executor,
            service_tier=normalized_service_tier or None,
            model_group=normalized_model_group or None,
            active_flow_ids=active_flow_ids,
        )
        publish_batch_scheduler_flows([*refreshed, *deactivated])
        return refreshed

    async def _deactivate_stale_scheduler_flows(
        self,
        db: Any,
        *,
        service_tier: str | None,
        model_group: str | None,
        active_flow_ids: list[str],
    ) -> list[BatchSchedulerFlowRecord]:
        params: list[Any] = [active_flow_ids]
        filters: list[str] = [
            "NOT (flow_id = ANY($1::text[]))",
            "(active = true OR queued_jobs <> 0 OR queued_work_units <> 0 OR in_flight_work_units <> 0)",
        ]
        if service_tier:
            params.append(service_tier)
            filters.append(f"service_tier = ${len(params)}")
        if model_group:
            params.append(model_group)
            filters.append(f"model_group = ${len(params)}")
        rows = await db.query_raw(
            f"""
            UPDATE deltallm_batch_scheduler_flow
            SET active = false,
                queued_jobs = 0,
                queued_work_units = 0,
                in_flight_work_units = 0,
                updated_at = NOW()
            WHERE {' AND '.join(filters)}
            RETURNING *, NULL::timestamp AS oldest_queue_entered_at, 1::int AS next_item_work_units
            """,
            *params,
        )
        return [flow_from_row(row) for row in rows]

    async def list_scheduler_flows(
        self,
        *,
        service_tier: str | None = None,
        model_group: str | None = None,
        tenant_scope_type: str | None = None,
        active: bool | None = None,
        db: Any | None = None,
    ) -> list[BatchSchedulerFlowRecord]:
        executor = db or self.prisma
        if executor is None:
            return []
        params: list[Any] = []
        filters: list[str] = []
        if service_tier:
            params.append(str(service_tier).strip() or "standard")
            filters.append(f"service_tier = ${len(params)}")
        if model_group:
            params.append(str(model_group).strip())
            filters.append(f"model_group = ${len(params)}")
        if tenant_scope_type:
            params.append(str(tenant_scope_type).strip())
            filters.append(f"tenant_scope_type = ${len(params)}")
        if active is not None:
            params.append(bool(active))
            filters.append(f"active = ${len(params)}")
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = await executor.query_raw(
            f"""
            SELECT *, NULL::timestamp AS oldest_queue_entered_at, 1::int AS next_item_work_units
            FROM deltallm_batch_scheduler_flow
            {where_sql}
            ORDER BY service_tier ASC,
                     model_group ASC,
                     active DESC,
                     last_selected_at ASC NULLS FIRST,
                     tenant_scope_type ASC,
                     tenant_scope_id ASC
            """,
            *params,
        )
        return [flow_from_row(row) for row in rows]

    async def _publish_scheduler_flow_metric_group(
        self,
        db: Any,
        *,
        service_tier: str,
        model_group: str,
        tenant_scope_type: str,
    ) -> None:
        flows = await self.list_scheduler_flows(
            service_tier=service_tier,
            model_group=model_group,
            tenant_scope_type=tenant_scope_type,
            db=db,
        )
        publish_batch_scheduler_flows(flows)

    async def get_tenant_queued_work_units(
        self,
        *,
        tenant_scope_type: str,
        tenant_scope_id: str,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
        created_by_organization_id: str | None = None,
        created_by_user_id: str | None = None,
    ) -> int:
        """Return queued work for admission control.

        API-key scopes need the raw key context while legacy raw scope rows may
        exist, otherwise a stable hashed scope could silently miss old rows.
        """
        if self.prisma is None:
            return 0
        normalized_scope_type = str(tenant_scope_type or "").strip()
        normalized_scope_id = str(tenant_scope_id or "").strip()
        raw_api_key = str(created_by_api_key or "").strip()
        owner_team_id = str(created_by_team_id or "").strip()
        owner_organization_id = str(created_by_organization_id or "").strip()
        owner_user_id = str(created_by_user_id or "").strip()
        if normalized_scope_type == "api_key":
            if not raw_api_key and not normalized_scope_id.startswith(API_KEY_TENANT_SCOPE_PREFIX):
                raw_api_key = normalized_scope_id
            if not raw_api_key and normalized_scope_id.startswith(API_KEY_TENANT_SCOPE_PREFIX):
                raise ValueError(
                    "created_by_api_key is required when counting api_key tenant backlog"
                )
            normalized_scope_id = stable_tenant_scope_id(
                scope_type=normalized_scope_type,
                scope_id=normalized_scope_id,
            )
        rows = await self.prisma.query_raw(
            """
            SELECT COALESCE(SUM(GREATEST(COALESCE(i.estimated_work_units, 1), 1)), 0)::int
                AS queued_work_units
            FROM deltallm_batch_item i
            JOIN deltallm_batch_job j ON j.batch_id = i.batch_id
            WHERE j.status IN ('queued', 'in_progress')
              AND (
                  (
                      j.tenant_scope_type = $1
                      AND j.tenant_scope_id = $2
                  )
                  OR (
                      $1 = 'api_key'
                      AND $3 <> ''
                      AND (
                          j.tenant_scope_id = $3
                          OR j.created_by_api_key = $3
                      )
                  )
                  OR (
                      $1 = 'team'
                      AND $4 <> ''
                      AND j.created_by_team_id = $4
                  )
                  OR (
                      $1 = 'organization'
                      AND $5 <> ''
                      AND j.created_by_organization_id = $5
                  )
                  OR (
                      $1 = 'user'
                      AND $6 <> ''
                      AND j.created_by_user_id = $6
                  )
              )
              AND (
                  i.status = 'pending'
                  OR (i.status = 'in_progress' AND i.lease_expires_at < NOW())
              )
            """,
            normalized_scope_type,
            normalized_scope_id,
            raw_api_key,
            owner_team_id,
            owner_organization_id,
            owner_user_id,
        )
        if not rows:
            return 0
        return max(0, int(dict(rows[0]).get("queued_work_units") or 0))

    async def claim_next_fair_share_work(
        self,
        *,
        worker_id: str,
        service_tier: str,
        model_group: str,
        max_items: int,
        max_work_units: int,
        lease_seconds: int,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
        base_quantum_work_units: int = 16,
        max_deficit_multiplier: int = 8,
        tenant_max_in_flight_work_units: int = 0,
    ) -> BatchFairShareClaimResult:
        if self.prisma is None:
            return BatchFairShareClaimResult(claim=None, result="repository_unavailable")
        normalized_service_tier = str(service_tier or "standard").strip() or "standard"
        normalized_model_group = str(model_group or "").strip()
        if not normalized_model_group:
            return BatchFairShareClaimResult(claim=None, result="missing_model_group")
        tx_factory = getattr(self.prisma, "tx", None)
        if not callable(tx_factory):
            logger.warning(
                "batch fair-share claim requires transaction-capable prisma client model_group=%s",
                normalized_model_group,
            )
            return BatchFairShareClaimResult(claim=None, result="transaction_unavailable")
        async with tx_factory() as tx:
            locked = await self._try_acquire_scheduler_flow_lock(
                tx,
                service_tier=normalized_service_tier,
                model_group=normalized_model_group,
            )
            if not locked:
                increment_batch_scheduler_flow_skip(reason="lock_busy")
                return BatchFairShareClaimResult(claim=None, result="lock_busy")

            empty_flow_ids: set[str] = set()
            rounds = max(1, int(max_deficit_multiplier))
            flows = await self.refresh_scheduler_flows(
                service_tier=normalized_service_tier,
                model_group=normalized_model_group,
                db=tx,
                base_quantum_work_units=base_quantum_work_units,
                max_deficit_multiplier=max_deficit_multiplier,
            )
            for _ in range(rounds):
                flow_selection = self._select_scheduler_flow(
                    flows,
                    max_work_units=max_work_units,
                    tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
                    allow_deficit_bypass=False,
                    max_deficit_multiplier=max_deficit_multiplier,
                    excluded_flow_ids=empty_flow_ids,
                )
                await self._record_scheduler_flow_skip_reasons(tx, flow_selection.skip_reasons)
                selected = flow_selection.selected
                if selected is not None:
                    claim_result = await self._claim_scheduler_selected_flow(
                        tx,
                        flow=selected,
                        flow_selection=flow_selection,
                        worker_id=worker_id,
                        max_items=max_items,
                        max_work_units=max_work_units,
                        lease_seconds=lease_seconds,
                        capacity_max_in_flight_items=capacity_max_in_flight_items,
                        capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
                        tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
                        max_deficit_multiplier=max_deficit_multiplier,
                    )
                    if claim_result.result != "empty_flow":
                        return claim_result
                    empty_flow_ids.add(selected.flow_id)
                    continue
                terminal_result = self._terminal_empty_scheduler_flow_result(flow_selection)
                if terminal_result is not None:
                    increment_batch_scheduler_flow_skip(reason=terminal_result)
                    return BatchFairShareClaimResult(claim=None, result=terminal_result)
                refill_count = await self._refill_scheduler_flow_deficits(
                    tx,
                    service_tier=normalized_service_tier,
                    model_group=normalized_model_group,
                    max_deficit_multiplier=max_deficit_multiplier,
                )
                increment_batch_scheduler_deficit_refill(
                    model_group=normalized_model_group,
                    service_tier=normalized_service_tier,
                    count=refill_count,
                )
                if refill_count > 0:
                    flows = self._simulate_scheduler_flow_deficit_refill(
                        flows,
                        max_deficit_multiplier=max_deficit_multiplier,
                    )

            flow_selection = self._select_scheduler_flow(
                flows,
                max_work_units=max_work_units,
                tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
                allow_deficit_bypass=True,
                max_deficit_multiplier=max_deficit_multiplier,
                excluded_flow_ids=empty_flow_ids,
            )
            await self._record_scheduler_flow_skip_reasons(tx, flow_selection.skip_reasons)
            selected = flow_selection.selected
            if selected is None:
                result = self._terminal_empty_scheduler_flow_result(flow_selection)
                if result is None:
                    result = "empty_flow" if empty_flow_ids else "no_active_flow"
                increment_batch_scheduler_flow_skip(reason=result)
                return BatchFairShareClaimResult(claim=None, result=result)
            return await self._claim_scheduler_selected_flow(
                tx,
                flow=selected,
                flow_selection=flow_selection,
                worker_id=worker_id,
                max_items=1,
                max_work_units=max_work_units,
                lease_seconds=lease_seconds,
                capacity_max_in_flight_items=capacity_max_in_flight_items,
                capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
                tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
                max_deficit_multiplier=max_deficit_multiplier,
                allow_oversized_first_item=True,
            )

    async def recommend_next_fair_share_flow(
        self,
        *,
        service_tier: str,
        model_group: str,
        max_items: int,
        max_work_units: int,
        base_quantum_work_units: int = 16,
        max_deficit_multiplier: int = 8,
        tenant_max_in_flight_work_units: int = 0,
    ) -> BatchFairShareClaimResult:
        del max_items
        if self.prisma is None:
            return BatchFairShareClaimResult(claim=None, result="repository_unavailable")
        normalized_service_tier = str(service_tier or "standard").strip() or "standard"
        normalized_model_group = str(model_group or "").strip()
        if not normalized_model_group:
            return BatchFairShareClaimResult(claim=None, result="missing_model_group")
        tx_factory = getattr(self.prisma, "tx", None)
        if not callable(tx_factory):
            logger.warning(
                "batch fair-share recommendation requires transaction-capable prisma client model_group=%s",
                normalized_model_group,
            )
            return BatchFairShareClaimResult(claim=None, result="transaction_unavailable")
        async with tx_factory() as tx:
            locked = await self._try_acquire_scheduler_flow_lock(
                tx,
                service_tier=normalized_service_tier,
                model_group=normalized_model_group,
            )
            if not locked:
                increment_batch_scheduler_shadow_skip(
                    model_group=normalized_model_group,
                    service_tier=normalized_service_tier,
                    reason="lock_busy",
                )
                return BatchFairShareClaimResult(claim=None, result="lock_busy")

            flows = await self.refresh_scheduler_flows(
                service_tier=normalized_service_tier,
                model_group=normalized_model_group,
                db=tx,
                base_quantum_work_units=base_quantum_work_units,
                max_deficit_multiplier=max_deficit_multiplier,
            )
            simulated_flows = [replace(flow) for flow in flows]
            rounds = max(1, int(max_deficit_multiplier))
            for _ in range(rounds):
                flow_selection = self._select_scheduler_flow(
                    simulated_flows,
                    max_work_units=max_work_units,
                    tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
                    allow_deficit_bypass=False,
                    max_deficit_multiplier=max_deficit_multiplier,
                )
                self._record_scheduler_shadow_skip_reasons(
                    model_group=normalized_model_group,
                    service_tier=normalized_service_tier,
                    skip_reasons=flow_selection.skip_reasons,
                )
                if flow_selection.selected is not None:
                    return BatchFairShareClaimResult(
                        claim=None,
                        result="recommended",
                        flow=flow_selection.selected,
                        expected_share=self._expected_scheduler_flow_share(
                            simulated_flows,
                            flow_selection.selected,
                        ),
                        active_flow_count=flow_selection.active_flow_count,
                        total_in_flight_work_units=self._total_scheduler_flow_in_flight_work_units(
                            simulated_flows
                        ),
                    )
                terminal_result = self._terminal_empty_scheduler_flow_result(flow_selection)
                if terminal_result is not None:
                    increment_batch_scheduler_shadow_skip(
                        model_group=normalized_model_group,
                        service_tier=normalized_service_tier,
                        reason=terminal_result,
                    )
                    return BatchFairShareClaimResult(
                        claim=None,
                        result=terminal_result,
                        active_flow_count=flow_selection.active_flow_count,
                    )
                simulated_flows = self._simulate_scheduler_flow_deficit_refill(
                    simulated_flows,
                    max_deficit_multiplier=max_deficit_multiplier,
                )

            flow_selection = self._select_scheduler_flow(
                simulated_flows,
                max_work_units=max_work_units,
                tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
                allow_deficit_bypass=True,
                max_deficit_multiplier=max_deficit_multiplier,
            )
            self._record_scheduler_shadow_skip_reasons(
                model_group=normalized_model_group,
                service_tier=normalized_service_tier,
                skip_reasons=flow_selection.skip_reasons,
            )
            if flow_selection.selected is None:
                result = self._terminal_empty_scheduler_flow_result(flow_selection) or "no_active_flow"
                increment_batch_scheduler_shadow_skip(
                    model_group=normalized_model_group,
                    service_tier=normalized_service_tier,
                    reason=result,
                )
                return BatchFairShareClaimResult(
                    claim=None,
                    result=result,
                    active_flow_count=flow_selection.active_flow_count,
                )
            return BatchFairShareClaimResult(
                claim=None,
                result="recommended",
                flow=flow_selection.selected,
                expected_share=self._expected_scheduler_flow_share(
                    simulated_flows,
                    flow_selection.selected,
                ),
                active_flow_count=flow_selection.active_flow_count,
                total_in_flight_work_units=self._total_scheduler_flow_in_flight_work_units(
                    simulated_flows
                ),
            )

    @staticmethod
    def _record_scheduler_shadow_skip_reasons(
        *,
        model_group: str,
        service_tier: str,
        skip_reasons: dict[str, str],
    ) -> None:
        for reason in skip_reasons.values():
            increment_batch_scheduler_shadow_skip(
                model_group=model_group,
                service_tier=service_tier,
                reason=_normalize_scheduler_flow_skip_reason(reason),
            )

    @staticmethod
    def _simulate_scheduler_flow_deficit_refill(
        flows: list[BatchSchedulerFlowRecord],
        *,
        max_deficit_multiplier: int,
    ) -> list[BatchSchedulerFlowRecord]:
        refilled: list[BatchSchedulerFlowRecord] = []
        bounded_multiplier = max(1, int(max_deficit_multiplier))
        now = datetime.now(tz=UTC)
        for flow in flows:
            if not flow.active or flow.queued_jobs <= 0:
                refilled.append(flow)
                continue
            max_deficit = max(1, int(flow.quantum_work_units or 1)) * bounded_multiplier
            refilled.append(
                replace(
                    flow,
                    deficit_work_units=min(
                        int(flow.deficit_work_units or 0) + max(1, int(flow.quantum_work_units or 1)),
                        max_deficit,
                    ),
                    last_refilled_at=now,
                )
            )
        return refilled

    @staticmethod
    def _expected_scheduler_flow_share(
        flows: list[BatchSchedulerFlowRecord],
        selected: BatchSchedulerFlowRecord,
    ) -> float | None:
        active_flows = [
            flow
            for flow in flows
            if flow.active and flow.queued_jobs > 0 and flow.queued_work_units > 0
        ]
        if not active_flows:
            return None
        total_weight = sum(max(1, int(flow.weight or 1)) for flow in active_flows)
        if total_weight <= 0:
            return None
        selected_flow = next(
            (flow for flow in active_flows if flow.flow_id == selected.flow_id),
            selected,
        )
        return float(max(1, int(selected_flow.weight or 1))) / float(total_weight)

    @staticmethod
    def _total_scheduler_flow_in_flight_work_units(
        flows: list[BatchSchedulerFlowRecord],
    ) -> int:
        return sum(
            max(0, int(flow.in_flight_work_units or 0))
            for flow in flows
            if flow.active and flow.queued_jobs > 0 and flow.queued_work_units > 0
        )

    async def _claim_scheduler_selected_flow(
        self,
        db: Any,
        *,
        flow: BatchSchedulerFlowRecord,
        flow_selection: _SchedulerFlowSelection,
        worker_id: str,
        max_items: int,
        max_work_units: int,
        lease_seconds: int,
        capacity_max_in_flight_items: int | None,
        capacity_max_in_flight_work_units: int | None,
        tenant_max_in_flight_work_units: int,
        max_deficit_multiplier: int,
        allow_oversized_first_item: bool | None = None,
    ) -> BatchFairShareClaimResult:
        oversized_first_item = (
            flow_selection.selected_uses_oversized_first_item
            if allow_oversized_first_item is None
            else allow_oversized_first_item
        )
        claim_work_units = self._flow_claim_work_units(
            flow,
            max_work_units=max_work_units,
            tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
            single_eligible_flow=flow_selection.single_eligible_flow,
        )
        return await self._claim_scheduler_flow_with_client(
            db,
            flow=flow,
            worker_id=worker_id,
            max_items=1 if oversized_first_item else max_items,
            max_work_units=claim_work_units,
            lease_seconds=lease_seconds,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
            allow_oversized_first_item=oversized_first_item,
            max_deficit_multiplier=max_deficit_multiplier,
        )

    async def _try_acquire_scheduler_flow_lock(
        self,
        db: Any,
        *,
        service_tier: str,
        model_group: str,
    ) -> bool:
        rows = await db.query_raw(
            """
            SELECT pg_try_advisory_xact_lock(hashtext($1), hashtext($2))::bool AS locked
            """,
            model_group,
            service_tier,
        )
        if not rows:
            return False
        value = dict(rows[0]).get("locked")
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"t", "true", "1"}

    async def _record_scheduler_flow_skip_reasons(
        self,
        db: Any,
        skip_reasons: dict[str, str],
    ) -> list[BatchSchedulerFlowRecord]:
        normalized: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for flow_id, reason in skip_reasons.items():
            normalized_flow_id = str(flow_id or "").strip()
            normalized_reason = _normalize_scheduler_flow_skip_reason(reason)
            if not normalized_flow_id or not normalized_reason:
                continue
            key = (normalized_flow_id, normalized_reason)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)
            increment_batch_scheduler_flow_skip(reason=normalized_reason)
        if not normalized:
            return []
        params: list[Any] = []
        values_sql: list[str] = []
        for flow_id, reason in normalized:
            params.extend([flow_id, reason])
            values_sql.append(f"(${len(params) - 1}, ${len(params)})")
        rows = await db.query_raw(
            f"""
            WITH payload(flow_id, reason) AS (
                VALUES {", ".join(values_sql)}
            )
            UPDATE deltallm_batch_scheduler_flow f
            SET skip_reason_summary = COALESCE(f.skip_reason_summary, '{{}}'::jsonb)
                    || jsonb_build_object(
                        payload.reason,
                        COALESCE((f.skip_reason_summary->>payload.reason)::int, 0) + 1
                    ),
                updated_at = NOW()
            FROM payload
            WHERE f.flow_id = payload.flow_id
            RETURNING f.*, NULL::timestamp AS oldest_queue_entered_at, 1::int AS next_item_work_units
            """,
            *params,
        )
        updated = [flow_from_row(row) for row in rows]
        return updated

    @staticmethod
    def _terminal_empty_scheduler_flow_result(flow_selection: _SchedulerFlowSelection) -> str | None:
        if flow_selection.active_flow_count <= 0:
            return "no_active_flow"
        skipped_flow_count = len(flow_selection.skip_reasons)
        if (
            skipped_flow_count == flow_selection.active_flow_count
            and set(flow_selection.skip_reasons.values()) == {"tenant_in_flight_full"}
        ):
            return "tenant_in_flight_full"
        return None

    @staticmethod
    def _tenant_remaining_work_units(
        flow: BatchSchedulerFlowRecord,
        *,
        tenant_max_in_flight_work_units: int,
    ) -> int | None:
        bounded_tenant_max_in_flight = max(0, int(tenant_max_in_flight_work_units or 0))
        if bounded_tenant_max_in_flight <= 0:
            return None
        return max(0, bounded_tenant_max_in_flight - max(0, int(flow.in_flight_work_units or 0)))

    @classmethod
    def _flow_claim_work_units(
        cls,
        flow: BatchSchedulerFlowRecord,
        *,
        max_work_units: int,
        tenant_max_in_flight_work_units: int,
        single_eligible_flow: bool,
    ) -> int:
        bounded_max_work_units = max(1, int(max_work_units))
        tenant_remaining = cls._tenant_remaining_work_units(
            flow,
            tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
        )
        if tenant_remaining is not None:
            bounded_max_work_units = min(bounded_max_work_units, max(0, tenant_remaining))
        if single_eligible_flow:
            return bounded_max_work_units
        return min(bounded_max_work_units, max(1, int(flow.deficit_work_units or 0)))

    @classmethod
    def _select_scheduler_flow(
        cls,
        flows: list[BatchSchedulerFlowRecord],
        *,
        max_work_units: int,
        tenant_max_in_flight_work_units: int,
        allow_deficit_bypass: bool,
        max_deficit_multiplier: int = 8,
        excluded_flow_ids: set[str] | None = None,
    ) -> _SchedulerFlowSelection:
        candidates: list[BatchSchedulerFlowRecord] = []
        oversized_candidate_flow_ids: set[str] = set()
        skip_reasons: dict[str, str] = {}
        eligible_flow_count = 0
        bounded_max_work_units = max(1, int(max_work_units))
        excluded = set(excluded_flow_ids or set())
        active_flows = [
            flow
            for flow in flows
            if flow.active and flow.queued_jobs > 0 and flow.queued_work_units > 0
        ]
        single_active_flow = len(active_flows) == 1
        for flow in active_flows:
            if flow.flow_id in excluded:
                skip_reasons[flow.flow_id] = "empty_flow"
                continue
            tenant_remaining = cls._tenant_remaining_work_units(
                flow,
                tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
            )
            if tenant_remaining is not None and tenant_remaining <= 0:
                skip_reasons[flow.flow_id] = "tenant_in_flight_full"
                continue
            next_item_work_units = max(1, int(flow.next_item_work_units or 1))
            if tenant_remaining is not None and next_item_work_units > tenant_remaining:
                skip_reasons[flow.flow_id] = "tenant_in_flight_full"
                continue
            if allow_deficit_bypass:
                eligible_flow_count += 1
                candidates.append(flow)
                if next_item_work_units > bounded_max_work_units:
                    oversized_candidate_flow_ids.add(flow.flow_id)
                continue
            if next_item_work_units > bounded_max_work_units:
                oversized_deficit_target = min(
                    next_item_work_units,
                    max_deficit_for_flow(
                        quantum_work_units=flow.quantum_work_units,
                        max_deficit_multiplier=max_deficit_multiplier,
                    ),
                )
                if single_active_flow or flow.deficit_work_units >= oversized_deficit_target:
                    eligible_flow_count += 1
                    candidates.append(flow)
                    oversized_candidate_flow_ids.add(flow.flow_id)
                else:
                    skip_reasons[flow.flow_id] = "oversized_head_item"
                continue
            eligible_flow_count += 1
            if single_active_flow:
                candidates.append(flow)
                continue
            if flow.deficit_work_units >= next_item_work_units:
                candidates.append(flow)
            else:
                skip_reasons[flow.flow_id] = "insufficient_deficit"
        if not candidates:
            return _SchedulerFlowSelection(
                selected=None,
                skip_reasons=skip_reasons,
                active_flow_count=len(active_flows),
                eligible_flow_count=eligible_flow_count,
            )
        selected = sorted(
            candidates,
            key=lambda flow: (
                flow.last_selected_at or datetime.min.replace(tzinfo=UTC),
                flow.oldest_queue_entered_at or datetime.max.replace(tzinfo=UTC),
                flow.flow_id,
            ),
        )[0]
        return _SchedulerFlowSelection(
            selected=selected,
            skip_reasons=skip_reasons,
            active_flow_count=len(active_flows),
            eligible_flow_count=eligible_flow_count,
            selected_uses_oversized_first_item=selected.flow_id in oversized_candidate_flow_ids,
        )

    async def _refill_scheduler_flow_deficits(
        self,
        db: Any,
        *,
        service_tier: str,
        model_group: str,
        max_deficit_multiplier: int,
    ) -> int:
        rows = await db.query_raw(
            """
            UPDATE deltallm_batch_scheduler_flow
            SET deficit_work_units = LEAST(
                    deficit_work_units + quantum_work_units,
                    quantum_work_units * $3::int
                ),
                last_refilled_at = NOW(),
                updated_at = NOW()
            WHERE service_tier = $1
              AND model_group = $2
              AND active = true
              AND queued_jobs > 0
            RETURNING flow_id
            """,
            service_tier,
            model_group,
            max(1, int(max_deficit_multiplier)),
        )
        return len(rows or [])

    async def _claim_scheduler_flow_with_client(
        self,
        db: Any,
        *,
        flow: BatchSchedulerFlowRecord,
        worker_id: str,
        max_items: int,
        max_work_units: int,
        lease_seconds: int,
        capacity_max_in_flight_items: int | None,
        capacity_max_in_flight_work_units: int | None,
        allow_oversized_first_item: bool,
        max_deficit_multiplier: int,
    ) -> BatchFairShareClaimResult:
        claim = await self._claim_next_work_with_client(
            db,
            worker_id=worker_id,
            max_items=max_items,
            max_work_units=max_work_units,
            lease_seconds=lease_seconds,
            allowed_model_groups=[flow.model_group],
            service_tier=flow.service_tier,
            claim_order="fifo",
            capacity_model_group=flow.model_group,
            capacity_service_tier=flow.service_tier,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
            tenant_scope_type=flow.tenant_scope_type,
            tenant_scope_id=flow.tenant_scope_id,
            allow_oversized_first_item=allow_oversized_first_item,
        )
        if claim is None:
            await self._record_scheduler_flow_skip_reasons(db, {flow.flow_id: "empty_flow"})
            increment_batch_scheduler_flow_claim(
                model_group=flow.model_group,
                service_tier=flow.service_tier,
                tenant_scope_type=flow.tenant_scope_type,
                result="empty",
            )
            return BatchFairShareClaimResult(claim=None, result="empty_flow", flow=flow)
        max_negative_deficit = max_deficit_for_flow(
            quantum_work_units=flow.quantum_work_units,
            max_deficit_multiplier=max_deficit_multiplier,
        )
        rows = await db.query_raw(
            """
            UPDATE deltallm_batch_scheduler_flow
            SET deficit_work_units = GREATEST(
                    deficit_work_units - $2::int,
                    ($3::int * -1)
                ),
                last_selected_at = NOW(),
                updated_at = NOW()
            WHERE flow_id = $1
            RETURNING *
            """,
            flow.flow_id,
            max(1, int(claim.claimed_work_units or 1)),
            max_negative_deficit,
        )
        updated_flow = flow_from_row(rows[0]) if rows else flow
        await self._publish_scheduler_flow_metric_group(
            db,
            service_tier=flow.service_tier,
            model_group=flow.model_group,
            tenant_scope_type=flow.tenant_scope_type,
        )
        increment_batch_scheduler_flow_claim(
            model_group=flow.model_group,
            service_tier=flow.service_tier,
            tenant_scope_type=flow.tenant_scope_type,
            result="claimed",
        )
        if flow.oldest_queue_entered_at is not None:
            observe_batch_scheduler_flow_wait(
                model_group=flow.model_group,
                service_tier=flow.service_tier,
                tenant_scope_type=flow.tenant_scope_type,
                wait_seconds=max(
                    0.0,
                    (datetime.now(tz=UTC) - flow.oldest_queue_entered_at).total_seconds(),
                ),
            )
        await self._observe_scheduler_fairness_ratio_for_claim(db, flow=flow, claim=claim)
        return BatchFairShareClaimResult(claim=claim, result="claimed", flow=updated_flow)

    async def _observe_scheduler_fairness_ratio_for_claim(
        self,
        db: Any,
        *,
        flow: BatchSchedulerFlowRecord,
        claim: BatchWorkClaim,
    ) -> None:
        flows = await self.list_scheduler_flows(
            service_tier=flow.service_tier,
            model_group=flow.model_group,
            active=True,
            db=db,
        )
        active_flows = [candidate for candidate in flows if candidate.active]
        if not active_flows:
            active_flows = [flow]
        total_weight = sum(max(1, int(candidate.weight or 1)) for candidate in active_flows)
        if total_weight <= 0:
            return
        selected_flow = next(
            (candidate for candidate in active_flows if candidate.flow_id == flow.flow_id),
            flow,
        )
        selected_weight = max(1, int(selected_flow.weight or flow.weight or 1))
        claimed_work_units = max(1, int(claim.claimed_work_units or 1))
        total_work_units = (
            sum(max(0, int(candidate.in_flight_work_units or 0)) for candidate in active_flows)
            + claimed_work_units
        )
        if total_work_units <= 0:
            return
        selected_work_units = (
            max(0, int(selected_flow.in_flight_work_units or flow.in_flight_work_units or 0))
            + claimed_work_units
        )
        expected_share = float(selected_weight) / float(total_weight)
        actual_share = float(selected_work_units) / float(total_work_units)
        observe_batch_scheduler_fairness_ratio(
            model_group=flow.model_group,
            service_tier=flow.service_tier,
            ratio=actual_share / expected_share,
        )

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
        tenant_scope_type: str | None = None,
        tenant_scope_id: str | None = None,
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
                    tenant_scope_type=tenant_scope_type,
                    tenant_scope_id=tenant_scope_id,
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
            tenant_scope_type=tenant_scope_type,
            tenant_scope_id=tenant_scope_id,
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
        tenant_scope_type: str | None = None,
        tenant_scope_id: str | None = None,
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
        tenant_filter_sql = ""
        normalized_tenant_scope_type = str(tenant_scope_type or "").strip()
        normalized_tenant_scope_id = str(tenant_scope_id or "").strip()
        if normalized_tenant_scope_type and normalized_tenant_scope_id:
            params.append(normalized_tenant_scope_type)
            tenant_scope_type_param = len(params)
            params.append(normalized_tenant_scope_id)
            tenant_scope_id_param = len(params)
            tenant_filter_sql = (
                f"AND j.tenant_scope_type = ${tenant_scope_type_param} "
                f"AND j.tenant_scope_id = ${tenant_scope_id_param}"
            )
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
                  {tenant_filter_sql}
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
