from __future__ import annotations

import base64
import binascii
from datetime import UTC, date, datetime, time
import json
import logging
from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from src.api.admin.endpoints.common import (
    db_or_503,
    get_auth_scope,
    log_admin_query_timing,
    to_json_value,
)
from src.auth.roles import Permission
from src.billing.spend_read import SpendReadSource, get_spend_read_source
from src.db.spend_components import external_request_sql
from src.middleware.admin import require_any_admin_permission
from src.providers.resolution import provider_from_model, resolve_provider
from src.services.spend_reporting_cache import reporting_cache_ttl
from src.api.admin.spend_reporting_dependencies import (
    _resolve_reporting_visibility,
    _reporting_v2_enabled,
    _reporting_context,
    _reporting_cache,
    _reporting_cache_revalidation_requested,
    _load_reporting_response,
    _run_uncached_reporting_response,
)
from src.db.reporting import (
    _run_reporting_query,
    _run_reporting_transaction,
    _run_reporting_statement,
)
from src.services.spend_visibility import (
    SPEND_VISIBILITY_PERMISSIONS,
    SpendVisibility,
    apply_spend_visibility,
)

router = APIRouter(tags=["Spend"])
logger = logging.getLogger(__name__)

_USAGE_SCOPE_COLUMNS = {
    "organization": "organization_column",
    "team": "team_id",
    "user": "user_column",
}


def _date_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


