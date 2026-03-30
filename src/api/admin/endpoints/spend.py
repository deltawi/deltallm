from __future__ import annotations

from datetime import UTC, date, datetime, time
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from src.api.admin.endpoints.common import db_or_503, get_auth_scope, log_admin_query_timing, to_json_value
from src.auth.roles import Permission
from src.billing.spend_read import SpendReadSource, apply_org_scope, get_spend_read_source
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Spend"])


def _date_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


def _grouped_spend_config(group_by: str, source: SpendReadSource) -> dict[str, Any]:
    if group_by == "model":
        return {
            "group_expr": "s.model",
            "display_expr": "NULL",
            "group_by_exprs": ["s.model"],
            "search_clause": "s.model ILIKE ${i}",
        }
    if group_by == "organization":
        return {
            "joins": [
                "LEFT JOIN deltallm_teamtable t ON t.team_id = s.team_id",
                "LEFT JOIN deltallm_organizationtable o ON o.organization_id = t.organization_id",
            ],
            "group_expr": "COALESCE(t.organization_id, 'none')",
            "display_expr": "NULLIF(TRIM(COALESCE(o.organization_name, '')), '')",
            "group_by_exprs": [
                "COALESCE(t.organization_id, 'none')",
                "NULLIF(TRIM(COALESCE(o.organization_name, '')), '')",
            ],
            "search_clause": "(COALESCE(t.organization_id, 'none') ILIKE ${i} OR COALESCE(o.organization_name, '') ILIKE ${i})",
        }
    if group_by == "team":
        return {
            "joins": ["LEFT JOIN deltallm_teamtable t ON t.team_id = s.team_id"],
            "group_expr": "COALESCE(s.team_id, 'none')",
            "display_expr": "NULLIF(TRIM(COALESCE(t.team_alias, '')), '')",
            "group_by_exprs": [
                "COALESCE(s.team_id, 'none')",
                "NULLIF(TRIM(COALESCE(t.team_alias, '')), '')",
            ],
            "search_clause": "(COALESCE(s.team_id, 'none') ILIKE ${i} OR COALESCE(t.team_alias, '') ILIKE ${i})",
        }
    if group_by == "api_key":
        return {
            "joins": ["LEFT JOIN deltallm_verificationtoken vt ON vt.token = s.api_key"],
            "group_expr": "s.api_key",
            "display_expr": "NULLIF(TRIM(COALESCE(vt.key_name, '')), '')",
            "group_by_exprs": [
                "s.api_key",
                "NULLIF(TRIM(COALESCE(vt.key_name, '')), '')",
            ],
            "search_clause": "(s.api_key ILIKE ${i} OR COALESCE(vt.key_name, '') ILIKE ${i})",
        }
    if group_by == "provider":
        return {
            "group_expr": "COALESCE(s.api_base, 'unknown')",
            "display_expr": "NULL",
            "group_by_exprs": ["COALESCE(s.api_base, 'unknown')"],
            "search_clause": "COALESCE(s.api_base, 'unknown') ILIKE ${i}",
        }
    return {
        "group_expr": f"COALESCE({source.column('user_column', table_alias='s')}, 'anonymous')",
        "display_expr": "NULL",
        "group_by_exprs": [f"COALESCE({source.column('user_column', table_alias='s')}, 'anonymous')"],
        "search_clause": f"COALESCE({source.column('user_column', table_alias='s')}, 'anonymous') ILIKE ${{i}}",
    }


@router.get("/ui/api/spend/summary", dependencies=[Depends(require_admin_permission(Permission.SPEND_READ))])
async def spend_summary(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.SPEND_READ)
    db = db_or_503(request)
    source = get_spend_read_source()
    clauses: list[str] = []
    params: list[Any] = []

    if start_date is not None:
        params.append(_date_start(start_date))
        clauses.append(f"start_time >= ${len(params)}::timestamp")
    if end_date is not None:
        params.append(_date_end(end_date))
        clauses.append(f"start_time <= ${len(params)}::timestamp")
    if not scope.is_platform_admin:
        apply_org_scope(clauses=clauses, params=params, org_ids=scope.org_ids, source=source)

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    rows = await db.query_raw(
        f"""
        SELECT
            COALESCE(SUM(spend), 0) AS total_spend,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM({source.prompt_tokens_column}), 0) AS prompt_tokens,
            COALESCE(SUM({source.completion_tokens_column}), 0) AS completion_tokens,
            COUNT(*) AS total_requests,
            COUNT(*) FILTER (WHERE COALESCE(status, 'success') = 'success') AS successful_requests,
            COUNT(*) FILTER (WHERE status = 'error') AS failed_requests
        FROM {source.table}
        {where_sql}
        """,
        *params,
    )
    log_admin_query_timing(
        "spend_summary",
        started_at,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
        scoped=not scope.is_platform_admin,
    )
    return to_json_value(dict(rows[0] if rows else {}))


