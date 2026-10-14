"""SQL for the live Admin UI Dashboard: recent hours, recent days, providers and budgets.

Each query reads a short, fixed window of the spend read view (alias ``s``)
and returns aggregates only. The caller's reporting view is applied first.
The budget query reads only the team, organization and key tables.
"""

from __future__ import annotations

from datetime import date, datetime

from src.billing.spend.spend_read import SpendReadSource
from src.db.billing.spend_components import external_request_sql
from src.db.billing.spend_dimensions import canonical_provider_expr
from src.db.billing.usage_explorer_queries import ReportQuery, WhereBuilder
from src.services.reporting.spend_visibility import SpendVisibility, apply_spend_visibility

BUDGET_ALERT_RATIO = 0.8
MAX_BUDGET_ROWS = 5


def _window_where(
    *,
    start: datetime,
    end: datetime,
    visibility: SpendVisibility,
    source: SpendReadSource,
) -> WhereBuilder:
    where = WhereBuilder()
    where.add("s.start_time >= {p}::timestamptz", start)
    where.add("s.start_time < {p}::timestamptz", end)
    apply_spend_visibility(
        clauses=where.clauses,
        params=where.params,
        visibility=visibility,
        source=source,
        table_alias="s",
    )
    return where


def live_hourly_query(
    *,
    start: datetime,
    end: datetime,
    active_keys_since: datetime,
    visibility: SpendVisibility,
    source: SpendReadSource,
) -> ReportQuery:
    """Hourly totals for ``[start, end)`` and the distinct keys seen since ``active_keys_since``."""

    where = _window_where(start=start, end=end, visibility=visibility, source=source)
    external = external_request_sql(alias="s")
    keys_since = where.bind(active_keys_since)
    return ReportQuery(
        sql=f"""
        WITH hourly AS (
            SELECT
                DATE_TRUNC('hour', s.start_time AT TIME ZONE 'UTC') AS bucket,
                COALESCE(SUM(s.spend), 0) AS total_spend,
                COUNT(*) FILTER (WHERE {external}) AS request_count,
                COUNT(*) FILTER (WHERE {external} AND s.status = 'error') AS failed_requests,
                ARRAY_AGG(DISTINCT s.api_key) FILTER (
                    WHERE {external} AND s.start_time >= {keys_since}::timestamptz
                ) AS active_keys
            FROM {source.table} s
            WHERE {where.sql()}
            GROUP BY 1
        )
        SELECT
            bucket,
            total_spend,
            request_count,
            failed_requests,
            (
                SELECT COUNT(DISTINCT key)
                FROM hourly, UNNEST(hourly.active_keys) AS key
            ) AS active_keys
        FROM hourly
        ORDER BY bucket ASC
        """,
        params=tuple(where.params),
    )


def live_daily_query(
    *,
    start: date,
    end: date,
    visibility: SpendVisibility,
    source: SpendReadSource,
) -> ReportQuery:
    """Daily totals for the complete UTC days ``[start, end)``."""

    where = WhereBuilder()
    where.add("s.start_time >= ({p}::date)::timestamp AT TIME ZONE 'UTC'", start.isoformat())
    where.add("s.start_time < ({p}::date)::timestamp AT TIME ZONE 'UTC'", end.isoformat())
    apply_spend_visibility(
        clauses=where.clauses,
        params=where.params,
        visibility=visibility,
        source=source,
        table_alias="s",
    )
    external = external_request_sql(alias="s")
    return ReportQuery(
        sql=f"""
        SELECT
            (s.start_time AT TIME ZONE 'UTC')::date AS bucket,
            COALESCE(SUM(s.spend), 0) AS total_spend,
            COUNT(*) FILTER (WHERE {external}) AS request_count,
            COUNT(*) FILTER (WHERE {external} AND s.status = 'error') AS failed_requests
        FROM {source.table} s
        WHERE {where.sql()}
        GROUP BY 1
        ORDER BY 1 ASC
        """,
        params=tuple(where.params),
    )


def live_providers_query(
    *,
    start: datetime,
    end: datetime,
    visibility: SpendVisibility,
    source: SpendReadSource,
    model_provider_overrides: dict[str, str] | None = None,
) -> ReportQuery:
    """Requests and failures per provider, with the model and error that failed most."""

    where = _window_where(start=start, end=end, visibility=visibility, source=source)
    external = external_request_sql(alias="s")
    provider = canonical_provider_expr(
        table_alias="s", model_provider_overrides=model_provider_overrides
    )
    return ReportQuery(
        sql=f"""
        WITH by_model AS (
            SELECT
                {provider} AS provider,
                s.model,
                COUNT(*) AS request_count,
                COUNT(*) FILTER (WHERE s.status = 'error') AS failed_requests,
                MODE() WITHIN GROUP (ORDER BY COALESCE(s.error_type, s.http_status_code::text))
                    FILTER (WHERE s.status = 'error') AS top_error,
                MIN(s.start_time) FILTER (WHERE s.status = 'error') AS first_error_at,
                MAX(s.start_time) FILTER (WHERE s.status = 'error') AS last_error_at
            FROM {source.table} s
            WHERE {where.sql()} AND {external}
            GROUP BY 1, 2
        )
        SELECT
            provider,
            SUM(request_count) AS request_count,
            SUM(failed_requests) AS failed_requests,
            (ARRAY_AGG(model ORDER BY failed_requests DESC, model ASC)
                FILTER (WHERE failed_requests > 0))[1] AS top_failing_model,
            (ARRAY_AGG(top_error ORDER BY failed_requests DESC, model ASC)
                FILTER (WHERE failed_requests > 0))[1] AS top_error,
            MAX(failed_requests) AS top_failing_model_failures,
            MIN(first_error_at) AS first_error_at,
            MAX(last_error_at) AS last_error_at
        FROM by_model
        GROUP BY provider
        ORDER BY SUM(request_count) DESC, provider ASC
        """,
        params=tuple(where.params),
    )


