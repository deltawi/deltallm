from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from src.batch.scheduling import (
    API_KEY_TENANT_SCOPE_PREFIX,
    ESTIMATOR_VERSION,
    MIXED_MODEL_GROUP,
    advisory_lock_acquires_legacy,
    advisory_lock_key,
    advisory_lock_legacy_parts,
    build_scheduling_dimensions,
    estimate_request_work_units,
    parse_advisory_lock_bool,
    parse_tenant_scope_preference,
    resolve_model_group,
)

_BACKFILL_LOCK_SCOPE = "batch_scheduler"
_BACKFILL_LOCK_NAME = "scheduler_dimensions_backfill"


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _model_group_filter_values(
    model_group: str | None,
    model_group_resolver: Any | None,
) -> list[str]:
    normalized_model_group = str(model_group or "").strip()
    if not normalized_model_group:
        return []
    values = {normalized_model_group}
    resolver_config = getattr(model_group_resolver, "config", None)
    aliases = getattr(resolver_config, "model_group_alias", None)
    if isinstance(aliases, Mapping):
        for model_name, resolved_group in aliases.items():
            if str(resolved_group or "").strip() == normalized_model_group:
                normalized_model = str(model_name or "").strip()
                if normalized_model:
                    values.add(normalized_model)
    return sorted(values)


