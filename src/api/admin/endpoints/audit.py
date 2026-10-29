from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from src.auth.roles import Permission
from src.api.admin.endpoints.audit_labels import event_context, label_events
from src.api.admin.endpoints.common import db_or_503, get_auth_scope, to_json_value
from src.audit.categories import (
    AUDIT_CATEGORIES,
    audit_action_category,
    audit_category_clause,
    known_audit_actions,
)
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


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """A cursor is the time and ID of the last row already shown."""
    if not cursor:
        return None
    try:
        moment, _, event_id = cursor.partition("|")
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor"
        ) from None
    if not event_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor")
    return _naive_utc(parsed) or parsed, event_id


def _encode_cursor(row: dict[str, Any]) -> str:
    occurred_at = row.get("occurred_at")
    moment = occurred_at.isoformat() if isinstance(occurred_at, datetime) else str(occurred_at)
    return f"{moment}|{row.get('event_id')}"


async def _actor_ids(db: Any, actor: str) -> tuple[list[str], list[str]]:
    """Resolve the Actor filter: an email, an API key name, a key hash, or a raw actor ID."""
    value = actor.strip()
    account_rows = await db.query_raw(
        "SELECT account_id FROM deltallm_platformaccount WHERE lower(email) = lower($1) LIMIT 1",
        value,
    )
    key_rows = await db.query_raw(
        "SELECT token FROM deltallm_verificationtoken WHERE key_name = $1 LIMIT 20",
        value,
    )
    actor_ids = [value] + [str(row["account_id"]) for row in account_rows]
    # A key hash also matches events whose actor_id is the key owner's user ID.
    key_hashes = [value] + [str(row["token"]) for row in key_rows]
    return actor_ids, key_hashes


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
    category: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    actor_ids: list[str] | None = None,
    actor_key_hashes: list[str] | None = None,
    actor_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip: str | None = None,
    search: str | None = None,
    cursor: tuple[datetime, str] | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    def add_clause(template: str, value: Any) -> None:
        params.append(value)
        clauses.append(template.format(i=len(params)))

    if action:
        add_clause("action = ${i}", action)
    if category:
        clauses.append(audit_category_clause(category))
    if status_value:
        add_clause("status = ${i}", status_value)
    if actor_id:
        add_clause("actor_id = ${i}", actor_id)
    if actor_ids:
        params.append(actor_ids)
        actor_clause = f"actor_id = ANY(${len(params)}::text[])"
        if actor_key_hashes:
            params.append(actor_key_hashes)
            actor_clause = f"({actor_clause} OR api_key = ANY(${len(params)}::text[]))"
        clauses.append(actor_clause)
    if actor_type:
        add_clause("actor_type = ${i}", actor_type)
    if organization_id:
        add_clause("organization_id = ${i}", organization_id)
    if request_id:
        add_clause("request_id = ${i}", request_id)
    if correlation_id:
        add_clause("correlation_id = ${i}", correlation_id)
    if resource_type:
        add_clause("resource_type = ${i}", resource_type)
    if resource_id:
        add_clause("resource_id = ${i}", resource_id)
    if ip:
        add_clause("ip = ${i}", ip)
    if search:
        params.append(search)
        index = len(params)
        clauses.append(
            f"(event_id::text = ${index} OR request_id = ${index} OR resource_id = ${index})"
        )

    start_dt = _date_start(start_date)
    end_dt = _date_end(end_date)
    if start_dt is not None:
        add_clause("occurred_at >= ${i}::timestamp", start_dt)
    if end_dt is not None:
        add_clause("occurred_at <= ${i}::timestamp", end_dt)
    if since is not None:
        add_clause("occurred_at >= ${i}::timestamp", _naive_utc(since))
    if until is not None:
        add_clause("occurred_at < ${i}::timestamp", _naive_utc(until))
    if cursor is not None:
        params.append(cursor[0])
        time_index = len(params)
        params.append(cursor[1])
        id_index = len(params)
        clauses.append(
            f"(occurred_at < ${time_index}::timestamp OR "
            f"(occurred_at = ${time_index}::timestamp AND event_id::text < ${id_index}))"
        )

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


_CATEGORY_PATTERN = "^(" + "|".join(AUDIT_CATEGORIES) + ")$"


@router.get(
    "/ui/api/audit/actions", dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))]
)
async def list_audit_actions() -> dict[str, Any]:
    """Every known action with its category. A fixed list: no database read."""
    return {"actions": known_audit_actions(), "categories": list(AUDIT_CATEGORIES)}


