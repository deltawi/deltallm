"""SQL for batch items on the admin batch detail page.

The caller checks that the principal can read the batch first. Every query is
bounded by one ``batch_id`` and uses the ``(batch_id, status)`` or
``(batch_id, line_number)`` indexes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.batch.models import BatchItemStatus

BATCH_ITEM_STATUS_SET = frozenset(
    {
        BatchItemStatus.PENDING,
        BatchItemStatus.IN_PROGRESS,
        BatchItemStatus.COMPLETED,
        BatchItemStatus.FAILED,
        BatchItemStatus.CANCELLED,
    }
)
ERROR_GROUP_LIMIT = 20
# Raw groups read before they merge into public errors. Many raw codes become one
# public error, so the response limit applies only after the merge.
ERROR_GROUP_SCAN_LIMIT = 1000

_ITEM_COLUMNS = """
    item_id, line_number, custom_id, status, attempts, provider_cost, billed_cost,
    last_error,
    LEFT(error_body ->> 'retry_category', 64) AS _error_retry_category,
    CASE WHEN LENGTH(error_body ->> 'code') <= 64
         THEN error_body ->> 'code' END AS _error_code,
    request_body IS NOT NULL AS has_request_body,
    response_body IS NOT NULL AS has_response_body,
    error_body IS NOT NULL AS has_error_body,
    usage IS NOT NULL AS has_usage
"""


@dataclass(frozen=True, slots=True)
class BatchItemPageQuery:
    batch_id: str
    limit: int
    offset: int = 0
    after_line_number: int | None = None
    status: str | None = None
    custom_id: str | None = None


@dataclass(frozen=True, slots=True)
class BatchItemErrorGroup:
    retry_category: str | None
    code: str | None
    count: int


async def fetch_batch_item_rows(db: Any, query: BatchItemPageQuery) -> list[dict[str, Any]]:
    """Return up to ``limit + 1`` rows, so the caller can tell whether more exist."""

    params: list[Any] = [query.batch_id]
    clauses = ["batch_id = $1"]
    if query.status:
        params.append(query.status)
        clauses.append(f"status = ${len(params)}")
    if query.custom_id:
        params.append(query.custom_id)
        clauses.append(f"custom_id = ${len(params)}")
    if query.after_line_number is not None:
        params.append(query.after_line_number)
        clauses.append(f"line_number > ${len(params)}")
    params.append(query.limit + 1)
    limit_sql = f"LIMIT ${len(params)}"
    if query.after_line_number is None:
        params.append(query.offset)
        limit_sql += f" OFFSET ${len(params)}"
    rows = await db.query_raw(
        f"""
        SELECT {_ITEM_COLUMNS}
        FROM deltallm_batch_item
        WHERE {" AND ".join(clauses)}
        ORDER BY line_number ASC
        {limit_sql}
        """,
        *params,
    )
    return [dict(row) for row in rows]


async def group_batch_item_errors(db: Any, batch_id: str) -> list[BatchItemErrorGroup]:
    """Count failed items by their allowlisted retry category and public code."""

    rows = await db.query_raw(
        f"""
        SELECT LEFT(error_body ->> 'retry_category', 64) AS retry_category,
               CASE WHEN LENGTH(error_body ->> 'code') <= 64
                    THEN error_body ->> 'code' END AS code,
               COUNT(*) AS count
        FROM deltallm_batch_item
        WHERE batch_id = $1 AND status = $2
        GROUP BY 1, 2
        ORDER BY count DESC
        LIMIT {ERROR_GROUP_SCAN_LIMIT}
        """,
        batch_id,
        BatchItemStatus.FAILED,
    )
    groups: list[BatchItemErrorGroup] = []
    for row in rows:
        data = dict(row)
        groups.append(
            BatchItemErrorGroup(
                retry_category=data.get("retry_category"),
                code=data.get("code"),
                count=int(data.get("count") or 0),
            )
        )
    return groups
