"""SQL for the Admin UI usage explorer: totals, split series and ranked breakdowns.

Every query reads one bounded time window of the spend read view (alias ``s``),
applies the caller's reporting view first and the usage filters second, and
returns aggregates only. Identifiers come from static allowlists; values are
bound parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from src.billing.spend.spend_read import SpendReadSource
from src.db.billing.spend_components import external_request_sql
from src.db.billing.spend_dimensions import (
    SpendDimension,
    SpendDimensionSql,
    canonical_provider_expr,
    person_dimension_sql,
    person_filter_sql,
    person_unassigned_sql,
    search_clause_sql,
    search_pattern,
    spend_dimension_sql,
)
from src.services.reporting.spend_visibility import SpendVisibility, apply_spend_visibility
from src.services.reporting.usage_filters import UsageFilters

SeriesInterval = Literal["day", "week", "month"]
BreakdownSort = Literal["spend", "tokens", "requests"]

MAX_SERIES_GROUPS = 8
_BUCKET_EXPRESSIONS: dict[SeriesInterval, str] = {
    "day": "(s.start_time AT TIME ZONE 'UTC')::date",
    "week": "DATE_TRUNC('week', s.start_time AT TIME ZONE 'UTC')::date",
    "month": "DATE_TRUNC('month', s.start_time AT TIME ZONE 'UTC')::date",
}
_SORT_COLUMNS: dict[BreakdownSort, str] = {
    "spend": "total_spend",
    "tokens": "total_tokens",
    "requests": "request_count",
}


@dataclass(frozen=True, slots=True)
class ReportWindow:
    """An inclusive current window and the equal-length window just before it."""

    start: datetime | None
    end: datetime
    previous_start: datetime | None = None

    @property
    def compares(self) -> bool:
        return self.start is not None and self.previous_start is not None


@dataclass(frozen=True, slots=True)
class ReportQuery:
    sql: str
    params: tuple[object, ...]


@dataclass(slots=True)
class WhereBuilder:
    clauses: list[str] = field(default_factory=list)
    params: list[object] = field(default_factory=list)

    def bind(self, value: object) -> str:
        self.params.append(value)
        return f"${len(self.params)}"

    def add(self, template: str, value: object) -> None:
        # Not str.format: column expressions can hold model names with braces.
        self.clauses.append(template.replace("{p}", self.bind(value)))

    def sql(self) -> str:
        return " AND ".join(self.clauses) if self.clauses else "TRUE"


def _filter_column(
    dimension: SpendDimension,
    source: SpendReadSource,
    model_provider_overrides: dict[str, str] | None,
) -> str:
    if dimension == "provider":
        return canonical_provider_expr(
            table_alias="s", model_provider_overrides=model_provider_overrides
        )
    if dimension == "organization":
        return source.column("organization_column", table_alias="s")
    if dimension == "user":
        return source.column("user_column", table_alias="s")
    if dimension == "team":
        return "s.team_id"
    if dimension == "api_key":
        return "s.api_key"
    return "s.model"


def _groups_by_person(dimension: SpendDimension, source: SpendReadSource) -> bool:
    """The usage "user" dimension means the person who owns the key, not the runtime user."""

    return dimension == "user" and source.owner_account_column is not None


def explorer_dimension_sql(
    dimension: SpendDimension,
    source: SpendReadSource,
    *,
    model_provider_overrides: dict[str, str] | None,
    user_identity_labels_visible: bool,
) -> SpendDimensionSql:
    if _groups_by_person(dimension, source):
        return person_dimension_sql(
            source, user_identity_labels_visible=user_identity_labels_visible
        )
    return spend_dimension_sql(
        dimension,
        source,
        model_provider_overrides=model_provider_overrides,
        user_identity_labels_visible=user_identity_labels_visible,
    )


def apply_usage_filters(
    where: WhereBuilder,
    *,
    filters: UsageFilters,
    source: SpendReadSource,
    model_provider_overrides: dict[str, str] | None = None,
) -> None:
    for dimension, value in filters.values:
        if _groups_by_person(dimension, source):
            where.add(person_filter_sql(source), value)
            continue
        column = _filter_column(dimension, source, model_provider_overrides)
        if dimension == "provider":
            where.add(column + " = LOWER({p})", value)
        else:
            where.add(column + " = {p}", value)
    for dimension in sorted(filters.unassigned):
        if _groups_by_person(dimension, source):
            where.clauses.append(person_unassigned_sql(source))
            continue
        where.clauses.append(
            f"{_filter_column(dimension, source, model_provider_overrides)} IS NULL"
        )


def _scoped_where(
    *,
    window: ReportWindow,
    visibility: SpendVisibility,
    filters: UsageFilters,
    source: SpendReadSource,
    model_provider_overrides: dict[str, str] | None,
) -> WhereBuilder:
    where = WhereBuilder()
    lower_bound = window.previous_start if window.compares else window.start
    if lower_bound is not None:
        where.add("s.start_time >= {p}::timestamptz", lower_bound)
    where.add("s.start_time <= {p}::timestamptz", window.end)
    apply_spend_visibility(
        clauses=where.clauses,
        params=where.params,
        visibility=visibility,
        source=source,
        table_alias="s",
    )
    apply_usage_filters(
        where,
        filters=filters,
        source=source,
        model_provider_overrides=model_provider_overrides,
    )
    return where


def _metric_columns(source: SpendReadSource, *, only: str | None, prefix: str = "") -> str:
    """Aggregate columns, optionally restricted to the rows that match ``only``."""

    external = external_request_sql(alias="s")
    narrow = f" AND {only}" if only else ""
    row_filter = f" FILTER (WHERE {only})" if only else ""
    return f"""
        COALESCE(SUM(s.spend){row_filter}, 0) AS {prefix}total_spend,
        COALESCE(SUM(s.total_tokens){row_filter}, 0) AS {prefix}total_tokens,
        COALESCE(SUM(s.{source.prompt_tokens_column}){row_filter}, 0) AS {prefix}prompt_tokens,
        COALESCE(SUM(s.{source.completion_tokens_column}){row_filter}, 0) AS {prefix}completion_tokens,
        COUNT(*) FILTER (WHERE {external}{narrow}) AS {prefix}request_count,
        COUNT(*) FILTER (WHERE {external} AND s.status = 'error'{narrow}) AS {prefix}failed_requests
    """


def usage_summary_query(
    *,
    window: ReportWindow,
    visibility: SpendVisibility,
    filters: UsageFilters,
    source: SpendReadSource,
    model_provider_overrides: dict[str, str] | None = None,
) -> ReportQuery:
    """Totals for the window and, when it has a start, the window just before it."""

    where = _scoped_where(
        window=window,
        visibility=visibility,
        filters=filters,
        source=source,
        model_provider_overrides=model_provider_overrides,
    )
    if not window.compares:
        columns = _metric_columns(source, only=None)
    else:
        current = f"s.start_time >= {where.bind(window.start)}::timestamptz"
        columns = (
            _metric_columns(source, only=current)
            + ","
            + _metric_columns(source, only=f"NOT ({current})", prefix="previous_")
        )
    return ReportQuery(
        sql=f"SELECT {columns} FROM {source.table} s WHERE {where.sql()}",
        params=tuple(where.params),
    )


def usage_series_query(
    *,
    window: ReportWindow,
    interval: SeriesInterval,
    split_by: SpendDimension | None,
    max_groups: int,
    visibility: SpendVisibility,
    filters: UsageFilters,
    source: SpendReadSource,
    model_provider_overrides: dict[str, str] | None = None,
    user_identity_labels_visible: bool = False,
) -> ReportQuery:
    """Buckets for the current window, split into the top groups by spend plus "other".

    Rows are first aggregated per bucket and group, so the ranking step reads at
    most ``buckets x groups`` rows and never re-reads the spend view.
    ``series_index`` is 0 for the merged remainder and 1..N for ranked groups.
    """

    current_window = ReportWindow(start=window.start, end=window.end)
    where = _scoped_where(
        window=current_window,
        visibility=visibility,
        filters=filters,
        source=source,
        model_provider_overrides=model_provider_overrides,
    )
    bucket = _BUCKET_EXPRESSIONS[interval]
    external = external_request_sql(alias="s")
    if split_by is None:
        return ReportQuery(
            sql=f"""
            SELECT
                {bucket} AS bucket,
                1 AS series_index,
                NULL::text AS group_key,
                NULL::text AS display_name,
                FALSE AS is_unassigned,
                COALESCE(SUM(s.spend), 0) AS total_spend,
                COALESCE(SUM(s.total_tokens), 0) AS total_tokens,
                COUNT(*) FILTER (WHERE {external}) AS request_count,
                COUNT(*) FILTER (WHERE {external} AND s.status = 'error') AS failed_requests
            FROM {source.table} s
            WHERE {where.sql()}
            GROUP BY {bucket}
            ORDER BY bucket ASC
            """,
            params=tuple(where.params),
        )

    dimension = explorer_dimension_sql(
        split_by,
        source,
        model_provider_overrides=model_provider_overrides,
        user_identity_labels_visible=user_identity_labels_visible,
    )
    joins = "\n".join(dimension.joins)
    group_limit = where.bind(max(1, min(max_groups, MAX_SERIES_GROUPS)))
    return ReportQuery(
        sql=f"""
        WITH grouped AS (
            SELECT
                {bucket} AS bucket,
                {dimension.group_expr} AS group_key,
                {dimension.display_expr} AS display_name,
                COALESCE(SUM(s.spend), 0) AS total_spend,
                COALESCE(SUM(s.total_tokens), 0) AS total_tokens,
                COUNT(*) FILTER (WHERE {external}) AS request_count,
                COUNT(*) FILTER (WHERE {external} AND s.status = 'error') AS failed_requests
            FROM {source.table} s
            {joins}
            WHERE {where.sql()}
            GROUP BY {bucket}, {", ".join(dimension.group_by_exprs)}
        ),
        ranked AS (
            SELECT
                group_key,
                display_name,
                ROW_NUMBER() OVER (
                    ORDER BY SUM(total_spend) DESC, SUM(request_count) DESC, group_key ASC NULLS LAST
                ) AS rank
            FROM grouped
            GROUP BY group_key, display_name
        )
        SELECT
            g.bucket,
            CASE WHEN r.rank <= {group_limit} THEN r.rank ELSE 0 END AS series_index,
            CASE WHEN r.rank <= {group_limit} THEN g.group_key END AS group_key,
            CASE WHEN r.rank <= {group_limit} THEN g.display_name END AS display_name,
            BOOL_AND(r.rank <= {group_limit} AND g.group_key IS NULL) AS is_unassigned,
            SUM(g.total_spend) AS total_spend,
            SUM(g.total_tokens) AS total_tokens,
            SUM(g.request_count) AS request_count,
            SUM(g.failed_requests) AS failed_requests
        FROM grouped g
        JOIN ranked r
          ON r.group_key IS NOT DISTINCT FROM g.group_key
         AND r.display_name IS NOT DISTINCT FROM g.display_name
        GROUP BY 1, 2, 3, 4
        ORDER BY g.bucket ASC, series_index ASC
        """,
        params=tuple(where.params),
    )


def usage_breakdown_query(
    *,
    window: ReportWindow,
    group_by: SpendDimension,
    sort_by: BreakdownSort,
    search: str | None,
    limit: int,
    offset: int,
    visibility: SpendVisibility,
    filters: UsageFilters,
    source: SpendReadSource,
    model_provider_overrides: dict[str, str] | None = None,
    user_identity_labels_visible: bool = False,
) -> ReportQuery:
    """One page of groups ranked in the current window, with previous-window spend."""

    where = _scoped_where(
        window=window,
        visibility=visibility,
        filters=filters,
        source=source,
        model_provider_overrides=model_provider_overrides,
    )
    dimension = explorer_dimension_sql(
        group_by,
        source,
        model_provider_overrides=model_provider_overrides,
        user_identity_labels_visible=user_identity_labels_visible,
    )
    if search:
        where.params.append(search_pattern(search))
        where.clauses.append(search_clause_sql(dimension.search_clause, len(where.params)))

    if window.compares:
        current = f"s.start_time >= {where.bind(window.start)}::timestamptz"
        current_columns = _metric_columns(source, only=current)
        previous_column = f"COALESCE(SUM(s.spend) FILTER (WHERE NOT ({current})), 0)"
        having = f"HAVING COUNT(*) FILTER (WHERE {current}) > 0"
    else:
        current_columns = _metric_columns(source, only=None)
        previous_column = "NULL::double precision"
        having = ""
    limit_param = where.bind(limit)
    offset_param = where.bind(offset)
    sort_column = _SORT_COLUMNS[sort_by]
    joins = "\n".join(dimension.joins)
    return ReportQuery(
        sql=f"""
        WITH grouped AS (
            SELECT
                {dimension.group_expr} AS group_key,
                ({dimension.group_expr}) IS NULL AS is_unassigned,
                {dimension.display_expr} AS display_name,
                {current_columns},
                {previous_column} AS previous_spend
            FROM {source.table} s
            {joins}
            WHERE {where.sql()}
            GROUP BY {", ".join(dimension.group_by_exprs)}
            {having}
        ),
        totals AS (
            SELECT COUNT(*) AS total_count, COALESCE(SUM(total_spend), 0) AS scope_spend
            FROM grouped
        ),
        page AS (
            SELECT * FROM grouped
            ORDER BY {sort_column} DESC, group_key ASC NULLS LAST
            LIMIT {limit_param} OFFSET {offset_param}
        )
        SELECT page.*, totals.total_count, totals.scope_spend
        FROM totals
        LEFT JOIN page ON TRUE
        ORDER BY page.{sort_column} DESC NULLS LAST, page.group_key ASC NULLS LAST
        """,
        params=tuple(where.params),
    )


ExportInterval = Literal["day", "week", "month"]


def usage_export_query(
    *,
    window: ReportWindow,
    group_by: SpendDimension,
    interval: ExportInterval | None,
    max_rows: int,
    visibility: SpendVisibility,
    filters: UsageFilters,
    source: SpendReadSource,
    model_provider_overrides: dict[str, str] | None = None,
    user_identity_labels_visible: bool = False,
) -> ReportQuery:
    """Every group in the current window, optionally per UTC day, week or month.

    The query returns at most ``max_rows`` rows. The caller asks for one row more
    than it exports, so an extra row tells it that the export is truncated.
    """

    current_window = ReportWindow(start=window.start, end=window.end)
    where = _scoped_where(
        window=current_window,
        visibility=visibility,
        filters=filters,
        source=source,
        model_provider_overrides=model_provider_overrides,
    )
    dimension = explorer_dimension_sql(
        group_by,
        source,
        model_provider_overrides=model_provider_overrides,
        user_identity_labels_visible=user_identity_labels_visible,
    )
    bucket = _BUCKET_EXPRESSIONS[interval] if interval else "NULL::date"
    group_by_exprs = list(dimension.group_by_exprs)
    if interval:
        group_by_exprs.insert(0, bucket)
    limit_param = where.bind(max_rows)
    joins = "\n".join(dimension.joins)
    return ReportQuery(
        sql=f"""
        SELECT
            {bucket} AS bucket,
            {dimension.group_expr} AS group_key,
            ({dimension.group_expr}) IS NULL AS is_unassigned,
            {dimension.display_expr} AS display_name,
            {_metric_columns(source, only=None)}
        FROM {source.table} s
        {joins}
        WHERE {where.sql()}
        GROUP BY {", ".join(group_by_exprs)}
        ORDER BY bucket ASC NULLS FIRST, total_spend DESC, group_key ASC NULLS LAST
        LIMIT {limit_param}
        """,
        params=tuple(where.params),
    )
