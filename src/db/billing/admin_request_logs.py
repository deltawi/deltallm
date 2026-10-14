"""SQL for the Admin UI request log list.

The page query reads the spend read view in ``(start_time, id)`` order, applies
the caller's reporting view and the log filters, and stops at the page limit.
Names for teams, organizations, keys and users are looked up only for the rows
on the page, so the lookups never scale with the time window.
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


@dataclass(frozen=True, slots=True)
class RequestLogFilters:
    start: datetime | None = None
    end: datetime | None = None
    usage: UsageFilters = UsageFilters()
    status: RequestLogStatus | None = None
    cache_hit: bool | None = None
    request_id: str | None = None


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
    model_provider_overrides: dict[str, str] | None = None,
    user_identity_labels_visible: bool = False,
) -> RequestLogQueries:
    where = WhereBuilder()
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
    user_email = (
        '(SELECT u.user_email FROM deltallm_usertable u WHERE u.user_id = page."user")'
        if user_identity_labels_visible
        else "NULL::text"
    )
    page_sql = f"""
    SELECT page.*,
           (SELECT t.team_alias FROM deltallm_teamtable t WHERE t.team_id = page.team_id) AS team_alias,
           (SELECT o.organization_name FROM deltallm_organizationtable o
             WHERE o.organization_id = page.organization_id) AS organization_name,
           (SELECT vt.key_name FROM deltallm_verificationtoken vt WHERE vt.token = page.api_key) AS key_name,
           {user_email} AS user_email
    FROM (
        SELECT id, request_id, call_type, model, s.provider, api_base, api_key, spend, total_tokens,
               s.{source.prompt_tokens_column} AS prompt_tokens,
               s.{source.completion_tokens_column} AS completion_tokens,
               s.{source.cached_prompt_tokens_column} AS prompt_tokens_cached,
               s.{source.cached_completion_tokens_column} AS completion_tokens_cached,
               start_time, end_time, latency_ms, {user_column} AS "user", team_id,
               {source.column("organization_column", table_alias="s")} AS organization_id,
               s.{source.end_user_column} AS end_user,
               metadata, cache_hit, cache_key, request_tags, status, http_status_code, error_type
        FROM {source.table} s
        {where_sql}
        ORDER BY start_time DESC, id DESC
        LIMIT {limit_param}
        {offset_sql}
    ) page
    ORDER BY page.start_time DESC, page.id DESC
    """
    return RequestLogQueries(
        page=ReportQuery(sql=page_sql, params=tuple(where.params)),
        count=ReportQuery(
            sql=f"SELECT COUNT(*) AS total FROM {source.table} s{where_sql}",
            params=filter_params,
        ),
    )