@router.get(
    "/ui/api/audit/organizations",
    dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))],
)
async def list_audit_organizations(
    request: Request,
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=8, ge=1, le=50),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Organizations for the Organization filter, limited to the ones whose audit log the caller can read."""
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.AUDIT_READ
    )
    db = db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if not getattr(scope, "is_platform_admin", False):
        org_ids = list(getattr(scope, "org_ids", []) or [])
        if not org_ids:
            return {"organizations": []}
        params.append(org_ids)
        clauses.append(f"organization_id = ANY(${len(params)}::text[])")
    if search and search.strip():
        params.append(f"%{search.strip()}%")
        clauses.append(
            f"(organization_name ILIKE ${len(params)} OR organization_id ILIKE ${len(params)})"
        )
    params.append(limit)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await db.query_raw(
        f"""
        SELECT organization_id, organization_name
        FROM deltallm_organizationtable
        {where_sql}
        ORDER BY organization_name NULLS LAST, organization_id
        LIMIT ${len(params)}
        """,
        *params,
    )
    return {"organizations": [to_json_value(dict(row)) for row in rows]}


@router.get(
    "/ui/api/audit/events", dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))]
)
async def list_audit_events(
    request: Request,
    action: str | None = Query(default=None),
    category: str | None = Query(default=None, pattern=_CATEGORY_PATTERN),
    status_value: str | None = Query(default=None, alias="status"),
    actor_id: str | None = Query(default=None),
    actor: str | None = Query(default=None, max_length=320),
    actor_type: str | None = Query(default=None, max_length=64),
    organization_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    resource_type: str | None = Query(default=None, max_length=64),
    resource_id: str | None = Query(default=None, max_length=512),
    ip: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=512),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=256),
    count: bool = Query(default=True),
    enrich: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.AUDIT_READ
    )
    db = db_or_503(request)
    actor_ids: list[str] | None = None
    actor_key_hashes: list[str] | None = None
    if actor and actor.strip():
        actor_ids, actor_key_hashes = await _actor_ids(db, actor)
    decoded_cursor = _decode_cursor(cursor)
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
        category=category,
        since=since,
        until=until,
        actor_ids=actor_ids,
        actor_key_hashes=actor_key_hashes,
        actor_type=actor_type,
        resource_type=resource_type,
        resource_id=resource_id,
        ip=ip,
        search=search.strip() if search and search.strip() else None,
        cursor=decoded_cursor,
    )

    # Cursor paging reads one row past the page to learn whether more exist,
    # so it needs no count. Offset paging keeps the exact total for older callers.
    keyset = decoded_cursor is not None or not count
    page_size = limit + 1 if keyset else limit
    limit_idx = len(params) + 1
    offset_sql = ""
    query_params: list[Any] = [*params, page_size]
    if not keyset:
        offset_sql = f"OFFSET ${len(params) + 2}"
        query_params.append(offset)
    rows = await db.query_raw(
        f"""
        SELECT event_id, occurred_at, organization_id, actor_type, actor_id, api_key, action, resource_type, resource_id,
               request_id, correlation_id, ip, user_agent, status, latency_ms, input_tokens, output_tokens,
               error_type, error_code, metadata, content_stored
        FROM deltallm_auditevent
        {where_sql}
        ORDER BY occurred_at DESC{", event_id::text DESC" if keyset else ""}
        LIMIT ${limit_idx}
        {offset_sql}
        """,
        *query_params,
    )
    rows = [dict(row) for row in rows]
    events_rows = rows[:limit]
    events = [to_json_value(row) for row in events_rows]
    if enrich and events:
        await label_events(db, events, scope)

    if keyset:
        has_more = len(rows) > limit
        pagination: dict[str, Any] = {
            "limit": limit,
            "has_more": has_more,
            "next_cursor": _encode_cursor(events_rows[-1]) if has_more and events_rows else None,
        }
    else:
        count_rows = await db.query_raw(
            f"SELECT COUNT(*) AS total FROM deltallm_auditevent {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)
        pagination = {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }

    return {"events": events, "pagination": pagination}


_EVENT_ID_IS_UUID: bool | None = None


async def _event_id_is_uuid(db: Any) -> bool:
    """Older installs store event IDs as uuid, newer ones as text. Match the column
    type so lookups by ID use the primary key instead of casting every row."""
    global _EVENT_ID_IS_UUID
    if _EVENT_ID_IS_UUID is None:
        rows = await db.query_raw(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'deltallm_auditevent' AND column_name = 'event_id' LIMIT 1"
        )
        if not rows:
            return False
        _EVENT_ID_IS_UUID = str(dict(rows[0]).get("data_type") or "") == "uuid"
    return _EVENT_ID_IS_UUID


async def _event_id_clause(db: Any, event_id: str, index: int) -> str:
    if await _event_id_is_uuid(db):
        try:
            UUID(event_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Audit event not found"
            ) from None
        return f"event_id = ${index}::uuid"
    return f"event_id = ${index}"


async def _load_event(db: Any, event_id: str, scope: Any) -> dict[str, Any]:
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
    event_clause = await _event_id_clause(db, event_id, len(params))
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
    return to_json_value(dict(rows[0]))


async def _payload_rows(db: Any, event_id: str, *, content: bool) -> list[dict[str, Any]]:
    columns = (
        "payload_id, event_id, kind, storage_mode, content_json, storage_uri, content_sha256, size_bytes, redacted, created_at"
        if content
        else "payload_id, kind, storage_mode, size_bytes, redacted"
    )
    clause = await _event_id_clause(db, event_id, 1)
    rows = await db.query_raw(
        f"""
        SELECT {columns}
        FROM deltallm_auditpayload
        WHERE {clause}
        ORDER BY created_at ASC
        """,
        event_id,
    )
    return [to_json_value(dict(row)) for row in rows]


@router.get(
    "/ui/api/audit/events/{event_id}",
    dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))],
)
async def get_audit_event(
    request: Request,
    event_id: str,
    include_payloads: bool = Query(default=True),
    context: bool = Query(default=False),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.AUDIT_READ
    )
    db = db_or_503(request)
    event = await _load_event(db, event_id, scope)
    if include_payloads:
        event["payloads"] = await _payload_rows(db, event_id, content=True)
    else:
        # The panel shows that bodies exist and loads them only on request.
        summary = await _payload_rows(db, event_id, content=False)
        event["payload_summary"] = {
            "count": len(summary),
            "redacted": any(bool(row.get("redacted")) for row in summary),
            "kinds": [row.get("kind") for row in summary],
        }
    if context:
        event["context"] = await event_context(db, event, scope)
    return event


@router.get(
    "/ui/api/audit/events/{event_id}/payloads",
    dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))],
)
async def get_audit_event_payloads(
    request: Request,
    event_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.AUDIT_READ
    )
    db = db_or_503(request)
    await _load_event(db, event_id, scope)
    return {"payloads": await _payload_rows(db, event_id, content=True)}


_RELATED_COLUMNS = "event_id, occurred_at, action, status, actor_type, actor_id, resource_type, resource_id, error_code"


@router.get(
    "/ui/api/audit/events/{event_id}/related",
    dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))],
)
async def get_related_audit_events(
    request: Request,
    event_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Events around one event: the same request, the same resource, and recent
    sign-ins by the same person. Small indexed lookups, loaded when the panel opens."""
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.AUDIT_READ
    )
    db = db_or_503(request)
    event = await _load_event(db, event_id, scope)

    async def related(order: str, limit: int, **filters: Any) -> list[dict[str, Any]]:
        where_sql, params = _build_audit_where(
            action=None,
            status_value=None,
            actor_id=filters.get("actor_id"),
            organization_id=None,
            request_id=filters.get("request_id"),
            correlation_id=None,
            start_date=None,
            end_date=None,
            scope=scope,
            category=filters.get("category"),
            resource_type=filters.get("resource_type"),
            resource_id=filters.get("resource_id"),
        )
        params.append(limit)
        rows = await db.query_raw(
            f"SELECT {_RELATED_COLUMNS} FROM deltallm_auditevent {where_sql} ORDER BY occurred_at {order} LIMIT ${len(params)}",
            *params,
        )
        return [to_json_value(dict(row)) for row in rows]

    request_events: list[dict[str, Any]] = []
    if event.get("request_id"):
        request_events = await related("ASC", 20, request_id=event["request_id"])
    resource_events: list[dict[str, Any]] = []
    if event.get("resource_type") and event.get("resource_id"):
        resource_events = await related(
            "DESC", 6, resource_type=event["resource_type"], resource_id=event["resource_id"]
        )
    actor_signins: list[dict[str, Any]] = []
    if audit_action_category(str(event.get("action") or "")) == "auth" and event.get("actor_id"):
        actor_signins = await related("DESC", 5, actor_id=event["actor_id"], category="auth")
    return {"request": request_events, "resource": resource_events, "actor_signins": actor_signins}


