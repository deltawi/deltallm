"""Request log reads fixed to one external workspace (organization and team).

Every statement starts with the organization and team predicates. Callers
cannot remove or widen them, and no filter here accepts another tenant ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.billing.spend.spend_read import SpendReadSource
from src.db.runtime.reporting import ReportingTransaction, _run_reporting_statement


@dataclass(frozen=True, slots=True)
class WorkspaceLogScope:
    organization_id: str
    team_id: str


@dataclass(frozen=True, slots=True)
class WorkspaceLogFilters:
    start: datetime
    end: datetime
    limit: int
    model: str | None = None
    api_key: str | None = None
    status: Literal["success", "error"] | None = None
    after: tuple[datetime, str] | None = None


def _projection(source: SpendReadSource) -> str:
    # Keep this list equal to WorkspaceRequestLog. Do not add metadata,
    # api_base, cache_key, end-user, owner, tag or provider-body columns.
    return f"""
        id, request_id, start_time, end_time, latency_ms, call_type, model, api_key,
        status, http_status_code, error_type,
        {source.prompt_tokens_column} AS prompt_tokens,
        {source.completion_tokens_column} AS completion_tokens,
        {source.cached_prompt_tokens_column} AS prompt_tokens_cached,
        {source.cached_completion_tokens_column} AS completion_tokens_cached,
        spend, cache_hit
    """


def _scope_clauses(
    source: SpendReadSource, scope: WorkspaceLogScope
) -> tuple[list[str], list[object]]:
    if not scope.organization_id or not scope.team_id or source.organization_column is None:
        raise ValueError("A workspace log read requires an organization and a team")
    return (
        [f"{source.organization_column} = $1", "team_id = $2"],
        [scope.organization_id, scope.team_id],
    )


async def list_workspace_request_logs(
    tx: ReportingTransaction,
    *,
    deadline: float,
    cancellation_grace: float,
    source: SpendReadSource,
    scope: WorkspaceLogScope,
    filters: WorkspaceLogFilters,
) -> list[dict[str, object]]:
    """Return up to ``filters.limit + 1`` rows, newest first, to detect a next page."""

    clauses, params = _scope_clauses(source, scope)

    def add(template: str, value: object) -> None:
        params.append(value)
        clauses.append(template.format(i=len(params)))

    add("start_time >= ${i}::timestamptz", filters.start)
    add("start_time < ${i}::timestamptz", filters.end)
    if filters.model is not None:
        add("model = ${i}", filters.model)
    if filters.api_key is not None:
        add("api_key = ${i}", filters.api_key)
    if filters.status is not None:
        add("status = ${i}", filters.status)
    if filters.after is not None:
        params.extend(filters.after)
        clauses.append(f"(start_time, id) < (${len(params) - 1}::timestamptz, ${len(params)})")
    params.append(filters.limit + 1)
    return await _run_reporting_statement(
        tx,
        deadline=deadline,
        cancellation_grace=cancellation_grace,
        query=f"""
        SELECT {_projection(source)}
        FROM {source.table}
        WHERE {" AND ".join(clauses)}
        ORDER BY start_time DESC, id DESC
        LIMIT ${len(params)}
        """,
        params=tuple(params),
    )


async def get_workspace_request_log(
    tx: ReportingTransaction,
    *,
    deadline: float,
    cancellation_grace: float,
    source: SpendReadSource,
    scope: WorkspaceLogScope,
    log_id: str,
) -> dict[str, object] | None:
    clauses, params = _scope_clauses(source, scope)
    params.append(log_id)
    clauses.append(f"id = ${len(params)}")
    rows = await _run_reporting_statement(
        tx,
        deadline=deadline,
        cancellation_grace=cancellation_grace,
        query=f"""
        SELECT {_projection(source)}
        FROM {source.table}
        WHERE {" AND ".join(clauses)}
        LIMIT 1
        """,
        params=tuple(params),
    )
    return rows[0] if rows else None
