from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime, time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from src.auth.roles import Permission
from src.api.admin.endpoints.common import db_or_503, get_auth_scope, to_json_value
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Admin Audit"])


def _date_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


def _build_audit_where(
    *,
    action: str | None,
    status_value: str | None,
    actor_id: str | None,
    organization_id: str | None,
    request_id: str | None,
    correlation_id: str | None,
    start_date: date | None,
    end_date: date | None,
    scope: Any,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    def add_clause(template: str, value: Any) -> None:
        params.append(value)
        clauses.append(template.format(i=len(params)))

    if action:
        add_clause("action = ${i}", action)
    if status_value:
        add_clause("status = ${i}", status_value)
    if actor_id:
        add_clause("actor_id = ${i}", actor_id)
    if organization_id:
        add_clause("organization_id = ${i}", organization_id)
    if request_id:
        add_clause("request_id = ${i}", request_id)
    if correlation_id:
        add_clause("correlation_id = ${i}", correlation_id)

    start_dt = _date_start(start_date)
    end_dt = _date_end(end_date)
    if start_dt is not None:
        add_clause("occurred_at >= ${i}::timestamp", start_dt)
    if end_dt is not None:
        add_clause("occurred_at <= ${i}::timestamp", end_dt)

    if not getattr(scope, "is_platform_admin", False):
        org_ids = list(getattr(scope, "org_ids", []) or [])
        if not org_ids:
            clauses.append("1 = 0")
        else:
            placeholders = ", ".join(f"${len(params) + index + 1}" for index in range(len(org_ids)))
            params.extend(org_ids)
            clauses.append(f"organization_id IN ({placeholders})")

    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


@router.get("/ui/api/audit/events", dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))])
async def list_audit_events(
    request: Request,
    action: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    actor_id: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.AUDIT_READ)
    db = db_or_503(request)
    where_sql, params = _build_audit_where(
        action=action,
        status_value=status_value,
        actor_id=actor_id,
        organization_id=organization_id,
        request_id=request_id,
        correlation_id=correlation_id,
        start_date=start_date,
        end_date=end_date,
        scope=scope,
    )

    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    rows = await db.query_raw(
        f"""
        SELECT event_id, occurred_at, organization_id, actor_type, actor_id, api_key, action, resource_type, resource_id,
               request_id, correlation_id, ip, user_agent, status, latency_ms, input_tokens, output_tokens,
               error_type, error_code, metadata, content_stored
        FROM deltallm_auditevent
        {where_sql}
        ORDER BY occurred_at DESC
        LIMIT ${limit_idx}
        OFFSET ${offset_idx}
        """,
        *params,
        limit,
        offset,
    )
    count_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM deltallm_auditevent {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    return {
        "events": [to_json_value(dict(row)) for row in rows],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.get("/ui/api/audit/events/{event_id}", dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))])
async def get_audit_event(
    request: Request,
    event_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.AUDIT_READ)
    db = db_or_503(request)
    where_sql, params = _build_audit_where(
        action=None,
        status_value=None,
        actor_id=None,
        organization_id=None,
        request_id=None,
        correlation_id=None,
        start_date=None,
        end_date=None,
        scope=scope,
    )
    params.append(event_id)
    event_clause = f"event_id::text = ${len(params)}"
    if where_sql:
        where_sql = f"{where_sql} AND {event_clause}"
    else:
        where_sql = f" WHERE {event_clause}"

    rows = await db.query_raw(
        f"""
        SELECT event_id, occurred_at, organization_id, actor_type, actor_id, api_key, action, resource_type, resource_id,
               request_id, correlation_id, ip, user_agent, status, latency_ms, input_tokens, output_tokens,
               error_type, error_code, metadata, content_stored, prev_hash, event_hash
        FROM deltallm_auditevent
        {where_sql}
        LIMIT 1
        """,
        *params,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit event not found")
    event = to_json_value(dict(rows[0]))

    payload_rows = await db.query_raw(
        """
        SELECT payload_id, event_id, kind, storage_mode, content_json, storage_uri, content_sha256, size_bytes, redacted, created_at
        FROM deltallm_auditpayload
        WHERE event_id::text = $1
        ORDER BY created_at ASC
        """,
        event_id,
    )
    event["payloads"] = [to_json_value(dict(row)) for row in payload_rows]
    return event


@router.get("/ui/api/audit/timeline", dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))])
async def audit_timeline(
    request: Request,
    request_id: str = Query(default=""),
    correlation_id: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    if not request_id and not correlation_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="request_id or correlation_id is required")

    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.AUDIT_READ)
    db = db_or_503(request)
    where_sql, params = _build_audit_where(
        action=None,
        status_value=None,
        actor_id=None,
        organization_id=None,
        request_id=request_id or None,
        correlation_id=correlation_id,
        start_date=None,
        end_date=None,
        scope=scope,
    )
    rows = await db.query_raw(
        f"""
        SELECT event_id, occurred_at, organization_id, actor_type, actor_id, action, resource_type, resource_id,
               request_id, correlation_id, status, error_type, error_code, metadata
        FROM deltallm_auditevent
        {where_sql}
        ORDER BY occurred_at ASC
        """,
        *params,
    )
    return {"events": [to_json_value(dict(row)) for row in rows]}


@router.get("/ui/api/audit/export", dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))])
async def export_audit_events(
    request: Request,
    format: str = Query(default="jsonl", pattern="^(jsonl|csv)$"),
    action: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    actor_id: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> Response:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.AUDIT_READ)
    db = db_or_503(request)
    where_sql, params = _build_audit_where(
        action=action,
        status_value=status_value,
        actor_id=actor_id,
        organization_id=organization_id,
        request_id=request_id,
        correlation_id=correlation_id,
        start_date=start_date,
        end_date=end_date,
        scope=scope,
    )

    limit_idx = len(params) + 1
    rows = await db.query_raw(
        f"""
        SELECT event_id, occurred_at, organization_id, actor_type, actor_id, api_key, action, resource_type, resource_id,
               request_id, correlation_id, status, latency_ms, input_tokens, output_tokens,
               error_type, error_code, metadata, content_stored
        FROM deltallm_auditevent
        {where_sql}
        ORDER BY occurred_at DESC
        LIMIT ${limit_idx}
        """,
        *params,
        limit,
    )
    payload = [to_json_value(dict(row)) for row in rows]

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "event_id",
                "occurred_at",
                "organization_id",
                "actor_type",
                "actor_id",
                "api_key",
                "action",
                "resource_type",
                "resource_id",
                "request_id",
                "correlation_id",
                "status",
                "latency_ms",
                "input_tokens",
                "output_tokens",
                "error_type",
                "error_code",
                "content_stored",
                "metadata",
            ],
        )
        writer.writeheader()
        for item in payload:
            row = dict(item)
            row["metadata"] = json.dumps(row.get("metadata") or {}, default=str)
            writer.writerow(row)
        content = buffer.getvalue()
        return Response(content=content, media_type="text/csv")

    jsonl_content = "\n".join(json.dumps(item, default=str) for item in payload)
    return Response(content=jsonl_content, media_type="application/x-ndjson")