@router.get(
    "/ui/api/audit/timeline",
    dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))],
)
async def audit_timeline(
    request: Request,
    request_id: str = Query(default=""),
    correlation_id: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    if not request_id and not correlation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request_id or correlation_id is required",
        )

    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.AUDIT_READ
    )
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


@router.get(
    "/ui/api/audit/export", dependencies=[Depends(require_admin_permission(Permission.AUDIT_READ))]
)
async def export_audit_events(
    request: Request,
    format: str = Query(default="jsonl", pattern="^(jsonl|csv)$"),
    action: str | None = Query(default=None),
    category: str | None = Query(default=None, pattern=_CATEGORY_PATTERN),
    status_value: str | None = Query(default=None, alias="status"),
    actor_id: str | None = Query(default=None),
    actor: str | None = Query(default=None, max_length=320),
    actor_type: str | None = Query(default=None, max_length=64),
    organization_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    resource_type: str | None = Query(default=None, max_length=64),
    resource_id: str | None = Query(default=None, max_length=512),
    ip: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=512),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> Response:
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.AUDIT_READ
    )
    db = db_or_503(request)
    actor_ids: list[str] | None = None
    actor_key_hashes: list[str] | None = None
    if actor and actor.strip():
        actor_ids, actor_key_hashes = await _actor_ids(db, actor)
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
        category=category,
        since=since,
        until=until,
        actor_ids=actor_ids,
        actor_key_hashes=actor_key_hashes,
        actor_type=actor_type,
        resource_type=resource_type,
        resource_id=resource_id,
        ip=ip,
        search=search.strip() if search and search.strip() else None,
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
