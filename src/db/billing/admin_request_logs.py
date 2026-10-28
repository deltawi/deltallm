"""SQL for the Admin UI request log list.

The page query reads the spend read view in ``(start_time, id)`` order, applies
the caller's reporting view and the log filters, and stops at the page limit.
Names for teams, organizations, keys and people are looked up only for the rows
on the page, so the lookups never scale with the time window.

Sorting by cost or duration reads every matching row of the window to find the
top of the page, so the endpoint allows it only for a bounded window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.billing.spend.spend_read import SpendReadSource
from src.db.billing.usage_explorer_queries import ReportQuery, WhereBuilder, apply_usage_filters
from src.services.reporting.spend_visibility import SpendVisibility, apply_spend_visibility
from src.services.reporting.usage_filters import UsageFilters

RequestLogStatus = Literal["success", "error"]
RequestLogSort = Literal["newest", "cost", "duration"]

# Legacy rows can miss latency_ms; their duration is the stored start to end time.
DURATION_MS_SQL = (
    "COALESCE(s.latency_ms::bigint, "
    "(EXTRACT(EPOCH FROM (s.end_time - s.start_time)) * 1000)::bigint)"
)
_SORT_COLUMNS: dict[RequestLogSort, str] = {
    "cost": "s.spend",
    "duration": DURATION_MS_SQL,
}
_SORT_CASTS: dict[RequestLogSort, str] = {
    "cost": "double precision",
    "duration": "bigint",
}


@dataclass(frozen=True, slots=True)
class RequestLogFilters:
    start: datetime | None = None
    end: datetime | None = None
    usage: UsageFilters = UsageFilters()
    status: RequestLogStatus | None = None
    cache_hit: bool | None = None
    request_id: str | None = None
    tag: str | None = None
    end_user: str | None = None
    call_type: str | None = None
    deployment: str | None = None
    http_status: int | None = None
    error_type: str | None = None
    min_spend: float | None = None
    min_duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RequestLogQueries:
    page: ReportQuery
    count: ReportQuery


def request_log_queries(
    *,
    filters: RequestLogFilters,
    visibility: SpendVisibility,
    source: SpendReadSource,
    limit: int,
    offset: int,
    cursor: tuple[datetime, str] | None,
    sort: RequestLogSort = "newest",
    sort_cursor: tuple[float | int, str] | None = None,
    model_provider_overrides: dict[str, str] | None = None,
    user_identity_labels_visible: bool = False,
) -> RequestLogQueries:
    where = WhereBuilder()
    sort_column = _SORT_COLUMNS.get(sort)
    if sort_cursor is not None and sort_column is not None:
        sort_value, sort_id = sort_cursor
        value_param = where.bind(sort_value)
        id_param = where.bind(sort_id)
        where.clauses.append(
            f"({sort_column}, id) < ({value_param}::{_SORT_CASTS[sort]}, {id_param})"
        )
    if cursor is not None:
        cursor_time, cursor_id = cursor
        where.params.extend((cursor_time, cursor_id))
        where.clauses.append(
            f"(start_time, id) < (${len(where.params) - 1}::timestamptz, ${len(where.params)})"
        )
    if filters.start is not None:
        where.add("start_time >= {p}::timestamptz", filters.start)
    if filters.end is not None:
        where.add("start_time <= {p}::timestamptz", filters.end)
    if filters.status is not None:
        where.add("s.status = {p}", filters.status)
    if filters.cache_hit is not None:
        where.add("s.cache_hit = {p}", filters.cache_hit)
    if filters.request_id is not None:
        where.add("s.request_id = {p}", filters.request_id)
    if filters.tag is not None:
        # Both branches of the spend view have a GIN index on request_tags.
        where.add("s.request_tags @> ARRAY[{p}]::text[]", filters.tag)
    if filters.end_user is not None:
        where.add(f"s.{source.end_user_column} = {{p}}", filters.end_user)
    if filters.call_type is not None:
        where.add("s.call_type = {p}", filters.call_type)
    if filters.deployment is not None:
        where.add("s.deployment_model = {p}", filters.deployment)
    if filters.http_status is not None:
        where.add("s.http_status_code = {p}::int", filters.http_status)
    if filters.error_type is not None:
        where.add("s.error_type = {p}", filters.error_type)
    if filters.min_spend is not None:
        where.add("s.spend >= {p}::double precision", filters.min_spend)
    if filters.min_duration_ms is not None:
        where.add(DURATION_MS_SQL + " >= {p}::bigint", filters.min_duration_ms)
    apply_usage_filters(
        where,
        filters=filters.usage,
        source=source,
        model_provider_overrides=model_provider_overrides,
    )
    apply_spend_visibility(
        clauses=where.clauses,
        params=where.params,
        visibility=visibility,
        source=source,
        table_alias="s",
    )
    filter_params = tuple(where.params)
    where_sql = f" WHERE {where.sql()}" if where.clauses else ""

    limit_param = where.bind(limit)
    offset_sql = f"OFFSET {where.bind(offset)}" if offset else ""
    user_column = source.column("user_column", table_alias="s")
    owner_column = (
        source.column("owner_account_column", table_alias="s")
        if source.owner_account_column
        else "NULL::text"
    )
    # "user" is the person who owns the key, the same key the usage "user" dimension
    # groups by: owner account, then runtime user, then the key's service account.
    user_email = (
        """COALESCE(
               (SELECT pa.email FROM deltallm_platformaccount pa
                 WHERE pa.account_id = COALESCE(page.owner_account_id, page.runtime_user_id)),
               (SELECT u.user_email FROM deltallm_usertable u
                 WHERE u.user_id = page.runtime_user_id AND page.owner_account_id IS NULL),
               (SELECT sa.name || ' (service account)' FROM deltallm_serviceaccount sa
                 WHERE sa.service_account_id = pvt.owner_service_account_id)
           )"""
        if user_identity_labels_visible
        else "NULL::text"
    )
    if sort_column is None:
        inner_order = "ORDER BY start_time DESC, id DESC"
        outer_order = "ORDER BY page.start_time DESC, page.id DESC"
    else:
        inner_order = f"ORDER BY {sort_column} DESC, id DESC"
        outer_order = "ORDER BY page.sort_value DESC, page.id DESC"
    page_sql = f"""
    SELECT page.*,
           COALESCE(page.owner_account_id, page.runtime_user_id, pvt.owner_service_account_id)
             AS "user",
           (SELECT t.team_alias FROM deltallm_teamtable t WHERE t.team_id = page.team_id) AS team_alias,
           (SELECT o.organization_name FROM deltallm_organizationtable o
             WHERE o.organization_id = page.organization_id) AS organization_name,
           (SELECT vt.key_name FROM deltallm_verificationtoken vt WHERE vt.token = page.api_key) AS key_name,
           {user_email} AS user_email
    FROM (
        SELECT id, request_id, call_type, model, s.provider, s.deployment_model, api_base, api_key,
               spend, total_tokens,
               s.{source.prompt_tokens_column} AS prompt_tokens,
               s.{source.completion_tokens_column} AS completion_tokens,
               s.{source.cached_prompt_tokens_column} AS prompt_tokens_cached,
               s.{source.cached_completion_tokens_column} AS completion_tokens_cached,
               start_time, end_time, latency_ms, {DURATION_MS_SQL} AS duration_ms,
               {sort_column or "NULL::bigint"} AS sort_value, {user_column} AS runtime_user_id,
               {owner_column} AS owner_account_id, team_id,
               {source.column("organization_column", table_alias="s")} AS organization_id,
               s.{source.end_user_column} AS end_user,
               metadata, cache_hit, cache_key, request_tags, status, http_status_code, error_type
        FROM {source.table} s
        {where_sql}
        {inner_order}
        LIMIT {limit_param}
        {offset_sql}
    ) page
    LEFT JOIN deltallm_verificationtoken pvt
      ON pvt.token = page.api_key
     AND page.owner_account_id IS NULL
     AND page.runtime_user_id IS NULL
    {outer_order}
    """
    return RequestLogQueries(
        page=ReportQuery(sql=page_sql, params=tuple(where.params)),
        count=ReportQuery(
            sql=f"SELECT COUNT(*) AS total FROM {source.table} s{where_sql}",
            params=filter_params,
        ),
    )
