from __future__ import annotations

import contextlib
from datetime import UTC, datetime
import logging
from typing import Any, Iterator
from time import perf_counter

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel

from src.auth.roles import Permission
from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit, to_json_value, get_auth_scope
from src.audit.actions import AuditAction
from src.batch.repository import BatchRepository
from src.batch.models import BATCH_JOB_STATUS_SET, decode_operator_failed_reason, encode_operator_failed_reason
from src.metrics import (
    increment_batch_repair_action,
    publish_batch_runtime_summary,
)
from src.middleware.admin import require_admin_permission
from src.services.ui_authorization import build_batch_capabilities

router = APIRouter(tags=["Admin Batches"])
logger = logging.getLogger(__name__)


class MarkBatchFailedRequest(BaseModel):
    reason: str | None = None


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

    params.append(limit)
    params.append(offset)
    rows = await db.query_raw(
        f"""
        SELECT j.batch_id, j.endpoint, j.status, j.model, j.execution_mode,
               j.total_items, j.completed_items, j.failed_items, j.cancelled_items, j.in_progress_items,
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
        api_key = r.get("created_by_api_key") or ""
        masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else api_key
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

    api_key = job.get("created_by_api_key") or ""
    masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else api_key

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
