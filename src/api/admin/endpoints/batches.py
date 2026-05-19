from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import UTC, datetime
import logging
from typing import Any, Iterator
from time import perf_counter

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.auth.roles import Permission
from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit, to_json_value, get_auth_scope
from src.audit.actions import AuditAction
from src.batch.repository import BatchRepository
from src.batch.models import BATCH_JOB_STATUS_SET, decode_operator_failed_reason, encode_operator_failed_reason
from src.batch.scheduling import (
    API_KEY_TENANT_SCOPE_PREFIX,
    BatchJobRankInput,
    BatchModelCapacityConfig,
    BatchModelCapacityResolver,
    BatchSizeAgingConfig,
    BatchTenantFairShareConfig,
    SchedulerMode,
    SchedulerShadowMode,
    calculate_size_aging_rank,
    resolve_scheduler_modes_from_settings,
    scheduler_config_fingerprint,
    scheduler_mode_uses_fair_share,
    scheduler_mode_uses_model_capacity,
    scheduler_mode_uses_size_aware,
    scheduler_mode_uses_work_slice,
)
from src.config_runtime.dynamic import DynamicConfigPersistenceError, DynamicConfigValidationError
from src.metrics import (
    collect_batch_scheduler_status_metrics,
    increment_batch_repair_action,
    publish_batch_runtime_summary,
)
from src.middleware.admin import require_admin_permission
from src.services.ui_authorization import build_batch_capabilities

router = APIRouter(tags=["Admin Batches"])
logger = logging.getLogger(__name__)
SCHEDULER_FLOW_LIST_DEFAULT_LIMIT = 200
SCHEDULER_FLOW_LIST_MAX_LIMIT = 1000


class MarkBatchFailedRequest(BaseModel):
    reason: str | None = None


class SchedulerBackfillRequest(BaseModel):
    limit: int = Field(default=500, ge=1, le=5_000)


class SchedulerFlowRefreshRequest(BaseModel):
    model_group: str | None = Field(default=None, max_length=200)
    service_tier: str | None = Field(default=None, max_length=100)
    repair_limit: int = Field(default=500, ge=1, le=5_000)


class SchedulerModeUpdateRequest(BaseModel):
    active_mode: SchedulerMode
    shadow_mode: SchedulerShadowMode = "none"
    reason: str | None = Field(default=None, max_length=500)


async def _refresh_batch_runtime_metrics(repository: BatchRepository) -> None:
    try:
        summary = await repository.summarize_runtime_statuses(now=datetime.now(tz=UTC))
        publish_batch_runtime_summary(summary)
    except Exception:
        logger.debug("batch admin runtime metrics refresh failed", exc_info=True)
        return


@contextlib.contextmanager
def _repair_action_metric(action: str) -> Iterator[None]:
    """Records a repair action outcome as success unless an unexpected error escapes.

    HTTPExceptions are treated as client-visible validation and do not flip the
    counter to error — only unexpected exceptions do.
    """
    try:
        yield
    except HTTPException:
        raise
    except Exception:
        increment_batch_repair_action(action=action, status="error")
        raise
    increment_batch_repair_action(action=action, status="success")


def _batch_repository_or_503(request: Request) -> BatchRepository:
    repository = getattr(request.app.state, "batch_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Batch repository unavailable")
    return repository


def _mask_api_key(raw_api_key: str | None) -> str:
    api_key = raw_api_key or ""
    return f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else api_key


def _display_tenant_scope_id(*, scope_type: str | None, scope_id: str | None) -> str | None:
    normalized_scope_type = str(scope_type or "").strip()
    normalized_scope_id = str(scope_id or "").strip()
    if not normalized_scope_id:
        return None
    if normalized_scope_type != "api_key":
        return normalized_scope_id
    if normalized_scope_id.startswith(API_KEY_TENANT_SCOPE_PREFIX):
        digest = normalized_scope_id[len(API_KEY_TENANT_SCOPE_PREFIX) :]
        return f"api_key:{digest[:12]}" if digest else "api_key"
    return "api_key"


def _scheduler_debug_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _batch_size_aging_config_or_default(request: Request) -> BatchSizeAgingConfig:
    config = getattr(request.app.state, "batch_size_aging_config", None)
    if isinstance(config, BatchSizeAgingConfig):
        return config
    return BatchSizeAgingConfig()


