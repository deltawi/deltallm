from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.batch.error_sanitization import sanitize_batch_artifact_error


@dataclass(frozen=True, slots=True)
class BatchErrorRemediationResult:
    inspected: int
    updated: int
    next_after_item_id: str | None
    has_more: bool


class BatchErrorRemediationStore(Protocol):
    async def list_terminal_error_rows(
        self, *, after_item_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    async def update_terminal_error_rows(self, updates: list[dict[str, Any]]) -> int: ...


async def remediate_terminal_batch_item_errors(
    store: BatchErrorRemediationStore,
    *,
    after_item_id: str | None,
    page_size: int,
    max_pages: int,
    apply: bool,
) -> BatchErrorRemediationResult:
    """Inspect or sanitize terminal batch errors in bounded, restartable pages."""

    bounded_page_size = max(1, min(int(page_size), 1_000))
    bounded_max_pages = max(1, min(int(max_pages), 10_000))
    cursor = str(after_item_id or "")
    inspected = 0
    updated = 0
    has_more = False

    for _page in range(bounded_max_pages):
        rows = await store.list_terminal_error_rows(
            after_item_id=cursor,
            limit=bounded_page_size,
        )
        if not rows:
            has_more = False
            break

        updates = [_sanitized_update(dict(row)) for row in rows]
        inspected += len(updates)
        cursor = str(updates[-1]["item_id"])
        has_more = len(updates) == bounded_page_size
        if apply:
            updated += await store.update_terminal_error_rows(updates)
        if not has_more:
            break

    return BatchErrorRemediationResult(
        inspected=inspected,
        updated=updated,
        next_after_item_id=cursor or None,
        has_more=has_more,
    )


def _sanitized_update(row: dict[str, Any]) -> dict[str, Any]:
    retry_category = row.get("retry_category")
    safe_error = sanitize_batch_artifact_error(
        {"retry_category": retry_category} if retry_category is not None else None,
        cancelled=str(row.get("status") or "") == "cancelled",
    )
    return {
        "item_id": str(row["item_id"]),
        "last_error": safe_error["message"] if row.get("has_last_error") else None,
        "error_body": safe_error if row.get("has_error_body") else None,
    }
