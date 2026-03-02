from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.auth.roles import Permission
from src.api.admin.endpoints.common import db_or_503, to_json_value, get_auth_scope

router = APIRouter(tags=["Admin Batches"])


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

    if not scope.is_platform_admin:
        if scope.team_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.team_ids)))
            params.extend(scope.team_ids)
            clauses.append(f"j.created_by_team_id IN ({ph})")
        elif scope.org_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            clauses.append(f"j.created_by_team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN ({ph}))")
        else:
            return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if search:
        params.append(f"%{search}%")
        clauses.append(f"(j.batch_id ILIKE ${len(params)} OR j.model ILIKE ${len(params)})")

    valid_statuses = {"validating", "queued", "in_progress", "finalizing", "completed", "failed", "cancelled", "expired"}
    if status_filter and status_filter in valid_statuses:
        params.append(status_filter)
        clauses.append(f"j.status = ${len(params)}")

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
               j.created_by_api_key, j.created_by_team_id,
               j.created_at, j.started_at, j.completed_at,
               t.team_alias,
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
            "team_alias": r.get("team_alias"),
            "created_at": to_json_value(r.get("created_at")),
            "started_at": to_json_value(r.get("started_at")),
            "completed_at": to_json_value(r.get("completed_at")),
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

    if not scope.is_platform_admin:
        if scope.team_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.team_ids)))
            params.extend(scope.team_ids)
            clauses.append(f"created_by_team_id IN ({ph})")
        elif scope.org_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            clauses.append(f"created_by_team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN ({ph}))")
        else:
            return {"total": 0, "queued": 0, "in_progress": 0, "completed": 0, "failed": 0, "cancelled": 0}

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = await db.query_raw(
        f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'queued') AS queued,
            COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress,
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
        SELECT j.*, t.team_alias
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

    if not scope.is_platform_admin:
        team_id = job.get("created_by_team_id")
        if team_id and scope.team_ids and team_id in scope.team_ids:
            pass
        elif team_id and scope.org_ids:
            org_rows = await db.query_raw(
                "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
                team_id,
            )
            org_id = str((org_rows[0] if org_rows else {}).get("organization_id") or "")
            if org_id not in scope.org_ids:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

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
        "total_items": int(job.get("total_items") or 0),
        "completed_items": int(job.get("completed_items") or 0),
        "failed_items": int(job.get("failed_items") or 0),
        "cancelled_items": int(job.get("cancelled_items") or 0),
        "in_progress_items": int(job.get("in_progress_items") or 0),
        "total_provider_cost": float(cost_row.get("total_provider_cost") or 0),
        "total_billed_cost": float(cost_row.get("total_billed_cost") or 0),
        "created_by_api_key": masked_key,
        "created_by_team_id": job.get("created_by_team_id"),
        "team_alias": job.get("team_alias"),
        "created_at": to_json_value(job.get("created_at")),
        "started_at": to_json_value(job.get("started_at")),
        "completed_at": to_json_value(job.get("completed_at")),
        "cancel_requested_at": to_json_value(job.get("cancel_requested_at")),
        "expires_at": to_json_value(job.get("expires_at")),
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

    rows = await db.query_raw(
        "SELECT batch_id, status, created_by_team_id FROM deltallm_batch_job WHERE batch_id = $1 LIMIT 1",
        batch_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    job = dict(rows[0])

    if not scope.is_platform_admin:
        team_id = job.get("created_by_team_id")
        if team_id and scope.team_ids and team_id in scope.team_ids:
            pass
        elif team_id and scope.org_ids:
            org_rows = await db.query_raw(
                "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
                team_id,
            )
            org_id = str((org_rows[0] if org_rows else {}).get("organization_id") or "")
            if org_id not in scope.org_ids:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

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