def _scheduler_policy_fields(
    row: dict[str, Any],
    *,
    size_aging_config: BatchSizeAgingConfig | None = None,
) -> dict[str, Any]:
    config = size_aging_config or BatchSizeAgingConfig()
    debug = _scheduler_debug_dict(row.get("scheduler_debug"))
    queue_entered_at = row.get("queue_entered_at")
    age_seconds: float | None = None
    if isinstance(queue_entered_at, datetime):
        age_seconds = max(0.0, (datetime.now(tz=UTC) - queue_entered_at).total_seconds())
    scheduler_rank = debug.get("scheduler_rank")
    age_credit_work_units = debug.get("age_credit_work_units")
    next_policy_reason = debug.get("next_policy_reason")
    if scheduler_rank is None or age_credit_work_units is None or not next_policy_reason:
        rank_result = calculate_size_aging_rank(
            BatchJobRankInput(
                remaining_work_units=max(
                    1,
                    int(
                        row.get("remaining_work_units")
                        or row.get("estimated_work_units")
                        or row.get("total_items")
                        or 1
                    ),
                ),
                queue_entered_at=queue_entered_at if isinstance(queue_entered_at, datetime) else None,
                last_scheduled_at=(
                    row.get("last_scheduled_at")
                    if isinstance(row.get("last_scheduled_at"), datetime)
                    else None
                ),
                size_class=str(row.get("size_class") or "unknown"),
            ),
            aging_seconds_per_work_unit=config.aging_seconds_per_work_unit,
            max_age_credit_work_units=config.max_age_credit_work_units,
            min_large_job_claim_interval_seconds=config.min_large_job_claim_interval_seconds,
            small_job_max_work_units=config.small_job_max_work_units,
        )
        if scheduler_rank is None:
            scheduler_rank = rank_result.rank
        if age_credit_work_units is None:
            age_credit_work_units = rank_result.age_credit_work_units
        if not next_policy_reason:
            next_policy_reason = rank_result.policy_reason
    return {
        "age_seconds": age_seconds,
        "age_credit_work_units": age_credit_work_units,
        "scheduler_rank": scheduler_rank,
        "scheduler_rank_updated_at": debug.get("scheduler_rank_updated_at"),
        "next_policy_reason": next_policy_reason or "tenant_fair_share",
    }


def _redacted_tenant_scope_id(*, scope_type: str | None, scope_id: str | None) -> str | None:
    normalized_scope_type = str(scope_type or "").strip()
    normalized_scope_id = str(scope_id or "").strip()
    if not normalized_scope_id:
        return None
    if normalized_scope_type == "api_key":
        return _display_tenant_scope_id(scope_type=normalized_scope_type, scope_id=normalized_scope_id)
    if normalized_scope_type == "anonymous" and normalized_scope_id == "anonymous":
        return "anonymous"
    digest = hashlib.sha256(normalized_scope_id.encode("utf-8")).hexdigest()
    label = normalized_scope_type or "tenant"
    return f"{label}:{digest[:12]}"


def _model_capacity_resolver_or_503(request: Request, repository: BatchRepository) -> BatchModelCapacityResolver:
    resolver = getattr(request.app.state, "batch_model_capacity_resolver", None)
    if resolver is not None:
        return resolver
    general_settings = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
    if general_settings is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="App config unavailable")
    return BatchModelCapacityResolver(
        repository=repository,
        config=BatchModelCapacityConfig.from_settings(general_settings),
        router=getattr(request.app.state, "router", None),
        router_state_backend=getattr(request.app.state, "router_state_backend", None),
        backpressure=getattr(request.app.state, "batch_backpressure", None),
    )


def _tenant_fair_share_config_or_503(request: Request) -> BatchTenantFairShareConfig:
    config = getattr(request.app.state, "batch_tenant_fair_share_config", None)
    if config is not None:
        return config
    general_settings = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
    if general_settings is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="App config unavailable")
    return BatchTenantFairShareConfig.from_settings(general_settings)


def _dynamic_config_or_503(request: Request):  # noqa: ANN202
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    update_config = getattr(dynamic_config, "update_config", None)
    if dynamic_config is None or not callable(update_config):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable")
    return dynamic_config


def _dynamic_config_generation(request: Request) -> int | None:
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    get_config_generation = getattr(dynamic_config, "get_config_generation", None)
    if not callable(get_config_generation):
        return None
    try:
        return int(get_config_generation())
    except Exception:
        logger.debug("batch scheduler config generation status lookup failed", exc_info=True)
        return None


def _capacity_snapshot_response(snapshot) -> dict[str, Any]:  # noqa: ANN001
    return {
        "model_group": snapshot.model_group,
        "service_tier": snapshot.service_tier,
        "queued_jobs": snapshot.queued_jobs,
        "queued_work_units": snapshot.queued_work_units,
        "in_flight_items": snapshot.in_flight_items,
        "in_flight_work_units": snapshot.in_flight_work_units,
        "capacity_source": snapshot.capacity_source,
        "max_in_flight": snapshot.max_in_flight_items,
        "max_claim_work_units": snapshot.max_claim_work_units,
        "available_in_flight": snapshot.available_in_flight_items,
        "available_work_units": snapshot.available_work_units,
        "rpm_remaining": snapshot.rpm_remaining,
        "tpm_remaining": snapshot.tpm_remaining,
        "healthy_deployments": snapshot.healthy_deployments,
        "backpressure_until": to_json_value(snapshot.backpressure_until),
        "skip_reason": snapshot.reason,
        "skip_reason_summary": dict(snapshot.skip_reasons or {}),
        "last_selected_at": to_json_value(snapshot.last_selected_at),
        "oldest_queue_entered_at": to_json_value(snapshot.oldest_queue_entered_at),
    }