class BatchMaintenanceRepository:
    def __init__(
        self,
        prisma_client: Any | None = None,
        *,
        model_group_resolver: Any | None = None,
        tenant_scope_preference: tuple[str, ...] | list[str] | str | None = None,
    ) -> None:
        self.prisma = prisma_client
        self.model_group_resolver = model_group_resolver
        self.tenant_scope_preference = parse_tenant_scope_preference(tenant_scope_preference)

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

    async def backfill_scheduler_dimensions(
        self,
        *,
        limit: int = 500,
        service_tier: str | None = None,
        model_group: str | None = None,
    ) -> dict[str, int]:
        if self.prisma is None:
            return {"jobs": 0, "items": 0}
        bounded_limit = max(1, min(int(limit), 5_000))
        normalized_service_tier = str(service_tier or "").strip() or None
        normalized_model_group = str(model_group or "").strip() or None
        if hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await self._backfill_scheduler_dimensions_locked(
                    tx,
                    limit=bounded_limit,
                    service_tier=normalized_service_tier,
                    model_group=normalized_model_group,
                )
        return await self._backfill_scheduler_dimensions_locked(
            self.prisma,
            limit=bounded_limit,
            service_tier=normalized_service_tier,
            model_group=normalized_model_group,
        )

    async def _try_backfill_lock(self, prisma: Any) -> bool:
        lock_key = advisory_lock_key(_BACKFILL_LOCK_SCOPE, _BACKFILL_LOCK_NAME)
        if not advisory_lock_acquires_legacy():
            rows = await prisma.query_raw(
                """
                SELECT pg_try_advisory_xact_lock($1::bigint)::bool AS acquired
                """,
                lock_key,
            )
            if not rows:
                return False
            row = dict(rows[0])
            return parse_advisory_lock_bool(row.get("acquired"))
        legacy_first, legacy_second = advisory_lock_legacy_parts(
            _BACKFILL_LOCK_SCOPE,
            _BACKFILL_LOCK_NAME,
        )
        rows = await prisma.query_raw(
            """
            WITH legacy_lock AS (
                SELECT pg_try_advisory_xact_lock(hashtext($1), hashtext($2))::bool AS acquired
            ),
            canonical_lock AS (
                SELECT
                    legacy_lock.acquired AS legacy_acquired,
                    CASE
                    WHEN legacy_lock.acquired THEN pg_try_advisory_xact_lock($3::bigint)::bool
                    ELSE false
                    END AS canonical_acquired
                FROM legacy_lock
            )
            SELECT (legacy_acquired AND canonical_acquired)::bool AS acquired
            FROM canonical_lock
            """,
            legacy_first,
            legacy_second,
            lock_key,
        )
        if not rows:
            return False
        row = dict(rows[0])
        return parse_advisory_lock_bool(
            row.get("acquired", row.get("pg_try_advisory_xact_lock"))
        )

    async def _backfill_active_items(
        self,
        prisma: Any,
        *,
        limit: int,
        service_tier: str | None,
        model_group: str | None,
    ) -> int:
        params: list[Any] = [limit]
        filters: list[str] = []
        model_filter_values = _model_group_filter_values(model_group, self.model_group_resolver)
        if model_filter_values:
            params.append(model_filter_values)
            filters.append(
                "COALESCE(NULLIF(i.scheduling_model_group, ''), "
                "NULLIF(j.scheduling_model_group, ''), NULLIF(i.scheduling_model, ''), "
                "NULLIF(i.request_body->>'model', ''), NULLIF(j.model, '')) "
                f"= ANY(${len(params)}::text[])"
            )
        if service_tier:
            params.append(service_tier)
            filters.append(f"COALESCE(NULLIF(j.service_tier, ''), 'standard') = ${len(params)}")
        filter_sql = "".join(f"\n                  AND {filter_clause}" for filter_clause in filters)
        candidates = await prisma.query_raw(
            f"""
            WITH candidate AS (
                SELECT
                    i.item_id,
                    i.request_body,
                    i.scheduling_model,
                    i.scheduling_model_group,
                    i.estimated_work_units,
                    j.endpoint,
                    j.model AS job_model,
                    COALESCE(j.queue_entered_at, j.created_at) AS queue_order,
                    j.created_at AS job_created_at,
                    i.line_number
                FROM deltallm_batch_item i
                JOIN deltallm_batch_job j ON j.batch_id = i.batch_id
                WHERE j.status IN ('queued', 'in_progress', 'finalizing')
                  {filter_sql}
                  AND (
                      i.scheduling_model IS NULL
                      OR i.scheduling_model = ''
                      OR i.scheduling_model_group IS NULL
                      OR i.scheduling_model_group = ''
                      OR i.estimated_work_units <= 0
                  )
                ORDER BY COALESCE(j.queue_entered_at, j.created_at) ASC NULLS FIRST,
                         j.created_at ASC,
                         i.line_number ASC
                FOR UPDATE OF i SKIP LOCKED
                LIMIT $1
            )
            SELECT * FROM candidate
            """,
            *params,
        )
        if not candidates:
            return 0

        values_sql: list[str] = []
        params: list[Any] = []
        param_index = 1
        for row in candidates:
            candidate = dict(row)
            request_body = _json_mapping(candidate.get("request_body"))
            scheduling_model = (
                str(
                    candidate.get("scheduling_model")
                    or request_body.get("model")
                    or candidate.get("job_model")
                    or ""
                ).strip()
                or None
            )
            scheduling_model_group = (
                str(candidate.get("scheduling_model_group") or "").strip()
                or resolve_model_group(scheduling_model, self.model_group_resolver)
            )
            estimated_from_body = estimate_request_work_units(candidate.get("endpoint"), request_body)
            estimated_work_units = max(
                1,
                int(candidate.get("estimated_work_units") or 0),
                estimated_from_body,
            )
            values_sql.append(
                f"(${param_index}, ${param_index + 1}, ${param_index + 2}, ${param_index + 3})"
            )
            params.extend(
                [
                    candidate.get("item_id"),
                    scheduling_model,
                    scheduling_model_group,
                    estimated_work_units,
                ]
            )
            param_index += 4

        rows = await prisma.query_raw(
            f"""
            WITH payload(item_id, scheduling_model, scheduling_model_group, estimated_work_units) AS (
                VALUES {", ".join(values_sql)}
            )
            UPDATE deltallm_batch_item i
            SET scheduling_model = COALESCE(NULLIF(i.scheduling_model, ''), p.scheduling_model),
                scheduling_model_group = COALESCE(NULLIF(i.scheduling_model_group, ''), p.scheduling_model_group),
                estimated_work_units = GREATEST(i.estimated_work_units, p.estimated_work_units, 1)
            FROM payload p
            WHERE i.item_id = p.item_id
            RETURNING i.item_id
            """,
            *params,
        )
        return len(rows)

    async def _backfill_active_jobs(
        self,
        prisma: Any,
        *,
        limit: int,
        service_tier: str | None,
        model_group: str | None,
    ) -> int:
        scan_limit = max(limit, min(limit * 10, 5_000))
        tenant_scope_preference_key = ",".join(self.tenant_scope_preference)
        params: list[Any] = [
            limit,
            scan_limit,
            ESTIMATOR_VERSION,
            f"{API_KEY_TENANT_SCOPE_PREFIX}%",
            tenant_scope_preference_key,
        ]
        filters: list[str] = []
        model_filter_values = _model_group_filter_values(model_group, self.model_group_resolver)
        if model_filter_values:
            params.append(model_filter_values)
            filters.append(
                "COALESCE(NULLIF(j.scheduling_model_group, ''), NULLIF(j.model, '')) "
                f"= ANY(${len(params)}::text[])"
            )
        if service_tier:
            params.append(service_tier)
            filters.append(f"COALESCE(NULLIF(j.service_tier, ''), 'standard') = ${len(params)}")
        filter_sql = "".join(f"\n                  AND {filter_clause}" for filter_clause in filters)
        job_candidates = await prisma.query_raw(
            f"""
            WITH field_candidate_jobs AS (
                SELECT
                    j.batch_id,
                    COALESCE(j.queue_entered_at, j.created_at) AS queue_order,
                    j.created_at
                FROM deltallm_batch_job j
                WHERE j.status IN ('queued', 'in_progress', 'finalizing')
                  {filter_sql}
                  AND (
                      j.scheduler_version IS NULL
                      OR j.scheduler_version = ''
                      OR j.scheduling_model IS NULL
                      OR j.scheduling_model = ''
                      OR j.scheduling_model_group IS NULL
                      OR j.scheduling_model_group = ''
                      OR j.scheduling_endpoint IS NULL
                      OR j.scheduling_endpoint = ''
                      OR j.tenant_scope_type IS NULL
                      OR j.tenant_scope_type = ''
                      OR j.tenant_scope_id IS NULL
                      OR j.tenant_scope_id = ''
                      OR j.service_tier IS NULL
                      OR j.service_tier = ''
                      OR j.size_class IS NULL
                      OR j.size_class = ''
                      OR j.queue_entered_at IS NULL
                      OR j.scheduler_debug IS NULL
                      OR j.scheduler_debug->>'estimator_version' IS DISTINCT FROM $3
                      OR j.scheduler_debug->>'tenant_scope_preference' IS DISTINCT FROM $5
                      OR (
                          j.tenant_scope_type = 'api_key'
                          AND j.tenant_scope_id IS NOT NULL
                          AND j.tenant_scope_id <> ''
                          AND j.tenant_scope_id NOT LIKE $4
                      )
                  )
                ORDER BY queue_order ASC NULLS FIRST,
                         j.created_at ASC
                LIMIT $2
            ),
            aggregate_candidate_jobs AS (
                SELECT
                    j.batch_id,
                    COALESCE(j.queue_entered_at, j.created_at) AS queue_order,
                    j.created_at
                FROM deltallm_batch_job j
                WHERE j.status IN ('queued', 'in_progress', 'finalizing')
                  {filter_sql}
                  AND (
                      j.estimated_work_units IS NULL
                      OR j.estimated_work_units <= 0
                      OR j.remaining_work_units IS NULL
                      OR j.remaining_work_units < 0
                      OR j.remaining_work_units > GREATEST(
                          COALESCE(j.estimated_work_units, 0),
                          COALESCE(j.total_items, 0),
                          0
                      )
                      OR j.size_class IS NULL
                      OR j.size_class = ''
                  )
                ORDER BY queue_order ASC NULLS FIRST,
                         j.created_at ASC
                LIMIT $2
            ),
            aggregate_drift_scan_jobs AS (
                SELECT
                    j.batch_id,
                    COALESCE(j.queue_entered_at, j.created_at) AS queue_order,
                    j.created_at
                FROM deltallm_batch_job j
                WHERE j.status IN ('queued', 'in_progress', 'finalizing')
                  {filter_sql}
                ORDER BY queue_order ASC NULLS FIRST,
                         j.created_at ASC
                LIMIT $2
            ),
            aggregate_drift_item_stats AS (
                SELECT
                    i.batch_id,
                    COALESCE(SUM(i.estimated_work_units), 0)::int AS estimated_work_units,
                    COALESCE(
                        SUM(i.estimated_work_units) FILTER (WHERE i.status IN ('pending', 'in_progress')),
                        0
                    )::int AS remaining_work_units,
                    COUNT(*) FILTER (
                        WHERE i.scheduling_model IS NULL
                           OR i.scheduling_model = ''
                           OR i.scheduling_model_group IS NULL
                           OR i.scheduling_model_group = ''
                           OR i.estimated_work_units <= 0
                    )::int AS missing_dimension_items
                FROM deltallm_batch_item i
                JOIN aggregate_drift_scan_jobs s ON s.batch_id = i.batch_id
                GROUP BY i.batch_id
            ),
            aggregate_drift_candidate_jobs AS (
                SELECT
                    s.batch_id,
                    s.queue_order,
                    s.created_at
                FROM aggregate_drift_scan_jobs s
                JOIN deltallm_batch_job j ON j.batch_id = s.batch_id
                JOIN aggregate_drift_item_stats dis ON dis.batch_id = s.batch_id
                WHERE dis.missing_dimension_items = 0
                  AND dis.estimated_work_units > 0
                  AND (
                      j.estimated_work_units IS DISTINCT FROM dis.estimated_work_units
                      OR j.remaining_work_units IS DISTINCT FROM GREATEST(dis.remaining_work_units, 0)
                      OR j.size_class IS DISTINCT FROM (
                          CASE
                          WHEN dis.estimated_work_units <= 10 THEN 'xs'
                          WHEN dis.estimated_work_units <= 100 THEN 's'
                          WHEN dis.estimated_work_units <= 1000 THEN 'm'
                          WHEN dis.estimated_work_units <= 10000 THEN 'l'
                          ELSE 'xl'
                          END
                      )
                  )
                ORDER BY s.queue_order ASC NULLS FIRST,
                         s.created_at ASC
                LIMIT $2
            ),
            tenant_scope_scan_jobs AS (
                SELECT
                    j.batch_id,
                    COALESCE(j.queue_entered_at, j.created_at) AS queue_order,
                    j.created_at
                FROM deltallm_batch_job j
                WHERE j.status IN ('queued', 'in_progress', 'finalizing')
                  {filter_sql}
                  AND j.tenant_scope_type IS NOT NULL
                  AND j.tenant_scope_type <> ''
                  AND j.tenant_scope_id IS NOT NULL
                  AND j.tenant_scope_id <> ''
                ORDER BY queue_order ASC NULLS FIRST,
                         j.created_at ASC
                LIMIT $2
            ),
            candidate_jobs AS (
                SELECT
                    targeted_jobs.batch_id,
                    MIN(targeted_jobs.candidate_priority) AS candidate_priority,
                    MIN(targeted_jobs.queue_order) AS queue_order,
                    MIN(targeted_jobs.created_at) AS created_at
                FROM (
                    SELECT batch_id, 0 AS candidate_priority, queue_order, created_at
                    FROM field_candidate_jobs
                    UNION ALL
                    SELECT batch_id, 0 AS candidate_priority, queue_order, created_at
                    FROM aggregate_candidate_jobs
                    UNION ALL
                    SELECT batch_id, 0 AS candidate_priority, queue_order, created_at
                    FROM aggregate_drift_candidate_jobs
                    UNION ALL
                    SELECT batch_id, 1 AS candidate_priority, queue_order, created_at
                    FROM tenant_scope_scan_jobs
                ) targeted_jobs
                GROUP BY targeted_jobs.batch_id
            ),
            item_stats AS (
                SELECT
                    i.batch_id,
                    COUNT(*)::int AS item_count,
                    COALESCE(SUM(i.estimated_work_units), 0)::int AS estimated_work_units,
                    COALESCE(
                        SUM(i.estimated_work_units) FILTER (WHERE i.status IN ('pending', 'in_progress')),
                        0
                    )::int AS remaining_work_units,
                    COUNT(*) FILTER (
                        WHERE i.scheduling_model IS NULL
                           OR i.scheduling_model = ''
                           OR i.scheduling_model_group IS NULL
                           OR i.scheduling_model_group = ''
                           OR i.estimated_work_units <= 0
                    )::int AS missing_dimension_items,
                    COUNT(DISTINCT NULLIF(i.scheduling_model, ''))::int AS distinct_models,
                    COUNT(DISTINCT NULLIF(i.scheduling_model_group, ''))::int AS distinct_model_groups,
                    MIN(NULLIF(i.scheduling_model, '')) AS scheduling_model,
                    MIN(NULLIF(i.scheduling_model_group, '')) AS scheduling_model_group
                FROM deltallm_batch_item i
                JOIN candidate_jobs s ON s.batch_id = i.batch_id
                GROUP BY i.batch_id
            ),
            candidate_input AS (
                SELECT
                    j.batch_id,
                    j.endpoint,
                    j.model,
                    j.created_by_organization_id,
                    j.created_by_team_id,
                    j.created_by_api_key,
                    j.created_by_user_id,
                    j.tenant_scope_type,
                    j.tenant_scope_id,
                    j.service_tier,
                    j.estimated_work_units,
                    j.remaining_work_units,
                    j.size_class,
                    j.scheduler_version,
                    j.scheduling_model,
                    j.scheduling_model_group,
                    j.scheduling_endpoint,
                    j.queue_entered_at,
                    j.scheduler_debug,
                    j.total_items,
                    j.created_at,
                    COALESCE(item_stats.item_count, 0)::int AS item_count,
                    COALESCE(item_stats.estimated_work_units, 0)::int AS item_estimated_work_units,
                    COALESCE(item_stats.remaining_work_units, 0)::int AS item_remaining_work_units,
                    COALESCE(item_stats.missing_dimension_items, 0)::int AS missing_dimension_items,
                    COALESCE(item_stats.distinct_models, 0)::int AS distinct_models,
                    COALESCE(item_stats.distinct_model_groups, 0)::int AS distinct_model_groups,
                    item_stats.scheduling_model AS item_scheduling_model,
                    item_stats.scheduling_model_group AS item_scheduling_model_group,
                    CASE
                        WHEN COALESCE(item_stats.estimated_work_units, 0) > 0
                            THEN COALESCE(item_stats.estimated_work_units, 0)::int
                        ELSE GREATEST(COALESCE(j.estimated_work_units, 0), COALESCE(j.total_items, 0), 0)::int
                    END AS derived_estimated_work_units,
                    CASE
                        WHEN COALESCE(item_stats.estimated_work_units, 0) > 0
                            THEN GREATEST(COALESCE(item_stats.remaining_work_units, 0), 0)::int
                        ELSE GREATEST(COALESCE(j.remaining_work_units, 0), 0)::int
                    END AS derived_remaining_work_units,
                    s.queue_order,
                    s.candidate_priority
                FROM candidate_jobs s
                JOIN deltallm_batch_job j ON j.batch_id = s.batch_id
                LEFT JOIN item_stats ON item_stats.batch_id = j.batch_id
            ),
            candidate AS (
                SELECT ci.*
                FROM candidate_input ci
                JOIN deltallm_batch_job j ON j.batch_id = ci.batch_id
                WHERE ci.missing_dimension_items = 0
                  AND (
                      ci.scheduler_version IS NULL
                      OR ci.scheduler_version = ''
                      OR ci.scheduling_model IS NULL
                      OR ci.scheduling_model = ''
                      OR ci.scheduling_model_group IS NULL
                      OR ci.scheduling_model_group = ''
                      OR ci.scheduling_endpoint IS NULL
                      OR ci.scheduling_endpoint = ''
                      OR ci.tenant_scope_type IS NULL
                      OR ci.tenant_scope_type = ''
                      OR ci.tenant_scope_id IS NULL
                      OR ci.tenant_scope_id = ''
                      OR ci.service_tier IS NULL
                      OR ci.service_tier = ''
                      OR ci.estimated_work_units IS DISTINCT FROM ci.derived_estimated_work_units
                      OR ci.remaining_work_units IS DISTINCT FROM ci.derived_remaining_work_units
                      OR ci.remaining_work_units < 0
                      OR ci.size_class IS DISTINCT FROM (
                          CASE
                          WHEN ci.derived_estimated_work_units <= 10 THEN 'xs'
                          WHEN ci.derived_estimated_work_units <= 100 THEN 's'
                          WHEN ci.derived_estimated_work_units <= 1000 THEN 'm'
                          WHEN ci.derived_estimated_work_units <= 10000 THEN 'l'
                          ELSE 'xl'
                          END
                      )
                      OR ci.queue_entered_at IS NULL
                      OR ci.scheduler_debug IS NULL
                      OR ci.scheduler_debug->>'estimator_version' IS DISTINCT FROM $3
                      OR ci.scheduler_debug->>'tenant_scope_preference' IS DISTINCT FROM $5
                      OR (
                          ci.tenant_scope_type = 'api_key'
                          AND ci.tenant_scope_id IS NOT NULL
                          AND ci.tenant_scope_id <> ''
                          AND ci.tenant_scope_id NOT LIKE $4
                      )
                      OR ci.candidate_priority = 1
                  )
                ORDER BY ci.candidate_priority ASC,
                         ci.queue_order ASC NULLS FIRST,
                         ci.created_at ASC
                FOR UPDATE OF j SKIP LOCKED
                LIMIT $1
            )
            SELECT
                batch_id,
                endpoint,
                model,
                created_by_organization_id,
                created_by_team_id,
                created_by_api_key,
                created_by_user_id,
                tenant_scope_type,
                tenant_scope_id,
                service_tier,
                estimated_work_units,
                remaining_work_units,
                total_items,
                created_at,
                item_count,
                item_estimated_work_units,
                item_remaining_work_units,
                missing_dimension_items,
                distinct_models,
                distinct_model_groups,
                item_scheduling_model,
                item_scheduling_model_group
            FROM candidate c
            """,
            *params,
        )
        jobs = 0
        for row in job_candidates:
            candidate = dict(row)
            if int(candidate.get("missing_dimension_items") or 0) > 0:
                continue
            existing_tenant_scope_type = str(candidate.get("tenant_scope_type") or "").strip()
            existing_tenant_scope_id = str(candidate.get("tenant_scope_id") or "").strip()
            item_estimated_work_units = int(candidate.get("item_estimated_work_units") or 0)
            item_remaining_work_units = int(candidate.get("item_remaining_work_units") or 0)
            estimated_work_units = (
                max(0, item_estimated_work_units)
                if item_estimated_work_units > 0
                else max(
                    int(candidate.get("estimated_work_units") or 0),
                    int(candidate.get("total_items") or 0),
                    0,
                )
            )
            remaining_work_units = (
                max(0, item_remaining_work_units)
                if item_estimated_work_units > 0
                else max(int(candidate.get("remaining_work_units") or 0), 0)
            )
            mixed_model = (
                int(candidate.get("distinct_models") or 0) > 1
                or int(candidate.get("distinct_model_groups") or 0) > 1
            )
            scheduling_model = (
                MIXED_MODEL_GROUP
                if mixed_model
                else str(candidate.get("item_scheduling_model") or candidate.get("model") or "").strip() or None
            )
            scheduling_model_group = (
                MIXED_MODEL_GROUP
                if mixed_model
                else str(candidate.get("item_scheduling_model_group") or "").strip()
                or resolve_model_group(scheduling_model, self.model_group_resolver)
            )
            candidate_api_key = (
                existing_tenant_scope_id
                if existing_tenant_scope_type == "api_key" and existing_tenant_scope_id
                else candidate.get("created_by_api_key")
            )
            dimensions = build_scheduling_dimensions(
                endpoint=str(candidate.get("endpoint") or ""),
                model=scheduling_model,
                model_group=scheduling_model_group,
                organization_id=candidate.get("created_by_organization_id"),
                team_id=candidate.get("created_by_team_id"),
                api_key=candidate_api_key,
                user_id=candidate.get("created_by_user_id"),
                service_tier=candidate.get("service_tier"),
                estimated_work_units=estimated_work_units,
                remaining_work_units=remaining_work_units,
                estimator_version=ESTIMATOR_VERSION,
                mixed_model=mixed_model,
                tenant_scope_preference=self.tenant_scope_preference,
            )
            tenant_scope_type = dimensions.tenant_scope_type
            tenant_scope_id = dimensions.tenant_scope_id
            updated = await prisma.query_raw(
                """
                UPDATE deltallm_batch_job
                SET scheduler_version = COALESCE(NULLIF(scheduler_version, ''), $2),
                    scheduling_model = $3,
                    scheduling_model_group = $4,
                    scheduling_endpoint = COALESCE(NULLIF(scheduling_endpoint, ''), $5),
                    tenant_scope_type = $6,
                    tenant_scope_id = $7,
                    service_tier = COALESCE(NULLIF(service_tier, ''), $8),
                    estimated_work_units = GREATEST($9, 0),
                    remaining_work_units = GREATEST($10, 0),
                    size_class = $11,
                    queue_entered_at = COALESCE(queue_entered_at, $12::timestamp),
                    scheduler_debug = COALESCE(scheduler_debug, '{}'::jsonb) || $13::jsonb
                WHERE batch_id = $1
                  AND status IN ('queued', 'in_progress', 'finalizing')
                  AND (
                      scheduler_version IS NULL
                      OR scheduler_version = ''
                      OR scheduling_model IS DISTINCT FROM $3
                      OR scheduling_model_group IS DISTINCT FROM $4
                      OR scheduling_endpoint IS NULL
                      OR scheduling_endpoint = ''
                      OR tenant_scope_type IS NULL
                      OR tenant_scope_type = ''
                      OR tenant_scope_type IS DISTINCT FROM $6
                      OR tenant_scope_id IS NULL
                      OR tenant_scope_id = ''
                      OR tenant_scope_id IS DISTINCT FROM $7
                      OR service_tier IS NULL
                      OR service_tier = ''
                      OR estimated_work_units IS DISTINCT FROM GREATEST($9, 0)
                      OR remaining_work_units IS DISTINCT FROM GREATEST($10, 0)
                      OR size_class IS DISTINCT FROM $11
                      OR queue_entered_at IS NULL
                      OR (
                          tenant_scope_type = 'api_key'
                          AND tenant_scope_id IS NOT NULL
                          AND tenant_scope_id <> ''
                          AND tenant_scope_id NOT LIKE $14
                      )
                      OR (
                          $13::jsonb ? 'mixed_model'
                          AND scheduler_debug->>'mixed_model' IS DISTINCT FROM $13::jsonb->>'mixed_model'
                      )
                      OR (
                          $13::jsonb ? 'estimator_version'
                          AND scheduler_debug->>'estimator_version' IS DISTINCT FROM $13::jsonb->>'estimator_version'
                      )
                      OR (
                          $13::jsonb ? 'tenant_scope_preference'
                          AND scheduler_debug->>'tenant_scope_preference' IS DISTINCT FROM $13::jsonb->>'tenant_scope_preference'
                      )
                  )
                RETURNING batch_id
                """,
                candidate.get("batch_id"),
                dimensions.scheduler_version,
                dimensions.scheduling_model,
                dimensions.scheduling_model_group,
                dimensions.scheduling_endpoint,
                tenant_scope_type,
                tenant_scope_id,
                dimensions.service_tier,
                dimensions.estimated_work_units,
                dimensions.remaining_work_units,
                dimensions.size_class,
                candidate.get("created_at"),
                json.dumps(dimensions.scheduler_debug or {}),
                f"{API_KEY_TENANT_SCOPE_PREFIX}%",
            )
            jobs += len(updated)
        return jobs

    async def _backfill_scheduler_dimensions_locked(
        self,
        prisma: Any,
        *,
        limit: int,
        service_tier: str | None,
        model_group: str | None,
    ) -> dict[str, int]:
        if not await self._try_backfill_lock(prisma):
            return {"jobs": 0, "items": 0, "skipped": 1}
        items = await self._backfill_active_items(
            prisma,
            limit=limit,
            service_tier=service_tier,
            model_group=model_group,
        )
        jobs = await self._backfill_active_jobs(
            prisma,
            limit=limit,
            service_tier=service_tier,
            model_group=model_group,
        )
        return {"jobs": jobs, "items": items}
