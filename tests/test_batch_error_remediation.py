from __future__ import annotations

import json

import pytest

from scripts.sanitize_batch_item_errors import _read_database_url
from src.batch.error_remediation import remediate_terminal_batch_item_errors
from src.batch.repositories.error_remediation_repository import BatchErrorRemediationRepository


class _FakeDatabase:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.update_pages: list[list[dict[str, object]]] = []

    async def list_terminal_error_rows(
        self, *, after_item_id: str, limit: int
    ) -> list[dict[str, object]]:
        return [row for row in self.rows if str(row["item_id"]) > after_item_id][:limit]

    async def update_terminal_error_rows(self, updates: list[dict[str, object]]) -> int:
        self.update_pages.append(updates)
        return len(updates)


@pytest.mark.asyncio
async def test_batch_error_remediation_is_bounded_restartable_and_sanitized() -> None:
    sensitive = "api_key=sk-historical-secret https://provider.internal/private"
    db = _FakeDatabase(
        [
            {
                "item_id": "item-1",
                "status": "failed",
                "has_last_error": True,
                "has_error_body": True,
                "retry_category": "upstream_5xx",
                "last_error": sensitive,
                "error_body": {
                    "message": sensitive,
                    "retry_category": "upstream_5xx",
                    "provider_payload": {"secret": sensitive},
                },
            },
            {
                "item_id": "item-2",
                "status": "cancelled",
                "has_last_error": True,
                "has_error_body": True,
                "retry_category": None,
                "last_error": sensitive,
                "error_body": {"message": sensitive},
            },
        ]
    )

    first = await remediate_terminal_batch_item_errors(
        db,
        after_item_id=None,
        page_size=1,
        max_pages=1,
        apply=True,
    )
    second = await remediate_terminal_batch_item_errors(
        db,
        after_item_id=first.next_after_item_id,
        page_size=1,
        max_pages=1,
        apply=True,
    )

    assert first.inspected == first.updated == 1
    assert first.has_more is True
    assert first.next_after_item_id == "item-1"
    assert second.inspected == second.updated == 1
    assert db.update_pages == [
        [
            {
                "item_id": "item-1",
                "last_error": "Provider unavailable",
                "error_body": {
                    "message": "Provider unavailable",
                    "type": "BatchItemError",
                    "retry_category": "upstream_5xx",
                },
            }
        ],
        [
            {
                "item_id": "item-2",
                "last_error": "Batch request cancelled",
                "error_body": {
                    "message": "Batch request cancelled",
                    "type": "BatchItemCancelled",
                },
            }
        ],
    ]
    assert sensitive not in str(db.update_pages)


@pytest.mark.asyncio
async def test_batch_error_remediation_inspect_mode_does_not_write() -> None:
    db = _FakeDatabase(
        [
            {
                "item_id": "item-1",
                "status": "failed",
                "has_last_error": True,
                "has_error_body": False,
                "retry_category": None,
                "last_error": "provider failure",
                "error_body": None,
            }
        ]
    )

    result = await remediate_terminal_batch_item_errors(
        db,
        after_item_id=None,
        page_size=10,
        max_pages=1,
        apply=False,
    )

    assert result.inspected == 1
    assert result.updated == 0
    assert result.has_more is False
    assert db.update_pages == []


class _RecordingPrisma:
    def __init__(self) -> None:
        self.query: tuple[str, tuple[object, ...]] | None = None
        self.execute: tuple[str, tuple[object, ...]] | None = None

    async def query_raw(self, query: str, *args: object) -> list[dict[str, object]]:
        self.query = (query, args)
        return [
            {
                "item_id": "item-2",
                "status": "failed",
                "has_last_error": True,
                "has_error_body": True,
                "retry_category": "upstream_5xx",
            }
        ]

    async def execute_raw(self, query: str, *args: object) -> int:
        self.execute = (query, args)
        return 1


@pytest.mark.asyncio
async def test_error_remediation_repository_bounds_terminal_row_query() -> None:
    prisma = _RecordingPrisma()
    repository = BatchErrorRemediationRepository(prisma)

    rows = await repository.list_terminal_error_rows(after_item_id="item-1", limit=25)

    assert rows == [
        {
            "item_id": "item-2",
            "status": "failed",
            "has_last_error": True,
            "has_error_body": True,
            "retry_category": "upstream_5xx",
        }
    ]
    assert prisma.query is not None
    query, args = prisma.query
    assert "last_error IS NOT NULL AS has_last_error" in query
    assert "LEFT(error_body ->> 'retry_category', 64)" in query
    assert "SELECT item_id, status, last_error, error_body" not in query
    assert "status IN ('completed', 'failed', 'cancelled')" in query
    assert "ORDER BY item_id" in query
    assert "LIMIT $2" in query
    assert args == ("item-1", 25)


@pytest.mark.asyncio
async def test_error_remediation_repository_updates_one_terminal_page() -> None:
    prisma = _RecordingPrisma()
    repository = BatchErrorRemediationRepository(prisma)
    updates = [
        {
            "item_id": "item-1",
            "last_error": "Provider unavailable",
            "error_body": {"message": "Provider unavailable", "type": "BatchItemError"},
        }
    ]

    updated = await repository.update_terminal_error_rows(updates)

    assert updated == 1
    assert prisma.execute is not None
    query, args = prisma.execute
    assert "jsonb_to_recordset($1::jsonb)" in query
    assert "status IN ('completed', 'failed', 'cancelled')" in query
    assert json.loads(str(args[0])) == updates


def test_database_url_file_read_is_bounded(tmp_path) -> None:  # noqa: ANN001
    oversized = tmp_path / "database-url"
    oversized.write_text("x" * 8_193, encoding="utf-8")

    with pytest.raises(ValueError, match="too large"):
        _read_database_url(str(oversized))