def _scheduler_status_response(request: Request) -> dict[str, Any]:
    general_settings = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
    if general_settings is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="App config unavailable")
    modes = resolve_scheduler_modes_from_settings(general_settings)
    model_capacity = BatchModelCapacityConfig.from_settings(general_settings)
    fair_share = BatchTenantFairShareConfig.from_settings(general_settings)
    size_aging = BatchSizeAgingConfig.from_settings(general_settings)
    config_hash = scheduler_config_fingerprint(general_settings)
    config_generation = _dynamic_config_generation(request)
    worker = getattr(getattr(request.app.state, "batch_runtime", None), "worker", None)
    worker_id = None
    worker_active_mode = None
    worker_shadow_mode = None
    worker_config_hash = None
    worker_config_generation = None
    if worker is not None:
        worker_id = getattr(getattr(worker, "config", None), "worker_id", None)
        worker_active_mode = worker._active_scheduler_mode()
        worker_shadow_mode = worker._shadow_scheduler_mode()
        worker_config_hash = getattr(worker, "_scheduler_config_hash", lambda: "unknown")()
        worker_config_generation = getattr(
            worker,
            "_scheduler_config_generation",
            lambda: None,
        )()
    return {
        "active_mode": modes.active_mode,
        "shadow_mode": modes.shadow_mode,
        "config": {
            "hash": config_hash,
            "generation": config_generation,
        },
        "local_worker": {
            "present": worker is not None,
            "worker_id": worker_id,
            "active_mode": worker_active_mode,
            "shadow_mode": worker_shadow_mode,
            "config_hash": worker_config_hash,
            "config_generation": worker_config_generation,
            "matches_config": (
                worker is not None
                and worker_active_mode == modes.active_mode
                and worker_shadow_mode == modes.shadow_mode
                and worker_config_hash == config_hash
            ),
        },
        "effective": {
            "work_slice_claims": scheduler_mode_uses_work_slice(modes.active_mode),
            "model_capacity_gates": scheduler_mode_uses_model_capacity(modes.active_mode),
            "tenant_fair_share": scheduler_mode_uses_fair_share(modes.active_mode),
            "size_aware_ranking": scheduler_mode_uses_size_aware(modes.active_mode),
            "shadow_model_capacity_gates": scheduler_mode_uses_model_capacity(modes.shadow_mode),
            "shadow_tenant_fair_share": scheduler_mode_uses_fair_share(modes.shadow_mode),
            "shadow_size_aware_ranking": scheduler_mode_uses_size_aware(modes.shadow_mode),
        },
        "legacy_flags": {
            "embeddings_batch_scheduler_enabled": bool(
                getattr(general_settings, "embeddings_batch_scheduler_enabled", False)
            ),
            "embeddings_batch_scheduler_shadow_enabled": bool(
                getattr(general_settings, "embeddings_batch_scheduler_shadow_enabled", False)
            ),
            "embeddings_batch_model_capacity_enabled": bool(
                getattr(general_settings, "embeddings_batch_model_capacity_enabled", False)
            ),
            "embeddings_batch_tenant_fair_share_enabled": bool(
                getattr(general_settings, "embeddings_batch_tenant_fair_share_enabled", False)
            ),
            "embeddings_batch_size_aware_scheduling_enabled": bool(
                getattr(general_settings, "embeddings_batch_size_aware_scheduling_enabled", False)
            ),
        },
        "claim_mode": (
            "work_slice"
            if scheduler_mode_uses_work_slice(modes.active_mode)
            else getattr(general_settings, "embeddings_batch_scheduler_claim_mode", "job_fifo")
        ),
        "strict_model_homogeneity_enabled": bool(
            getattr(general_settings, "embeddings_batch_scheduler_strict_model_homogeneity_enabled", False)
        ),
        "model_capacity": {
            "enabled": model_capacity.enabled,
            "fail_open": model_capacity.fail_open,
            "default_model_max_in_flight": model_capacity.default_model_max_in_flight,
            "default_model_max_claim_work_units": model_capacity.default_model_max_claim_work_units,
            "capacity_fraction": model_capacity.capacity_fraction,
            "refresh_seconds": model_capacity.refresh_seconds,
        },
        "fair_share": {
            "enabled": fair_share.enabled,
            "base_quantum_work_units": fair_share.base_quantum_work_units,
            "max_deficit_multiplier": fair_share.max_deficit_multiplier,
            "tenant_max_in_flight_work_units": fair_share.tenant_max_in_flight_work_units,
            "tenant_max_queued_work_units": fair_share.tenant_max_queued_work_units,
            "max_active_flows_per_decision": fair_share.max_active_flows_per_decision,
            "max_candidate_jobs_per_flow": fair_share.max_candidate_jobs_per_flow,
            "tenant_scope_preference": list(fair_share.tenant_scope_preference),
            "disabled_model_groups": list(fair_share.disabled_model_groups),
        },
        "size_aging": {
            "enabled": size_aging.enabled,
            "aging_seconds_per_work_unit": size_aging.aging_seconds_per_work_unit,
            "max_age_credit_work_units": size_aging.max_age_credit_work_units,
            "min_large_job_claim_interval_seconds": (
                size_aging.min_large_job_claim_interval_seconds
            ),
            "small_job_fast_lane_enabled": size_aging.small_job_fast_lane_enabled,
            "small_job_max_work_units": size_aging.small_job_max_work_units,
        },
        "rollback": {
            "primary": "set embeddings_batch_scheduler_mode=fifo_v1",
            "disable_shadow": "set embeddings_batch_scheduler_shadow_mode=none",
        },
        "metrics": collect_batch_scheduler_status_metrics(),
    }


def _scheduler_flow_response(flow) -> dict[str, Any]:  # noqa: ANN001
    return {
        "flow_id": flow.flow_id,
        "service_tier": flow.service_tier,
        "model_group": flow.model_group,
        "tenant_scope_type": flow.tenant_scope_type,
        "tenant_scope_id": _redacted_tenant_scope_id(
            scope_type=flow.tenant_scope_type,
            scope_id=flow.tenant_scope_id,
        ),
        "active": flow.active,
        "queued_jobs": flow.queued_jobs,
        "queued_work_units": flow.queued_work_units,
        "in_flight_work_units": flow.in_flight_work_units,
        "weight": flow.weight,
        "quantum_work_units": flow.quantum_work_units,
        "deficit_work_units": flow.deficit_work_units,
        "last_selected_at": to_json_value(flow.last_selected_at),
        "last_refilled_at": to_json_value(flow.last_refilled_at),
        "oldest_queue_entered_at": to_json_value(flow.oldest_queue_entered_at),
        "next_item_work_units": flow.next_item_work_units,
        "next_batch_id": flow.next_batch_id,
        "next_size_class": flow.next_size_class,
        "next_scheduler_rank": flow.next_scheduler_rank,
        "next_age_credit_work_units": flow.next_age_credit_work_units,
        "next_policy_reason": flow.next_policy_reason,
        "skip_reason_summary": dict(flow.skip_reasons or {}),
        "updated_at": to_json_value(flow.updated_at),
    }


async def _load_batch_scope_row(db: Any, batch_id: str) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT batch_id, status, created_by_team_id, created_by_organization_id
        FROM deltallm_batch_job
        WHERE batch_id = $1
        LIMIT 1
        """,
        batch_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return dict(rows[0])


async def _enforce_batch_update_scope(*, db: Any, scope, job: dict[str, Any]) -> None:  # noqa: ANN001
    if scope.is_platform_admin:
        return
    team_id = job.get("created_by_team_id")
    organization_id = job.get("created_by_organization_id")
    if team_id and scope.team_ids and team_id in scope.team_ids:
        return
    if organization_id and scope.org_ids and organization_id in scope.org_ids:
        return
    if team_id and scope.org_ids:
        org_rows = await db.query_raw(
            "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
            team_id,
        )
        org_id = str((org_rows[0] if org_rows else {}).get("organization_id") or "")
        if org_id in scope.org_ids:
            return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


def _append_batch_scope_clause(*, clauses: list[str], params: list[Any], scope, job_alias: str = "") -> bool:  # noqa: ANN001
    if scope.is_platform_admin:
        return True

    team_column = f"{job_alias}created_by_team_id"
    org_column = f"{job_alias}created_by_organization_id"
    scope_clauses: list[str] = []

    if scope.team_ids:
        team_placeholders = ", ".join(f"${len(params) + index + 1}" for index in range(len(scope.team_ids)))
        params.extend(scope.team_ids)
        scope_clauses.append(f"{team_column} IN ({team_placeholders})")

    if scope.org_ids:
        org_placeholders = ", ".join(f"${len(params) + index + 1}" for index in range(len(scope.org_ids)))
        params.extend(scope.org_ids)
        scope_clauses.append(
            f"({org_column} IN ({org_placeholders}) "
            f"OR {team_column} IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN ({org_placeholders})))"
        )

    if not scope_clauses:
        return False

    clauses.append("(" + " OR ".join(scope_clauses) + ")")
    return True


@router.get("/ui/api/batches")
async def list_batches(
    request: Request,
    search: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=10, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    db = db_or_503(request)

    clauses: list[str] = []
    params: list[Any] = []

    if not _append_batch_scope_clause(clauses=clauses, params=params, scope=scope, job_alias="j."):
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if search:
        params.append(f"%{search}%")
        clauses.append(f"(j.batch_id ILIKE ${len(params)} OR j.model ILIKE ${len(params)})")

    if status_filter and status_filter in BATCH_JOB_STATUS_SET:
        params.append(status_filter)
        clauses.append(f'j.status = ${len(params)}::"DeltaLLM_BatchJobStatus"')

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    count_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM deltallm_batch_job j {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)
    size_aging_config = _batch_size_aging_config_or_default(request)

    params.append(limit)
    params.append(offset)
    rows = await db.query_raw(
        f"""
        SELECT j.batch_id, j.endpoint, j.status, j.model, j.execution_mode,
               j.total_items, j.completed_items, j.failed_items, j.cancelled_items, j.in_progress_items,
               j.scheduler_version, j.scheduling_model, j.scheduling_model_group, j.scheduling_endpoint,
               j.tenant_scope_type, j.tenant_scope_id, j.service_tier, j.estimated_work_units,
               j.remaining_work_units, j.size_class, j.queue_entered_at, j.first_claimed_at,
               j.last_claimed_at, j.last_scheduled_at, j.scheduler_debug,
               j.created_by_api_key, j.created_by_team_id, j.created_by_organization_id,
               j.created_at, j.started_at, j.completed_at,
               t.team_alias, COALESCE(j.created_by_organization_id, t.organization_id) AS organization_id,
               COALESCE((SELECT SUM(bi.billed_cost) FROM deltallm_batch_item bi WHERE bi.batch_id = j.batch_id), 0) AS total_cost
        FROM deltallm_batch_job j
        LEFT JOIN deltallm_teamtable t ON t.team_id = j.created_by_team_id
        {where_sql}
        ORDER BY j.created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )

    data = []
    for row in rows:
        r = dict(row)
        total_items = int(r.get("total_items") or 0)
        completed = int(r.get("completed_items") or 0)
        failed = int(r.get("failed_items") or 0)
        masked_key = _mask_api_key(r.get("created_by_api_key"))
        scheduler_policy_fields = _scheduler_policy_fields(r, size_aging_config=size_aging_config)
        data.append({
            "batch_id": r.get("batch_id"),
            "endpoint": r.get("endpoint"),
            "status": r.get("status"),
            "model": r.get("model"),
            "total_items": total_items,
            "completed_items": completed,
            "failed_items": failed,
            "cancelled_items": int(r.get("cancelled_items") or 0),
            "in_progress_items": int(r.get("in_progress_items") or 0),
            "scheduler_version": r.get("scheduler_version"),
            "scheduling_model": r.get("scheduling_model"),
            "scheduling_model_group": r.get("scheduling_model_group"),
            "scheduling_endpoint": r.get("scheduling_endpoint"),
            "tenant_scope_type": r.get("tenant_scope_type"),
            "tenant_scope_id": _display_tenant_scope_id(
                scope_type=r.get("tenant_scope_type"),
                scope_id=r.get("tenant_scope_id"),
            ),
            "service_tier": r.get("service_tier"),
            "estimated_work_units": int(r.get("estimated_work_units") or 0),
            "remaining_work_units": int(r.get("remaining_work_units") or 0),
            "size_class": r.get("size_class"),
            "queue_entered_at": to_json_value(r.get("queue_entered_at")),
            "first_claimed_at": to_json_value(r.get("first_claimed_at")),
            "last_claimed_at": to_json_value(r.get("last_claimed_at")),
            "last_scheduled_at": to_json_value(r.get("last_scheduled_at")),
            **scheduler_policy_fields,
            "total_cost": float(r.get("total_cost") or 0),
            "created_by_api_key": masked_key,
            "created_by_team_id": r.get("created_by_team_id"),
            "created_by_organization_id": r.get("created_by_organization_id"),
            "team_alias": r.get("team_alias"),
            "created_at": to_json_value(r.get("created_at")),
            "started_at": to_json_value(r.get("started_at")),
            "completed_at": to_json_value(r.get("completed_at")),
            "capabilities": build_batch_capabilities(scope, r),
        })

    return {
        "data": data,
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/batches/summary")
async def batch_summary(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    db = db_or_503(request)

    clauses: list[str] = []
    params: list[Any] = []

    if not _append_batch_scope_clause(clauses=clauses, params=params, scope=scope):
        return {"total": 0, "queued": 0, "in_progress": 0, "completed": 0, "failed": 0, "cancelled": 0}

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = await db.query_raw(
        f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'queued') AS queued,
            COUNT(*) FILTER (WHERE status IN ('in_progress', 'finalizing')) AS in_progress,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled
        FROM deltallm_batch_job
        {where_sql}
        """,
        *params,
    )
    row = dict(rows[0]) if rows else {}
    return {
        "total": int(row.get("total") or 0),
        "queued": int(row.get("queued") or 0),
        "in_progress": int(row.get("in_progress") or 0),
        "completed": int(row.get("completed") or 0),
        "failed": int(row.get("failed") or 0),
        "cancelled": int(row.get("cancelled") or 0),
    }


@router.get("/ui/api/batches/feature-status", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def batch_feature_status(
    request: Request,
) -> dict[str, bool]:
    general_settings = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
    return {
        "embeddings_batch_enabled": bool(getattr(general_settings, "embeddings_batch_enabled", False)),
    }


@router.get("/ui/api/batches/scheduler/model-capacity")
async def batch_scheduler_model_capacity(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.PLATFORM_ADMIN,
    )
    repository = _batch_repository_or_503(request)
    resolver = _model_capacity_resolver_or_503(request, repository)
    snapshots = await resolver.build_snapshots()
    return {
        "enabled": resolver.config.enabled,
        "fail_open": resolver.config.fail_open,
        "default_model_max_in_flight": resolver.config.default_model_max_in_flight,
        "default_model_max_claim_work_units": resolver.config.default_model_max_claim_work_units,
        "capacity_fraction": resolver.config.capacity_fraction,
        "refresh_seconds": resolver.config.refresh_seconds,
        "data": [_capacity_snapshot_response(snapshot) for snapshot in snapshots],
    }


@router.get("/ui/api/batches/scheduler/status")
async def batch_scheduler_status(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.PLATFORM_ADMIN,
    )
    return _scheduler_status_response(request)


@router.post("/ui/api/batches/scheduler/mode")
async def update_batch_scheduler_mode(
    request: Request,
    payload: SchedulerModeUpdateRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.PLATFORM_ADMIN,
    )
    dynamic_config = _dynamic_config_or_503(request)
    before = _scheduler_status_response(request)
    config_update = {
        "general_settings": {
            "embeddings_batch_scheduler_mode": payload.active_mode,
            "embeddings_batch_scheduler_shadow_mode": payload.shadow_mode,
        }
    }
    try:
        await dynamic_config.update_config(config_update, updated_by="admin_api")
    except DynamicConfigValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Scheduler mode update is invalid",
        ) from exc
    except DynamicConfigPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler mode update could not be persisted",
        ) from exc
    get_app_config = getattr(dynamic_config, "get_app_config", None)
    if callable(get_app_config):
        request.app.state.app_config = get_app_config()
    response = _scheduler_status_response(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_BATCH_SCHEDULER_MODE_UPDATE,
        scope=scope,
        resource_type="batch_scheduler",
        resource_id="scheduler_mode",
        request_payload={
            "active_mode": payload.active_mode,
            "shadow_mode": payload.shadow_mode,
            "reason": payload.reason,
        },
        response_payload={
            "active_mode": response["active_mode"],
            "shadow_mode": response["shadow_mode"],
        },
        before=before,
        after=response,
    )
    logger.warning(
        "batch scheduler mode updated active_mode=%s shadow_mode=%s actor=%s reason=%s",
        response["active_mode"],
        response["shadow_mode"],
        scope.account_id,
        payload.reason or "",
    )
    return response


@router.get("/ui/api/batches/scheduler/flows")
async def batch_scheduler_flows(
    request: Request,
    model_group: str | None = Query(default=None),
    service_tier: str | None = Query(default=None),
    tenant_scope_type: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    limit: int = Query(
        default=SCHEDULER_FLOW_LIST_DEFAULT_LIMIT,
        ge=1,
        le=SCHEDULER_FLOW_LIST_MAX_LIMIT,
    ),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.PLATFORM_ADMIN,
    )
    repository = _batch_repository_or_503(request)
    config = _tenant_fair_share_config_or_503(request)
    flows = await repository.list_scheduler_flows(
        service_tier=service_tier,
        model_group=model_group,
        tenant_scope_type=tenant_scope_type,
        active=active,
        limit=limit,
    )
    return {
        "enabled": config.enabled,
        "limit": limit,
        "base_quantum_work_units": config.base_quantum_work_units,
        "max_deficit_multiplier": config.max_deficit_multiplier,
        "tenant_max_in_flight_work_units": config.tenant_max_in_flight_work_units,
        "tenant_max_queued_work_units": config.tenant_max_queued_work_units,
        "tenant_scope_preference": list(config.tenant_scope_preference),
        "disabled_model_groups": list(config.disabled_model_groups),
        "data": [_scheduler_flow_response(flow) for flow in flows],
    }


@router.post("/ui/api/batches/scheduler/flows/refresh")
async def refresh_batch_scheduler_flows(
    request: Request,
    payload: SchedulerFlowRefreshRequest | None = None,
    limit: int = Query(
        default=SCHEDULER_FLOW_LIST_DEFAULT_LIMIT,
        ge=1,
        le=SCHEDULER_FLOW_LIST_MAX_LIMIT,
    ),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.PLATFORM_ADMIN,
    )
    repository = _batch_repository_or_503(request)
    config = _tenant_fair_share_config_or_503(request)
    general_settings = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
    if general_settings is None:
        size_aging_config = BatchSizeAgingConfig()
        size_aware_scheduling_enabled = False
    else:
        modes = resolve_scheduler_modes_from_settings(general_settings)
        size_aging_config = BatchSizeAgingConfig.from_settings(general_settings)
        size_aware_scheduling_enabled = (
            scheduler_mode_uses_size_aware(modes.active_mode)
            or scheduler_mode_uses_size_aware(modes.shadow_mode)
            or size_aging_config.enabled
        )
    bounded_payload = payload or SchedulerFlowRefreshRequest()
    model_group = (bounded_payload.model_group or "").strip() or None
    service_tier = (bounded_payload.service_tier or "").strip() or None
    with _repair_action_metric("scheduler_flow_refresh"):
        repair_result = await repository.backfill_scheduler_dimensions(
            limit=bounded_payload.repair_limit,
            service_tier=service_tier,
            model_group=model_group,
        )
        refreshed = await repository.refresh_scheduler_flows(
            service_tier=service_tier,
            model_group=model_group,
            base_quantum_work_units=config.base_quantum_work_units,
            max_deficit_multiplier=config.max_deficit_multiplier,
            max_candidate_jobs_per_flow=config.max_candidate_jobs_per_flow,
            size_aware_scheduling_enabled=size_aware_scheduling_enabled,
            aging_seconds_per_work_unit=size_aging_config.aging_seconds_per_work_unit,
            max_age_credit_work_units=size_aging_config.max_age_credit_work_units,
            min_large_job_claim_interval_seconds=(
                size_aging_config.min_large_job_claim_interval_seconds
            ),
            small_job_max_work_units=size_aging_config.small_job_max_work_units,
        )
    flows = await repository.list_scheduler_flows(
        service_tier=service_tier,
        model_group=model_group,
        active=None,
        limit=limit,
    )
    response = {
        "enabled": config.enabled,
        "limit": limit,
        "base_quantum_work_units": config.base_quantum_work_units,
        "max_deficit_multiplier": config.max_deficit_multiplier,
        "tenant_max_in_flight_work_units": config.tenant_max_in_flight_work_units,
        "tenant_max_queued_work_units": config.tenant_max_queued_work_units,
        "tenant_scope_preference": list(config.tenant_scope_preference),
        "disabled_model_groups": list(config.disabled_model_groups),
        "repaired_jobs": int(repair_result.get("jobs") or 0),
        "repaired_items": int(repair_result.get("items") or 0),
        "repair_skipped": int(repair_result.get("skipped") or 0),
        "refreshed_flows": len(refreshed),
        "data": [_scheduler_flow_response(flow) for flow in flows],
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_BATCH_SCHEDULER_FLOW_REFRESH,
        scope=scope,
        resource_type="batch_scheduler",
        resource_id="scheduler_flows",
        request_payload={
            "model_group": model_group,
            "service_tier": service_tier,
            "repair_limit": bounded_payload.repair_limit,
            "flow_limit": limit,
        },
        response_payload={
            "repaired_jobs": response["repaired_jobs"],
            "repaired_items": response["repaired_items"],
            "repair_skipped": response["repair_skipped"],
            "refreshed_flows": response["refreshed_flows"],
        },
    )
    logger.info(
        "batch repair scheduler-flow-refresh model_group=%s service_tier=%s jobs=%s items=%s flows=%s actor=%s",
        model_group or "*",
        service_tier or "*",
        response["repaired_jobs"],
        response["repaired_items"],
        response["refreshed_flows"],
        scope.account_id,
    )
    return response


@router.post("/ui/api/batches/scheduler-dimensions/backfill")
async def backfill_scheduler_dimensions(
    request: Request,
    payload: SchedulerBackfillRequest | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, int]:
    request_start = perf_counter()
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.PLATFORM_ADMIN,
    )
    repository = _batch_repository_or_503(request)
    bounded_payload = payload or SchedulerBackfillRequest()
    with _repair_action_metric("scheduler_backfill"):
        result = await repository.backfill_scheduler_dimensions(limit=bounded_payload.limit)
    await _refresh_batch_runtime_metrics(repository)
    response = {
        "jobs": int(result.get("jobs") or 0),
        "items": int(result.get("items") or 0),
    }
    if result.get("skipped"):
        response["skipped"] = int(result.get("skipped") or 0)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_BATCH_SCHEDULER_BACKFILL,
        scope=scope,
        resource_type="batch_scheduler",
        resource_id="scheduler_dimensions",
        request_payload={"limit": bounded_payload.limit},
        response_payload=response,
    )
    logger.info(
        "batch repair scheduler-backfill jobs=%s items=%s actor=%s",
        response["jobs"],
        response["items"],
        scope.account_id,
    )
    return response


@router.get("/ui/api/batches/{batch_id}")
async def get_batch(
    request: Request,
    batch_id: str,
    items_limit: int = Query(default=50, ge=1, le=500),
    items_offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    db = db_or_503(request)

    rows = await db.query_raw(
        """
        SELECT j.*, t.team_alias, t.organization_id
        , COALESCE(j.created_by_organization_id, t.organization_id) AS organization_id
        FROM deltallm_batch_job j
        LEFT JOIN deltallm_teamtable t ON t.team_id = j.created_by_team_id
        WHERE j.batch_id = $1
        LIMIT 1
        """,
        batch_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    job = dict(rows[0])
    await _enforce_batch_update_scope(db=db, scope=scope, job=job)
    size_aging_config = _batch_size_aging_config_or_default(request)

    cost_rows = await db.query_raw(
        """
        SELECT COALESCE(SUM(provider_cost), 0) AS total_provider_cost,
               COALESCE(SUM(billed_cost), 0) AS total_billed_cost
        FROM deltallm_batch_item
        WHERE batch_id = $1
        """,
        batch_id,
    )
    cost_row = dict(cost_rows[0]) if cost_rows else {}

    items_count_rows = await db.query_raw(
        "SELECT COUNT(*) AS total FROM deltallm_batch_item WHERE batch_id = $1",
        batch_id,
    )
    items_total = int((items_count_rows[0] if items_count_rows else {}).get("total") or 0)

    item_rows = await db.query_raw(
        """
        SELECT item_id, line_number, custom_id, status, attempts, provider_cost, billed_cost,
               last_error, request_body, response_body, error_body, usage,
               scheduling_model, scheduling_model_group, estimated_work_units, not_before_at,
               last_scheduled_at,
               created_at, started_at, completed_at
        FROM deltallm_batch_item
        WHERE batch_id = $1
        ORDER BY line_number ASC
        LIMIT $2 OFFSET $3
        """,
        batch_id,
        items_limit,
        items_offset,
    )

    masked_key = _mask_api_key(job.get("created_by_api_key"))
    scheduler_policy_fields = _scheduler_policy_fields(job, size_aging_config=size_aging_config)

    return {
        "batch_id": job.get("batch_id"),
        "endpoint": job.get("endpoint"),
        "status": job.get("status"),
        "model": job.get("model"),
        "execution_mode": job.get("execution_mode"),
        "metadata": to_json_value(job.get("metadata")),
        "provider_error": decode_operator_failed_reason(job.get("provider_error")),
        "total_items": int(job.get("total_items") or 0),
        "completed_items": int(job.get("completed_items") or 0),
        "failed_items": int(job.get("failed_items") or 0),
        "cancelled_items": int(job.get("cancelled_items") or 0),
        "in_progress_items": int(job.get("in_progress_items") or 0),
        "scheduler_version": job.get("scheduler_version"),
        "scheduling_model": job.get("scheduling_model"),
        "scheduling_model_group": job.get("scheduling_model_group"),
        "scheduling_endpoint": job.get("scheduling_endpoint"),
        "tenant_scope_type": job.get("tenant_scope_type"),
        "tenant_scope_id": _display_tenant_scope_id(
            scope_type=job.get("tenant_scope_type"),
            scope_id=job.get("tenant_scope_id"),
        ),
        "service_tier": job.get("service_tier"),
        "estimated_work_units": int(job.get("estimated_work_units") or 0),
        "remaining_work_units": int(job.get("remaining_work_units") or 0),
        "size_class": job.get("size_class"),
        "queue_entered_at": to_json_value(job.get("queue_entered_at")),
        "first_claimed_at": to_json_value(job.get("first_claimed_at")),
        "last_claimed_at": to_json_value(job.get("last_claimed_at")),
        "last_scheduled_at": to_json_value(job.get("last_scheduled_at")),
        **scheduler_policy_fields,
        "scheduler_debug": to_json_value(job.get("scheduler_debug")),
        "total_provider_cost": float(cost_row.get("total_provider_cost") or 0),
        "total_billed_cost": float(cost_row.get("total_billed_cost") or 0),
        "created_by_api_key": masked_key,
        "created_by_team_id": job.get("created_by_team_id"),
        "created_by_organization_id": job.get("created_by_organization_id") or job.get("organization_id"),
        "team_alias": job.get("team_alias"),
        "created_at": to_json_value(job.get("created_at")),
        "started_at": to_json_value(job.get("started_at")),
        "completed_at": to_json_value(job.get("completed_at")),
        "cancel_requested_at": to_json_value(job.get("cancel_requested_at")),
        "expires_at": to_json_value(job.get("expires_at")),
        "capabilities": build_batch_capabilities(scope, job),
        "items": {
            "data": [to_json_value(dict(r)) for r in item_rows],
            "pagination": {
                "total": items_total,
                "limit": items_limit,
                "offset": items_offset,
                "has_more": items_offset + items_limit < items_total,
            },
        },
    }


@router.post("/ui/api/batches/{batch_id}/cancel")
async def cancel_batch(
    request: Request,
    batch_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    job = await _load_batch_scope_row(db, batch_id)
    await _enforce_batch_update_scope(db=db, scope=scope, job=job)

    terminal = {"completed", "failed", "cancelled", "expired"}
    if job.get("status") in terminal:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot cancel batch in '{job.get('status')}' status")

    updated = await db.query_raw(
        """
        UPDATE deltallm_batch_job
        SET cancel_requested_at = NOW(),
            status_last_updated_at = NOW()
        WHERE batch_id = $1
        RETURNING batch_id, status
        """,
        batch_id,
    )
    return {"batch_id": batch_id, "status": dict(updated[0]).get("status") if updated else job.get("status"), "cancel_requested": True}


@router.post("/ui/api/batches/{batch_id}/retry-finalization")
async def retry_batch_finalization(
    request: Request,
    batch_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    repository = _batch_repository_or_503(request)
    job = await _load_batch_scope_row(db, batch_id)
    await _enforce_batch_update_scope(db=db, scope=scope, job=job)
    if job.get("status") != "finalizing":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Batch is not in 'finalizing' status")
    with _repair_action_metric("retry_finalization"):
        updated = await repository.retry_finalization_now(batch_id)
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    await _refresh_batch_runtime_metrics(repository)
    response = {"batch_id": batch_id, "status": updated.status, "retried": True}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_BATCH_RETRY_FINALIZATION,
        scope=scope,
        resource_type="batch",
        resource_id=batch_id,
        request_payload={"batch_id": batch_id},
        response_payload=response,
    )
    logger.info("batch repair retry-finalization batch_id=%s actor=%s", batch_id, scope.account_id)
    return response


@router.post("/ui/api/batches/{batch_id}/requeue-stale")
async def requeue_stale_batch_items(
    request: Request,
    batch_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    repository = _batch_repository_or_503(request)
    job = await _load_batch_scope_row(db, batch_id)
    await _enforce_batch_update_scope(db=db, scope=scope, job=job)
    with _repair_action_metric("requeue_stale"):
        requeued = await repository.requeue_expired_in_progress_items(batch_id)
        refreshed = await repository.refresh_job_progress(batch_id)
    await _refresh_batch_runtime_metrics(repository)
    response = {
        "batch_id": batch_id,
        "status": refreshed.status if refreshed is not None else job.get("status"),
        "requeued_items": requeued,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_BATCH_REQUEUE_STALE,
        scope=scope,
        resource_type="batch",
        resource_id=batch_id,
        request_payload={"batch_id": batch_id},
        response_payload=response,
    )
    logger.info("batch repair requeue-stale batch_id=%s items=%s actor=%s", batch_id, requeued, scope.account_id)
    return response


@router.post("/ui/api/batches/{batch_id}/mark-failed")
async def mark_batch_failed(
    request: Request,
    batch_id: str,
    payload: MarkBatchFailedRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    repository = _batch_repository_or_503(request)
    job = await _load_batch_scope_row(db, batch_id)
    await _enforce_batch_update_scope(db=db, scope=scope, job=job)
    terminal = {"completed", "failed", "cancelled", "expired"}
    if job.get("status") in terminal:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot fail batch in '{job.get('status')}' status")
    reason = str(payload.reason or "").strip() or "Marked failed by operator"
    provider_error = encode_operator_failed_reason(reason)
    with _repair_action_metric("mark_failed"):
        failed_items = await repository.fail_nonterminal_items(batch_id=batch_id, reason=reason)
        await repository.set_provider_error(batch_id=batch_id, provider_error=provider_error)
        refreshed = await repository.refresh_job_progress(batch_id)
        if refreshed is not None and refreshed.status == "finalizing":
            refreshed = await repository.retry_finalization_now(batch_id) or refreshed
    await _refresh_batch_runtime_metrics(repository)
    current_status = refreshed.status if refreshed is not None else "failed"
    response = {
        "batch_id": batch_id,
        "current_status": current_status,
        "intended_status": "failed",
        "failed_items": failed_items,
        "reason": reason,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_BATCH_MARK_FAILED,
        scope=scope,
        resource_type="batch",
        resource_id=batch_id,
        request_payload={"batch_id": batch_id, "reason": reason},
        response_payload=response,
    )
    logger.warning("batch repair mark-failed batch_id=%s items=%s actor=%s reason=%s", batch_id, failed_items, scope.account_id, reason)
    return response
