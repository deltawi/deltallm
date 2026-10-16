"""SQL for the admin Batch jobs list, its status counts and its filter options.

The caller adds the visibility predicate first (see ``append_batch_scope_clause``).
The filters here only narrow that result. Column names are static and every
value is a bound parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

FACET_LIMIT = 50


@dataclass(frozen=True, slots=True)
class BatchListFilters:
    """Optional list filters. Every value is already validated by the HTTP edge."""

    search: str | None = None
    statuses: tuple[str, ...] = ()
    model: str | None = None
    endpoint: str | None = None
    team_id: str | None = None
    created_after: datetime | None = None


@dataclass(frozen=True, slots=True)
class BatchStatusCounts:
    total: int = 0
    queued: int = 0
    in_progress: int = 0
    finalizing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    expired: int = 0

    def to_response(self) -> dict[str, int]:
        # ``in_progress`` keeps its old meaning (in_progress plus finalizing).
        return {
            "total": self.total,
            "queued": self.queued,
            "in_progress": self.in_progress,
            "finalizing": self.finalizing,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "expired": self.expired,
        }


@dataclass(frozen=True, slots=True)
class BatchFacetValue:
    value: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class BatchFacets:
    models: tuple[BatchFacetValue, ...]
    teams: tuple[BatchFacetValue, ...]


def append_batch_list_filters(
    *,
    clauses: list[str],
    params: list[Any],
    filters: BatchListFilters,
    job_alias: str = "",
    include_status: bool = True,
) -> None:
    if filters.search:
        params.append(f"%{filters.search}%")
        placeholder = f"${len(params)}"
        clauses.append(
            f"({job_alias}batch_id ILIKE {placeholder} OR {job_alias}model ILIKE {placeholder})"
        )
    if include_status and filters.statuses:
        placeholders: list[str] = []
        for status in filters.statuses:
            params.append(status)
            placeholders.append(f'${len(params)}::"DeltaLLM_BatchJobStatus"')
        if len(placeholders) == 1:
            clauses.append(f"{job_alias}status = {placeholders[0]}")
        else:
            clauses.append(f"{job_alias}status IN ({', '.join(placeholders)})")
    if filters.model:
        params.append(filters.model)
        clauses.append(f"{job_alias}model = ${len(params)}")
    if filters.endpoint:
        params.append(filters.endpoint)
        clauses.append(f"{job_alias}endpoint = ${len(params)}")
    if filters.team_id:
        params.append(filters.team_id)
        clauses.append(f"{job_alias}created_by_team_id = ${len(params)}")
    if filters.created_after is not None:
        # ``created_at`` is a UTC timestamp without a time zone.
        created_after = filters.created_after
        if created_after.tzinfo is not None:
            created_after = created_after.astimezone(UTC).replace(tzinfo=None)
        params.append(created_after)
        clauses.append(f"{job_alias}created_at >= ${len(params)}::timestamp")


def _where(clauses: list[str]) -> str:
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


async def count_batch_statuses(
    db: Any, *, clauses: list[str], params: list[Any]
) -> BatchStatusCounts:
    rows = await db.query_raw(
        f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'queued') AS queued,
            COUNT(*) FILTER (WHERE status IN ('in_progress', 'finalizing')) AS in_progress,
            COUNT(*) FILTER (WHERE status = 'finalizing') AS finalizing,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
            COUNT(*) FILTER (WHERE status = 'expired') AS expired
        FROM deltallm_batch_job
        {_where(clauses)}
        """,
        *params,
    )
    row = dict(rows[0]) if rows else {}

    def count(key: str) -> int:
        return int(row.get(key) or 0)

    return BatchStatusCounts(
        total=count("total"),
        queued=count("queued"),
        in_progress=count("in_progress"),
        finalizing=count("finalizing"),
        completed=count("completed"),
        failed=count("failed"),
        cancelled=count("cancelled"),
        expired=count("expired"),
    )


async def list_batch_facets(db: Any, *, clauses: list[str], params: list[Any]) -> BatchFacets:
    """Return the most used models and teams, at most ``FACET_LIMIT`` of each."""

    where_sql = _where(clauses)
    model_rows = await db.query_raw(
        f"""
        SELECT j.model AS value, COUNT(*) AS count
        FROM deltallm_batch_job j
        {where_sql}{" AND" if clauses else " WHERE"} j.model IS NOT NULL
        GROUP BY j.model
        ORDER BY count DESC, value ASC
        LIMIT {FACET_LIMIT}
        """,
        *params,
    )
    team_rows = await db.query_raw(
        f"""
        SELECT j.created_by_team_id AS value, MAX(t.team_alias) AS label, COUNT(*) AS count
        FROM deltallm_batch_job j
        LEFT JOIN deltallm_teamtable t ON t.team_id = j.created_by_team_id
        {where_sql}{" AND" if clauses else " WHERE"} j.created_by_team_id IS NOT NULL
        GROUP BY j.created_by_team_id
        ORDER BY count DESC, value ASC
        LIMIT {FACET_LIMIT}
        """,
        *params,
    )

    def facet(row: Any) -> BatchFacetValue:
        data = dict(row)
        value = str(data.get("value") or "")
        return BatchFacetValue(
            value=value,
            label=str(data.get("label") or value),
            count=int(data.get("count") or 0),
        )

    return BatchFacets(
        models=tuple(facet(row) for row in model_rows),
        teams=tuple(facet(row) for row in team_rows),
    )
