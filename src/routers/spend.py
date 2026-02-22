from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.middleware.admin import require_master_key

spend_router = APIRouter(prefix="/spend", tags=["Spend"])
global_router = APIRouter(prefix="/global", tags=["Global Spend"])


def _db_or_503(request: Request) -> Any:
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if db is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
    return db


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_to_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_json_value(v) for k, v in value.items()}
    return value


def _date_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


def _build_spendlog_where(
    *,
    api_key: str | None,
    user_id: str | None,
    team_id: str | None,
    model: str | None,
    start_date: date | None,
    end_date: date | None,
    tags: list[str] | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    def add_clause(template: str, value: Any) -> None:
        params.append(value)
        clauses.append(template.format(i=len(params)))

    if api_key:
        add_clause("api_key = ${i}", api_key)
    if user_id:
        add_clause('"user" = ${i}', user_id)
    if team_id:
        add_clause("team_id = ${i}", team_id)
    if model:
        add_clause("model = ${i}", model)

    start_dt = _date_start(start_date)
    end_dt = _date_end(end_date)
    if start_dt is not None:
        add_clause("start_time >= ${i}", start_dt)
    if end_dt is not None:
        add_clause("start_time <= ${i}", end_dt)

    if tags:
        for tag in tags:
            add_clause("request_tags @> ${i}::text[]", [tag])

    if not clauses:
        return "", params

    return " WHERE " + " AND ".join(clauses), params


@spend_router.get("/logs")
async def get_spend_logs(
    request: Request,
    _: str = Depends(require_master_key),
    api_key: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    db = _db_or_503(request)
    where_sql, params = _build_spendlog_where(
        api_key=api_key,
        user_id=user_id,
        team_id=team_id,
        model=model,
        start_date=start_date,
        end_date=end_date,
        tags=tags,
    )

    log_params = [*params, limit, offset]
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2

    logs = await db.query_raw(
        f"""
        SELECT
            id,
            request_id,
            call_type,
            model,
            api_base,
            api_key,
            spend,
            total_tokens,
            prompt_tokens,
            completion_tokens,
            start_time,
            end_time,
            "user",
            team_id,
            cache_hit,
            request_tags
        FROM litellm_spendlogs
        {where_sql}
        ORDER BY start_time DESC
        LIMIT ${limit_idx}
        OFFSET ${offset_idx}
        """,
        *log_params,
    )

    total_rows = await db.query_raw(
        f"""
        SELECT COUNT(*) AS total
        FROM litellm_spendlogs
        {where_sql}
        """,
        *params,
    )
    total = int((total_rows[0] if total_rows else {}).get("total") or 0)

    return {
        "logs": [_to_json_value(dict(row)) for row in logs],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@global_router.get("/spend")
async def get_global_spend(
    request: Request,
    _: str = Depends(require_master_key),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict[str, Any]:
    db = _db_or_503(request)
    where_sql, params = _build_spendlog_where(
        api_key=None,
        user_id=None,
        team_id=None,
        model=None,
        start_date=start_date,
        end_date=end_date,
        tags=None,
    )
    rows = await db.query_raw(
        f"""
        SELECT
            COALESCE(SUM(spend), 0) AS total_spend,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COUNT(*) AS total_requests
        FROM litellm_spendlogs
        {where_sql}
        """,
        *params,
    )
    return _to_json_value(dict(rows[0] if rows else {}))


@global_router.get("/spend/report")
async def get_spend_report(
    request: Request,
    _: str = Depends(require_master_key),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    group_by: str = Query(pattern="^(model|provider|day|user|team)$", default="model"),
) -> dict[str, Any]:
    db = _db_or_503(request)
    group_column = {
        "model": "model",
        "provider": "api_base",
        "day": "DATE(start_time)",
        "user": "COALESCE(\"user\", 'anonymous')",
        "team": "COALESCE(team_id, 'none')",
    }.get(group_by, "model")

    where_sql, params = _build_spendlog_where(
        api_key=None,
        user_id=None,
        team_id=None,
        model=None,
        start_date=start_date,
        end_date=end_date,
        tags=None,
    )
    rows = await db.query_raw(
        f"""
        SELECT
            {group_column} AS group_key,
            COALESCE(SUM(spend), 0) AS total_spend,
            COUNT(*) AS request_count,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(AVG(spend), 0) AS avg_spend_per_request
        FROM litellm_spendlogs
        {where_sql}
        GROUP BY {group_column}
        ORDER BY total_spend DESC
        """,
        *params,
    )
    return {
        "group_by": group_by,
        "breakdown": [_to_json_value(dict(row)) for row in rows],
    }


@global_router.get("/spend/keys")
async def get_spend_per_key(request: Request, _: str = Depends(require_master_key)) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT token, key_name, spend, max_budget, user_id, team_id
        FROM litellm_verificationtoken
        ORDER BY spend DESC
        """
    )
    return [_to_json_value(dict(row)) for row in rows]


@global_router.get("/spend/teams")
async def get_spend_per_team(request: Request, _: str = Depends(require_master_key)) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT team_id, team_alias, spend, max_budget
        FROM litellm_teamtable
        ORDER BY spend DESC
        """
    )
    return [_to_json_value(dict(row)) for row in rows]


@global_router.get("/spend/end_users")
async def get_spend_per_end_user(
    request: Request,
    _: str = Depends(require_master_key),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    where_sql, params = _build_spendlog_where(
        api_key=None,
        user_id=None,
        team_id=None,
        model=None,
        start_date=start_date,
        end_date=end_date,
        tags=None,
    )

    rows = await db.query_raw(
        f"""
        SELECT
            COALESCE(end_user, "user", 'anonymous') AS end_user_id,
            COALESCE(SUM(spend), 0) AS total_spend,
            COUNT(*) AS request_count
        FROM litellm_spendlogs
        {where_sql}
        GROUP BY end_user_id
        ORDER BY total_spend DESC
        """,
        *params,
    )
    return [_to_json_value(dict(row)) for row in rows]


@global_router.get("/spend/models")
async def get_spend_per_model(
    request: Request,
    _: str = Depends(require_master_key),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    where_sql, params = _build_spendlog_where(
        api_key=None,
        user_id=None,
        team_id=None,
        model=None,
        start_date=start_date,
        end_date=end_date,
        tags=None,
    )

    rows = await db.query_raw(
        f"""
        SELECT
            model,
            COALESCE(SUM(spend), 0) AS total_spend,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COUNT(*) AS request_count
        FROM litellm_spendlogs
        {where_sql}
        GROUP BY model
        ORDER BY total_spend DESC
        """,
        *params,
    )
    return [_to_json_value(dict(row)) for row in rows]