def _encode_request_log_cursor(start_time: Any, row_id: Any) -> str:
    if isinstance(start_time, datetime):
        timestamp = start_time.isoformat()
    else:
        timestamp = str(start_time or "").strip()
    identifier = str(row_id or "").strip()
    if not timestamp or not identifier:
        raise ValueError("Request log cursor rows require start_time and id")
    payload = json.dumps(
        {"start_time": timestamp, "id": identifier},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_request_log_cursor(value: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        timestamp = datetime.fromisoformat(str(payload["start_time"]).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        identifier = str(payload["id"]).strip()
        if not identifier:
            raise ValueError("empty cursor id")
        return timestamp.astimezone(UTC), identifier
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid request log cursor") from exc


def _provider_from_api_base_expr(api_base_expr: str) -> str:
    lowered_api_base = f"LOWER(COALESCE({api_base_expr}, ''))"
    return f"""
        CASE
            WHEN {lowered_api_base} LIKE '%openai.azure.com%' THEN 'azure_openai'
            WHEN {lowered_api_base} LIKE '%api.groq.com%' OR {lowered_api_base} LIKE '%groq%' THEN 'groq'
            WHEN {lowered_api_base} LIKE '%openrouter.ai%' OR {lowered_api_base} LIKE '%openrouter%' THEN 'openrouter'
            WHEN {lowered_api_base} LIKE '%fireworks%' THEN 'fireworks'
            WHEN {lowered_api_base} LIKE '%together%' THEN 'together'
            WHEN {lowered_api_base} LIKE '%deepinfra%' THEN 'deepinfra'
            WHEN {lowered_api_base} LIKE '%perplexity%' THEN 'perplexity'
            WHEN {lowered_api_base} LIKE '%anthropic%' THEN 'anthropic'
            WHEN {lowered_api_base} LIKE '%googleapis.com%' OR {lowered_api_base} LIKE '%generativelanguage.googleapis.com%' THEN 'gemini'
            WHEN {lowered_api_base} LIKE '%bedrock%' THEN 'bedrock'
            WHEN {lowered_api_base} LIKE '%api.openai.com%' OR {lowered_api_base} LIKE '%openai.com%' THEN 'openai'
            WHEN {lowered_api_base} LIKE '%azure%' THEN 'azure_openai'
            ELSE NULL
        END
    """.strip()


def _provider_from_model_expr(model_expr: str) -> str:
    lowered_model = f"LOWER(COALESCE({model_expr}, ''))"
    return f"""
        CASE
            WHEN {lowered_model} LIKE 'azure_openai/%' OR {lowered_model} LIKE 'azure/%' THEN 'azure_openai'
            WHEN {lowered_model} LIKE 'anthropic/%' THEN 'anthropic'
            WHEN {lowered_model} LIKE 'openrouter/%' THEN 'openrouter'
            WHEN {lowered_model} LIKE 'groq/%' THEN 'groq'
            WHEN {lowered_model} LIKE 'together/%' THEN 'together'
            WHEN {lowered_model} LIKE 'fireworks/%' THEN 'fireworks'
            WHEN {lowered_model} LIKE 'deepinfra/%' THEN 'deepinfra'
            WHEN {lowered_model} LIKE 'perplexity/%' THEN 'perplexity'
            WHEN {lowered_model} LIKE 'gemini/%' THEN 'gemini'
            WHEN {lowered_model} LIKE 'bedrock/%' THEN 'bedrock'
            WHEN {lowered_model} LIKE 'vllm/%' THEN 'vllm'
            WHEN {lowered_model} LIKE 'lmstudio/%' THEN 'lmstudio'
            WHEN {lowered_model} LIKE 'ollama/%' THEN 'ollama'
            WHEN {lowered_model} LIKE 'openai/%' THEN 'openai'
            ELSE NULL
        END
    """.strip()


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _legacy_cache_provider_override_expr(
    *,
    table_alias: str = "s",
    model_provider_overrides: dict[str, str] | None = None,
) -> str:
    if not model_provider_overrides:
        return "NULL"

    lowered_api_base = f"LOWER(COALESCE({table_alias}.api_base, ''))"
    cases = []
    for model, provider in sorted(model_provider_overrides.items()):
        model_literal = _sql_string_literal(model)
        provider_literal = _sql_string_literal(provider)
        cases.append(
            f"""WHEN (
                LOWER(COALESCE({table_alias}.deployment_model, '')) = {model_literal}
                OR LOWER(COALESCE({table_alias}.model, '')) = {model_literal}
            ) THEN {provider_literal}"""
        )
    case_sql = "\n            ".join(cases)
    return f"""
        CASE
            WHEN {lowered_api_base} <> 'cache' THEN NULL
            {case_sql}
            ELSE NULL
        END
    """.strip()


def _canonical_provider_expr(
    *,
    table_alias: str = "s",
    model_provider_overrides: dict[str, str] | None = None,
) -> str:
    provider_column = f"{table_alias}.provider"
    metadata_provider_column = f"{table_alias}.metadata->>'provider'"
    api_base_column = f"{table_alias}.api_base"
    api_base_provider_expr = _provider_from_api_base_expr(api_base_column)
    legacy_cache_provider_expr = _legacy_cache_provider_override_expr(
        table_alias=table_alias,
        model_provider_overrides=model_provider_overrides,
    )
    deployment_model_provider_expr = _provider_from_model_expr(f"{table_alias}.deployment_model")
    model_provider_expr = _provider_from_model_expr(f"{table_alias}.model")
    return f"""
        COALESCE(
            NULLIF(LOWER(TRIM(COALESCE({metadata_provider_column}, ''))), ''),
            NULLIF(LOWER(TRIM({provider_column})), ''),
            {legacy_cache_provider_expr},
            {api_base_provider_expr},
            {deployment_model_provider_expr},
            {model_provider_expr},
            'unknown'
        )
    """.strip()


def _legacy_cache_model_provider_overrides(request: Request) -> dict[str, str]:
    registry = getattr(request.app.state, "model_registry", {}) or {}
    provider_candidates: dict[str, set[str]] = {}
    for deployments in registry.values():
        if not isinstance(deployments, list):
            continue
        for deployment in deployments:
            params = deployment.get("deltallm_params") if isinstance(deployment, dict) else None
            if not isinstance(params, dict):
                continue
            deployment_model = str(params.get("model") or "").strip()
            provider = resolve_provider(params)
            if not deployment_model or provider == "unknown":
                continue
            if provider_from_model(deployment_model) == provider:
                continue
            provider_candidates.setdefault(deployment_model.lower(), set()).add(provider)
    return {
        deployment_model: next(iter(providers))
        for deployment_model, providers in provider_candidates.items()
        if len(providers) == 1
    }


def _grouped_spend_config(
    group_by: str,
    source: SpendReadSource,
    *,
    model_provider_overrides: dict[str, str] | None = None,
    user_identity_labels_visible: bool = False,
) -> dict[str, Any]:
    if group_by == "model":
        return {
            "group_expr": "s.model",
            "display_expr": "NULL",
            "group_by_exprs": ["s.model"],
            "search_clause": "(COALESCE(s.model, '') ILIKE ${i} OR (s.model IS NULL AND 'unspecified model' ILIKE ${i}))",
        }
    if group_by == "organization":
        return {
            "joins": [
                "LEFT JOIN deltallm_organizationtable o ON o.organization_id = s.organization_id"
            ],
            "group_expr": "s.organization_id",
            "display_expr": "NULLIF(TRIM(COALESCE(o.organization_name, '')), '')",
            "group_by_exprs": [
                "s.organization_id",
                "NULLIF(TRIM(COALESCE(o.organization_name, '')), '')",
            ],
            "search_clause": "(COALESCE(s.organization_id, '') ILIKE ${i} OR COALESCE(o.organization_name, '') ILIKE ${i} OR (s.organization_id IS NULL AND 'unassigned organization' ILIKE ${i}))",
        }
    if group_by == "team":
        return {
            "joins": ["LEFT JOIN deltallm_teamtable t ON t.team_id = s.team_id"],
            "group_expr": "s.team_id",
            "display_expr": "NULLIF(TRIM(COALESCE(t.team_alias, '')), '')",
            "group_by_exprs": [
                "s.team_id",
                "NULLIF(TRIM(COALESCE(t.team_alias, '')), '')",
            ],
            "search_clause": "(COALESCE(s.team_id, '') ILIKE ${i} OR COALESCE(t.team_alias, '') ILIKE ${i} OR (s.team_id IS NULL AND 'unassigned team' ILIKE ${i}))",
        }
    if group_by == "user":
        user_column = source.column("user_column", table_alias="s")
        if not user_identity_labels_visible:
            return {
                "group_expr": user_column,
                "display_expr": "NULL",
                "group_by_exprs": [user_column],
                "search_clause": f"(COALESCE({user_column}, '') ILIKE ${{i}} OR ({user_column} IS NULL AND 'unassigned user' ILIKE ${{i}}))",
            }
        return {
            "joins": [f"LEFT JOIN deltallm_usertable u ON u.user_id = {user_column}"],
            "group_expr": user_column,
            "display_expr": "NULLIF(TRIM(COALESCE(u.user_email, '')), '')",
            "group_by_exprs": [
                user_column,
                "NULLIF(TRIM(COALESCE(u.user_email, '')), '')",
            ],
            "search_clause": f"(COALESCE({user_column}, '') ILIKE ${{i}} OR COALESCE(u.user_email, '') ILIKE ${{i}} OR ({user_column} IS NULL AND 'unassigned user' ILIKE ${{i}}))",
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
            "search_clause": "(COALESCE(s.api_key, '') ILIKE ${i} OR COALESCE(vt.key_name, '') ILIKE ${i} OR (s.api_key IS NULL AND 'unassigned api key' ILIKE ${i}))",
        }
    if group_by == "provider":
        provider_expr = _canonical_provider_expr(
            table_alias="s",
            model_provider_overrides=model_provider_overrides,
        )
        return {
            "group_expr": provider_expr,
            "display_expr": "NULL",
            "group_by_exprs": [provider_expr],
            "search_clause": f"({provider_expr} ILIKE ${{i}} OR COALESCE(s.api_base, '') ILIKE ${{i}})",
        }
    raise ValueError(f"Unsupported spend grouping: {group_by}")


def _user_identity_labels_visible(scope: Any, visibility: SpendVisibility) -> bool:
    if visibility.is_platform_admin:
        return True

    checks: list[bool] = []
    if visibility.view == "organization":
        checks.extend(
            Permission.USER_READ in (scope.org_permissions_by_id or {}).get(organization_id, set())
            for organization_id in visibility.organization_ids
        )
    elif visibility.view == "team":
        checks.extend(
            Permission.USER_READ in (scope.team_permissions_by_id or {}).get(team_id, set())
            for team_id in visibility.team_ids
        )
    return bool(checks) and all(checks)


def _reporting_scope_cache_payload(visibility: SpendVisibility) -> dict[str, Any]:
    return visibility.cache_payload()


def _summary_reporting_cache_payload(
    *,
    source: SpendReadSource,
    visibility: SpendVisibility,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    return {
        "endpoint": "summary",
        "component_count_schema": 1,
        "source": source.table,
        "scope": _reporting_scope_cache_payload(visibility),
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
    }


def _spend_report_cache_payload(
    *,
    source: SpendReadSource,
    visibility: SpendVisibility,
    group_by: str,
    interval: str,
    start_date: date | None,
    end_date: date | None,
    search: str | None,
    sort_by: str,
    scope_type: str | None,
    scope_id: str | None,
    scope_unassigned: bool,
    model_provider_overrides: dict[str, str] | None,
    user_identity_labels_visible: bool,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "endpoint": "report",
        "source": source.table,
        "scope": _reporting_scope_cache_payload(visibility),
        "group_by": group_by,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "scope_unassigned": scope_unassigned,
        "response_schema": 3,
    }
    if group_by == "day":
        payload["interval"] = interval
        return payload

    payload.update(
        {
            "search": search,
            "sort_by": sort_by,
            "limit": limit,
            "offset": offset,
        }
    )
    if group_by == "provider":
        payload["model_provider_overrides"] = model_provider_overrides
    if group_by == "user":
        payload["user_identity_labels"] = user_identity_labels_visible
    return payload


def _apply_usage_scope(
    *,
    clauses: list[str],
    params: list[Any],
    source: SpendReadSource,
    scope_type: Literal["organization", "team", "user"] | None,
    scope_id: str | None,
    scope_unassigned: bool,
    table_alias: str | None = None,
) -> None:
    if scope_type is None:
        return

    column_name = _USAGE_SCOPE_COLUMNS[scope_type]
    if column_name == "team_id":
        column = "team_id" if table_alias is None else f"{table_alias}.team_id"
    else:
        column = source.column(column_name, table_alias=table_alias)

    if scope_unassigned:
        clauses.append(f"{column} IS NULL")
        return

    if scope_id is None:
        return
    params.append(scope_id)
    clauses.append(f"{column} = ${len(params)}")


@router.get(
    "/ui/api/spend/summary",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def spend_summary(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    view: Literal["organization", "team", "self"] | None = Query(default=None),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.SPEND_READ,
    )
    visibility = _resolve_reporting_visibility(request, scope, view)
    db = db_or_503(request)
    cache = await _reporting_cache(request)
    source = get_spend_read_source()
    cache_ttl = reporting_cache_ttl(start_date, end_date)
    force_refresh = _reporting_cache_revalidation_requested(cache_control)
    cache_key = cache.key(
        _summary_reporting_cache_payload(
            source=source,
            visibility=visibility,
            start_date=start_date,
            end_date=end_date,
        )
    )
    clauses: list[str] = []
    params: list[Any] = []

    if start_date is not None:
        params.append(_date_start(start_date))
        clauses.append(f"start_time >= ${len(params)}::timestamp")
    if end_date is not None:
        params.append(_date_end(end_date))
        clauses.append(f"start_time <= ${len(params)}::timestamp")
    apply_spend_visibility(
        clauses=clauses,
        params=params,
        visibility=visibility,
        source=source,
    )

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    async def load_summary() -> dict[str, Any]:
        rows = await _run_reporting_query(
            db,
            cache,
            f"""
            SELECT
                COALESCE(SUM(spend), 0) AS total_spend,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM({source.prompt_tokens_column}), 0) AS prompt_tokens,
                COALESCE(SUM({source.completion_tokens_column}), 0) AS completion_tokens,
                COUNT(*) FILTER (WHERE {external_request_sql()}) AS total_requests,
                COUNT(DISTINCT model) FILTER (WHERE NULLIF(TRIM(model), '') IS NOT NULL) AS unique_models,
                COUNT(*) FILTER (WHERE {external_request_sql()} AND COALESCE(status, 'success') = 'success') AS successful_requests,
                COUNT(*) FILTER (WHERE {external_request_sql()} AND status = 'error') AS failed_requests
            FROM {source.table}
            {where_sql}
            """,
            *params,
        )
        return to_json_value(dict(rows[0] if rows else {}))

    cache_result = await _load_reporting_response(
        cache=cache,
        cache_key=cache_key,
        cache_ttl=cache_ttl,
        loader=load_summary,
        force_refresh=force_refresh,
    )
    log_admin_query_timing(
        "spend_summary",
        started_at,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
        scoped=not scope.is_platform_admin,
        cache_hit=cache_result.cache_hit,
        cache_status=cache_result.status,
        force_refresh=force_refresh,
        cache_ttl_seconds=cache_ttl,
    )
    return {
        **cache_result.value,
        "reporting_context": _reporting_context(request, visibility),
        "capabilities": visibility.capabilities(
            user_identity_labels=_user_identity_labels_visible(scope, visibility),
        ),
    }


@router.get(
    "/ui/api/spend/report",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def spend_report(
    request: Request,
    group_by: str = Query(
        default="day", pattern="^(model|provider|day|user|team|organization|api_key)$"
    ),
    interval: Literal["day", "week", "month"] = Query(default="day"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    view: Literal["organization", "team", "self"] | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_by: Literal["spend", "tokens"] = Query(default="spend"),
    scope_type: Literal["organization", "team", "user"] | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    scope_unassigned: bool = Query(default=False),
    limit: int = Query(default=5, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    scope_id = scope_id.strip() if scope_id and scope_id.strip() else None
    if scope_type is None and (scope_id is not None or scope_unassigned):
        raise HTTPException(status_code=422, detail="scope_type is required for a usage scope")
    if scope_type is not None and ((scope_id is not None) == scope_unassigned):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of scope_id or scope_unassigned=true",
        )
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.SPEND_READ,
    )
    visibility = _resolve_reporting_visibility(request, scope, view)
    if group_by not in visibility.allowed_groupings:
        raise HTTPException(
            status_code=403, detail="This usage dimension is outside your reporting scope"
        )
    if scope_type is not None and scope_type not in visibility.allowed_dimensions:
        raise HTTPException(
            status_code=403, detail="This usage filter is outside your reporting scope"
        )
    db = db_or_503(request)
    source = get_spend_read_source()
    user_identity_labels_visible = _user_identity_labels_visible(scope, visibility)
    model_provider_overrides = (
        _legacy_cache_model_provider_overrides(request) if group_by == "provider" else None
    )
    cache = await _reporting_cache(request)
    cache_ttl = reporting_cache_ttl(start_date, end_date)
    force_refresh = _reporting_cache_revalidation_requested(cache_control)
    normalized_search = search.strip() if search and search.strip() else None
    cache_key = cache.key(
        _spend_report_cache_payload(
            source=source,
            visibility=visibility,
            group_by=group_by,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            search=normalized_search,
            sort_by=sort_by,
            scope_type=scope_type,
            scope_id=scope_id,
            scope_unassigned=scope_unassigned,
            model_provider_overrides=model_provider_overrides,
            user_identity_labels_visible=user_identity_labels_visible,
            limit=limit,
            offset=offset,
        )
    )
    if group_by == "day":
        bucket_expr = {
            "day": "DATE(start_time)",
            "week": "DATE_TRUNC('week', start_time)::date",
            "month": "DATE_TRUNC('month', start_time)::date",
        }[interval]
        clauses: list[str] = []
        params: list[Any] = []
        if start_date is not None:
            params.append(_date_start(start_date))
            clauses.append(f"start_time >= ${len(params)}::timestamp")
        if end_date is not None:
            params.append(_date_end(end_date))
            clauses.append(f"start_time <= ${len(params)}::timestamp")
        apply_spend_visibility(
            clauses=clauses,
            params=params,
            visibility=visibility,
            source=source,
        )
        _apply_usage_scope(
            clauses=clauses,
            params=params,
            source=source,
            scope_type=scope_type,
            scope_id=scope_id,
            scope_unassigned=scope_unassigned,
        )

        where_sql = ""
        if clauses:
            where_sql = " WHERE " + " AND ".join(clauses)

        async def load_time_series() -> dict[str, Any]:
            rows = await _run_reporting_query(
                db,
                cache,
                f"""
                SELECT
                    {bucket_expr} AS group_key,
                    COALESCE(SUM(spend), 0) AS total_spend,
                    COUNT(*) FILTER (WHERE {external_request_sql()}) AS request_count,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COUNT(*) FILTER (WHERE {external_request_sql()} AND COALESCE(status, 'success') = 'success') AS successful_requests,
                    COUNT(*) FILTER (WHERE {external_request_sql()} AND status = 'error') AS failed_requests
                FROM {source.table}
                {where_sql}
                GROUP BY {bucket_expr}
                ORDER BY group_key ASC
                """,
                *params,
            )
            return {
                "group_by": group_by,
                "interval": interval,
                "breakdown": [to_json_value(dict(row)) for row in rows],
            }

        cache_result = await _load_reporting_response(
            cache=cache,
            cache_key=cache_key,
            cache_ttl=cache_ttl,
            loader=load_time_series,
            force_refresh=force_refresh,
        )
        log_admin_query_timing(
            "spend_report_day",
            started_at,
            interval=interval,
            start_date=start_date.isoformat() if start_date else None,
            end_date=end_date.isoformat() if end_date else None,
            scoped=not scope.is_platform_admin,
            cache_hit=cache_result.cache_hit,
            cache_status=cache_result.status,
            force_refresh=force_refresh,
            cache_ttl_seconds=cache_ttl,
        )
        return {
            **cache_result.value,
            "reporting_context": _reporting_context(request, visibility),
        }

    config = _grouped_spend_config(
        group_by,
        source,
        model_provider_overrides=model_provider_overrides,
        user_identity_labels_visible=user_identity_labels_visible,
    )
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
    if normalized_search:
        params.append(f"%{normalized_search}%")
        clauses.append(config["search_clause"].format(i=len(params)))
    apply_spend_visibility(
        clauses=clauses,
        params=params,
        visibility=visibility,
        source=source,
        table_alias="s",
    )
    _apply_usage_scope(
        clauses=clauses,
        params=params,
        source=source,
        scope_type=scope_type,
        scope_id=scope_id,
        scope_unassigned=scope_unassigned,
        table_alias="s",
    )

    join_sql = ""
    if joins:
        join_sql = "\n        " + "\n        ".join(joins)

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    page_params = [*params, limit, offset]
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    sort_expression = {"spend": "total_spend", "tokens": "total_tokens"}[sort_by]

    async def load_grouped_report() -> dict[str, Any]:
        rows = await _run_reporting_query(
            db,
            cache,
            f"""
            WITH grouped AS (
                SELECT
                    {group_expr} AS group_key,
                    ({group_expr}) IS NULL AS is_unassigned,
                    {display_expr} AS display_name,
                    COALESCE(SUM(s.spend), 0) AS total_spend,
                    COUNT(*) FILTER (WHERE {external_request_sql(alias="s")}) AS request_count,
                    COALESCE(SUM(s.total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(s.{source.prompt_tokens_column}), 0) AS prompt_tokens,
                    COALESCE(SUM(s.{source.completion_tokens_column}), 0) AS completion_tokens
                FROM {source.table} s
                {join_sql}
                {where_sql}
                GROUP BY {group_by_sql}
            ),
            page AS (
                SELECT
                    group_key,
                    is_unassigned,
                    display_name,
                    total_spend,
                    request_count,
                    total_tokens,
                    prompt_tokens,
                    completion_tokens
                FROM grouped
                ORDER BY {sort_expression} DESC, group_key ASC
                LIMIT ${limit_idx}
                OFFSET ${offset_idx}
            ),
            totals AS (
                SELECT COUNT(*) AS total_count FROM grouped
            )
            SELECT
                page.group_key,
                page.is_unassigned,
                page.display_name,
                page.total_spend,
                page.request_count,
                page.total_tokens,
                page.prompt_tokens,
                page.completion_tokens,
                totals.total_count
            FROM totals
            LEFT JOIN page ON TRUE
            ORDER BY page.{sort_expression} DESC NULLS LAST, page.group_key ASC
            """,
            *page_params,
        )
        total = int((rows[0] if rows else {}).get("total_count") or 0)
        # COUNT(*) is always non-null for a real group, including the NULL key
        # group. The totals LEFT JOIN produces a null request_count only when the
        # requested page is empty.
        data_rows = [row for row in rows if row.get("request_count") is not None]
        response = {
            "group_by": group_by,
            "data": [
                to_json_value({k: v for k, v in dict(row).items() if k != "total_count"})
                for row in data_rows
            ],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }
        if group_by == "user":
            response["capabilities"] = {
                "user_identity_labels": user_identity_labels_visible,
            }
        return response

    cache_result = await _load_reporting_response(
        cache=cache,
        cache_key=cache_key,
        cache_ttl=cache_ttl,
        loader=load_grouped_report,
        force_refresh=force_refresh,
    )
    log_admin_query_timing(
        "spend_report_grouped",
        started_at,
        group_by=group_by,
        sort_by=sort_by,
        scope_type=scope_type,
        search=normalized_search,
        limit=limit,
        offset=offset,
        scoped=not scope.is_platform_admin,
        cache_hit=cache_result.cache_hit,
        cache_status=cache_result.status,
        force_refresh=force_refresh,
        cache_ttl_seconds=cache_ttl,
    )
    return {
        **cache_result.value,
        "reporting_context": _reporting_context(request, visibility),
    }


@router.get(
    "/ui/api/spend/feature-status",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def spend_feature_status(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.SPEND_READ,
    )
    visibility = _resolve_reporting_visibility(request, scope, None)
    general_settings = getattr(
        getattr(request.app.state, "app_config", None), "general_settings", None
    )
    return {
        "cache_enabled": bool(getattr(general_settings, "cache_enabled", False)),
        "reporting_api_version": 2 if _reporting_v2_enabled(request) else 1,
        "capabilities": visibility.capabilities(
            user_identity_labels=_user_identity_labels_visible(scope, visibility),
        ),
    }


@router.get(
    "/ui/api/logs",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def request_logs(
    request: Request,
    model: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    view: Literal["organization", "team", "self"] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None, max_length=1024),
    pagination_mode: Literal["offset", "cursor"] = Query(default="offset"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    offset_pagination = pagination_mode == "offset"
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.SPEND_READ,
    )
    visibility = _resolve_reporting_visibility(request, scope, view)
    if not visibility.can_view_request_logs:
        raise HTTPException(status_code=403, detail="Request logs are outside your reporting scope")
    db = db_or_503(request)
    cache = await _reporting_cache(request)
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
    if pagination_mode == "offset" and cursor is not None:
        raise HTTPException(status_code=422, detail="cursor requires pagination_mode=cursor")
    if pagination_mode == "cursor" and "offset" in request.query_params:
        raise HTTPException(status_code=422, detail="offset cannot be used with cursor pagination")
    if cursor:
        cursor_time, cursor_id = _decode_request_log_cursor(cursor)
        params.extend((cursor_time, cursor_id))
        clauses.append(f"(start_time, id) < (${len(params) - 1}::timestamp, ${len(params)})")
    apply_spend_visibility(
        clauses=clauses,
        params=params,
        visibility=visibility,
        source=source,
    )

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    limit_idx = len(params) + 1
    query_limit = limit if offset_pagination else limit + 1
    query_params: tuple[Any, ...] = (*params, query_limit)
    offset_sql = ""
    if offset:
        offset_idx = len(params) + 2
        offset_sql = f"OFFSET ${offset_idx}"
        query_params = (*query_params, offset)

    async def load_logs() -> dict[str, Any]:
        async def query_logs(
            tx: Any,
            deadline: float,
            cancellation_grace: float,
        ) -> dict[str, Any]:
            logs = await _run_reporting_statement(
                tx,
                deadline=deadline,
                cancellation_grace=cancellation_grace,
                query=f"""
                SELECT id, request_id, call_type, model, api_base, api_key, spend, total_tokens,
                       {source.prompt_tokens_column} AS prompt_tokens,
                       {source.completion_tokens_column} AS completion_tokens,
                       {source.cached_prompt_tokens_column} AS prompt_tokens_cached,
                       {source.cached_completion_tokens_column} AS completion_tokens_cached,
                       start_time, end_time, {source.user_column} AS "user", team_id, {source.end_user_column} AS end_user,
                       metadata, cache_hit, cache_key, request_tags, status, http_status_code, error_type
                FROM {source.table}
                {where_sql}
                ORDER BY start_time DESC, id DESC
                LIMIT ${limit_idx}
                {offset_sql}
                """,
                params=query_params,
            )
            total: int | None = None
            if offset_pagination:
                total_rows = await _run_reporting_statement(
                    tx,
                    deadline=deadline,
                    cancellation_grace=cancellation_grace,
                    query=f"SELECT COUNT(*) AS total FROM {source.table} {where_sql}",
                    params=tuple(params),
                )
                total = int((total_rows[0] if total_rows else {}).get("total") or 0)

            has_more = offset + limit < total if total is not None else len(logs) > limit
            page_logs = logs[:limit]
            next_cursor = None
            if has_more and page_logs and not offset_pagination:
                last_row = page_logs[-1]
                next_cursor = _encode_request_log_cursor(
                    last_row.get("start_time"),
                    last_row.get("id"),
                )
            pagination = {
                "limit": limit,
                "offset": offset,
                "count": len(page_logs),
                "has_more": has_more,
                "next_cursor": next_cursor,
                "mode": pagination_mode,
            }
            if total is not None:
                pagination["total"] = total
            return {
                "logs": [to_json_value(dict(row)) for row in page_logs],
                "pagination": pagination,
            }

        return await _run_reporting_transaction(db, cache, query_logs)

    response = await _run_uncached_reporting_response(cache=cache, loader=load_logs)
    log_admin_query_timing(
        "request_logs",
        started_at,
        model=model,
        team_id=team_id,
        user_id=user_id,
        cursor=bool(cursor),
        pagination_mode=pagination_mode,
        limit=limit,
        offset=offset,
        scoped=not scope.is_platform_admin,
    )

    return {
        **response,
        "reporting_context": _reporting_context(request, visibility),
    }