def live_top_query(
    *,
    start: datetime,
    end: datetime,
    dimensions: tuple[str, ...],
    visibility: SpendVisibility,
    source: SpendReadSource,
) -> ReportQuery:
    """The busiest key, model and team by request share in ``[start, end)``.

    ``dimensions`` must be a subset of ``("api_key", "model", "team")``.
    """

    columns = {"api_key": "s.api_key", "model": "s.model", "team": "s.team_id"}
    selected = [name for name in ("api_key", "model", "team") if name in dimensions]
    where = _window_where(start=start, end=end, visibility=visibility, source=source)
    external = external_request_sql(alias="s")
    sets = ", ".join(f"({columns[name]})" for name in selected)
    group_name = "CASE " + " ".join(
        f"WHEN GROUPING({columns[name]}) = 0 THEN '{name}'" for name in selected
    )
    group_key = "COALESCE(" + ", ".join(columns[name] for name in selected) + ")"
    return ReportQuery(
        sql=f"""
        WITH grouped AS (
            SELECT
                {group_name} END AS dimension,
                {group_key} AS group_key,
                COUNT(*) AS request_count
            FROM {source.table} s
            WHERE {where.sql()} AND {external}
            GROUP BY GROUPING SETS ({sets}, ())
        ),
        ranked AS (
            SELECT
                grouped.*,
                ROW_NUMBER() OVER (
                    PARTITION BY dimension ORDER BY request_count DESC, group_key ASC NULLS LAST
                ) AS rank
            FROM grouped
            WHERE dimension IS NOT NULL AND group_key IS NOT NULL
        )
        SELECT
            ranked.dimension,
            ranked.group_key,
            ranked.request_count,
            totals.request_count AS total_requests,
            CASE ranked.dimension
                WHEN 'api_key' THEN (
                    SELECT vt.key_name FROM deltallm_verificationtoken vt
                    WHERE vt.token = ranked.group_key
                )
                WHEN 'team' THEN (
                    SELECT t.team_alias FROM deltallm_teamtable t
                    WHERE t.team_id = ranked.group_key
                )
            END AS display_name
        FROM ranked
        CROSS JOIN (SELECT request_count FROM grouped WHERE dimension IS NULL) totals
        WHERE ranked.rank = 1
        ORDER BY ranked.dimension
        """,
        params=tuple(where.params),
    )


def _budget_scope(kind: str, visibility: SpendVisibility, where: WhereBuilder) -> str | None:
    """Return the predicate that limits one budget table to the active view, or None."""

    def in_list(column: str, values: tuple[str, ...]) -> str:
        if not values:
            return "FALSE"
        return f"{column} IN ({', '.join(where.bind(value) for value in values)})"

    view = visibility.view
    if view == "platform" and visibility.is_platform_admin:
        return "TRUE"
    if view == "organization":
        if kind == "organization":
            return in_list("b.organization_id", visibility.organization_ids)
        if kind == "team":
            return in_list("b.organization_id", visibility.organization_ids)
        teams = in_list("t.organization_id", visibility.organization_ids)
        return f"b.team_id IN (SELECT t.team_id FROM deltallm_teamtable t WHERE {teams})"
    if view == "team":
        if kind == "organization":
            return None
        return in_list("b.team_id", visibility.team_ids)
    if view == "self" and kind == "api_key" and visibility.owner_account_id:
        # Same boundary as self spend: own keys, in current memberships only.
        owner = f"b.owner_account_id = {where.bind(visibility.owner_account_id)}"
        teams = in_list("b.team_id", visibility.self_team_ids)
        orgs = in_list("t.organization_id", visibility.self_organization_ids)
        return (
            f"{owner} AND ({teams} OR b.team_id IN "
            f"(SELECT t.team_id FROM deltallm_teamtable t WHERE {orgs}))"
        )
    return None


def budget_alerts_query(*, visibility: SpendVisibility) -> ReportQuery | None:
    """Organizations, teams and keys at or above the alert share of their max budget."""

    where = WhereBuilder()
    ratio = where.bind(BUDGET_ALERT_RATIO)
    limit = where.bind(MAX_BUDGET_ROWS)
    tables = {
        "organization": (
            "deltallm_organizationtable",
            "b.organization_id",
            "b.organization_name",
        ),
        "team": ("deltallm_teamtable", "b.team_id", "b.team_alias"),
        "api_key": ("deltallm_verificationtoken", "b.token", "b.key_name"),
    }
    parts: list[str] = []
    for kind, (table, key_column, name_column) in tables.items():
        scope = _budget_scope(kind, visibility, where)
        if scope is None:
            continue
        parts.append(
            f"""
            (SELECT '{kind}' AS kind, {key_column} AS group_key, {name_column} AS display_name,
                    b.spend AS spend, b.max_budget AS max_budget,
                    b.budget_duration AS budget_duration, b.budget_reset_at AS budget_reset_at
             FROM {table} b
             WHERE b.max_budget > 0 AND b.spend >= b.max_budget * {ratio} AND {scope}
             ORDER BY b.spend / b.max_budget DESC, {key_column} ASC
             LIMIT {limit})
            """
        )
    if not parts:
        return None
    return ReportQuery(
        sql=(
            "SELECT * FROM ("
            + " UNION ALL ".join(parts)
            + ") alerts ORDER BY alerts.spend / alerts.max_budget DESC, alerts.group_key ASC"
        ),
        params=tuple(where.params),
    )