@router.get("/ui/api/spend/report", dependencies=[Depends(require_admin_permission(Permission.SPEND_READ))])
async def spend_report(
    request: Request,
    group_by: str = Query(default="day", pattern="^(model|provider|day|user|team|organization|api_key)$"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.SPEND_READ)
    db = db_or_503(request)
    source = get_spend_read_source()
    if group_by == "day":
        clauses: list[str] = []
        params: list[Any] = []
        if start_date is not None:
            params.append(_date_start(start_date))
            clauses.append(f"start_time >= ${len(params)}::timestamp")
        if end_date is not None:
            params.append(_date_end(end_date))
            clauses.append(f"start_time <= ${len(params)}::timestamp")
        if not scope.is_platform_admin:
            apply_org_scope(clauses=clauses, params=params, org_ids=scope.org_ids, source=source)

        where_sql = ""
        if clauses:
            where_sql = " WHERE " + " AND ".join(clauses)

        rows = await db.query_raw(
            f"""
            SELECT
                DATE(start_time) AS group_key,
                COALESCE(SUM(spend), 0) AS total_spend,
                COUNT(*) AS request_count,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COUNT(*) FILTER (WHERE COALESCE(status, 'success') = 'success') AS successful_requests,
                COUNT(*) FILTER (WHERE status = 'error') AS failed_requests
            FROM {source.table}
            {where_sql}
            GROUP BY DATE(start_time)
            ORDER BY group_key ASC
            """,
            *params,
        )
        log_admin_query_timing(
            "spend_report_day",
            started_at,
            start_date=start_date.isoformat() if start_date else None,
            end_date=end_date.isoformat() if end_date else None,
            scoped=not scope.is_platform_admin,
        )
        return {
            "group_by": group_by,
            "breakdown": [to_json_value(dict(row)) for row in rows],
        }

    config = _grouped_spend_config(group_by, source)
    clauses: list[str] = []
    params: list[Any] = []
    joins = list(config.get("joins", []))
    group_expr = config["group_expr"]
    display_expr = config["display_expr"]
    group_by_sql = ", ".join(config.get("group_by_exprs", [group_expr]))

    if start_date is not None:
        params.append(_date_start(start_date))
        clauses.append(f"s.start_time >= ${len(params)}::timestamp")
    if end_date is not None:
        params.append(_date_end(end_date))
        clauses.append(f"s.start_time <= ${len(params)}::timestamp")
    if search:
        params.append(f"%{search.strip()}%")
        clauses.append(config["search_clause"].format(i=len(params)))
    if not scope.is_platform_admin:
        apply_org_scope(clauses=clauses, params=params, org_ids=scope.org_ids, source=source, table_alias="s")

    join_sql = ""
    if joins:
        join_sql = "\n        " + "\n        ".join(joins)

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    page_params = [*params, limit, offset]
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    rows = await db.query_raw(
        f"""
        WITH grouped AS (
            SELECT
                {group_expr} AS group_key,
                {display_expr} AS display_name,
                COALESCE(SUM(s.spend), 0) AS total_spend,
                COUNT(*) AS request_count,
                COALESCE(SUM(s.total_tokens), 0) AS total_tokens
            FROM {source.table} s
            {join_sql}
            {where_sql}
            GROUP BY {group_by_sql}
        )
        SELECT
            group_key,
            display_name,
            total_spend,
            request_count,
            total_tokens,
            COUNT(*) OVER() AS total_count
        FROM grouped
        ORDER BY total_spend DESC, group_key ASC
        LIMIT ${limit_idx}
        OFFSET ${offset_idx}
        """,
        *page_params,
    )
    total = int((rows[0] if rows else {}).get("total_count") or 0)
    log_admin_query_timing(
        "spend_report_grouped",
        started_at,
        group_by=group_by,
        search=search.strip() if search else None,
        limit=limit,
        offset=offset,
        scoped=not scope.is_platform_admin,
    )

    return {
        "group_by": group_by,
        "data": [to_json_value({k: v for k, v in dict(row).items() if k != "total_count"}) for row in rows],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.get("/ui/api/logs", dependencies=[Depends(require_admin_permission(Permission.SPEND_READ))])
async def request_logs(
    request: Request,
    model: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.SPEND_READ)
    db = db_or_503(request)
    source = get_spend_read_source()

    clauses: list[str] = []
    params: list[Any] = []

    def add_clause(template: str, value: Any) -> None:
        params.append(value)
        clauses.append(template.format(i=len(params)))

    if model:
        add_clause("model = ${i}", model)
    if team_id:
        add_clause("team_id = ${i}", team_id)
    if user_id:
        add_clause(f"{source.user_column} = ${{i}}", user_id)
    if start_date is not None:
        add_clause("start_time >= ${i}::timestamp", _date_start(start_date))
    if end_date is not None:
        add_clause("start_time <= ${i}::timestamp", _date_end(end_date))
    if not scope.is_platform_admin:
        apply_org_scope(clauses=clauses, params=params, org_ids=scope.org_ids, source=source)

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    limit_idx = len(params) + 1
    offset_idx = len(params) + 2

    logs = await db.query_raw(
        f"""
        SELECT id, request_id, call_type, model, api_base, api_key, spend, total_tokens,
               {source.prompt_tokens_column} AS prompt_tokens,
               {source.completion_tokens_column} AS completion_tokens,
               {source.cached_prompt_tokens_column} AS prompt_tokens_cached,
               {source.cached_completion_tokens_column} AS completion_tokens_cached,
               start_time, end_time, {source.user_column} AS "user", team_id, {source.end_user_column} AS end_user,
               metadata, cache_hit, cache_key, request_tags, status, http_status_code, error_type
        FROM {source.table}
        {where_sql}
        ORDER BY start_time DESC
        LIMIT ${limit_idx}
        OFFSET ${offset_idx}
        """,
        *params,
        limit,
        offset,
    )

    total_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM {source.table} {where_sql}",
        *params,
    )

    total = int((total_rows[0] if total_rows else {}).get("total") or 0)
    log_admin_query_timing(
        "request_logs",
        started_at,
        model=model,
        team_id=team_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
        scoped=not scope.is_platform_admin,
    )

    return {
        "logs": [to_json_value(dict(row)) for row in logs],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }
