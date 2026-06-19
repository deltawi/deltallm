from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.cache_invalidation_outbox import (
    CACHE_INVALIDATION_STATUSES,
    CacheInvalidationOutboxRepository,
)


class _FakePrisma:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, sql: str, *params: object) -> list[dict[str, object]]:
        self.calls.append((sql, params))
        return self.rows


def _outbox_row(**overrides: object) -> dict[str, object]:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    row: dict[str, object] = {
        "invalidation_id": "invalidation-1",
        "scope_type": "organization",
        "scope_id": "org-1",
        "reason": "tier_assignment_update",
        "metadata": {"assignment_id": "assignment-1"},
        "status": "pending",
        "attempt_count": 0,
        "max_attempts": 10,
        "next_attempt_at": now,
        "last_error": None,
        "locked_by": None,
        "lease_expires_at": None,
        "created_at": now,
        "updated_at": now,
        "processed_at": None,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_enqueue_uses_explicit_json_and_timestamp_casts() -> None:
    prisma = _FakePrisma(rows=[_outbox_row()])
    repository = CacheInvalidationOutboxRepository(prisma)
    next_attempt_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    record = await repository.enqueue(
        scope_type="organization",
        scope_id="org-1",
        reason="tier_assignment_update",
        metadata={"assignment_id": "assignment-1"},
        next_attempt_at=next_attempt_at,
    )

    sql, params = prisma.calls[0]
    assert record is not None
    assert record.invalidation_id == "invalidation-1"
    assert "$5::jsonb" in sql
    assert "$7::timestamp" in sql
    assert "WHERE status = 'pending'" in sql
    assert "status IN ('pending', 'processing')" not in sql
    assert "::timestamptz" not in sql
    assert params[4] == '{"assignment_id": "assignment-1"}'
    assert params[6] is next_attempt_at


@pytest.mark.asyncio
async def test_mark_retry_uses_timestamp_cast_for_next_attempt() -> None:
    prisma = _FakePrisma(rows=[{"updated_count": 1}])
    repository = CacheInvalidationOutboxRepository(prisma)
    next_attempt_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    updated = await repository.mark_retry(
        "invalidation-1",
        worker_id="worker-1",
        error="redis unavailable",
        next_attempt_at=next_attempt_at,
    )

    sql, params = prisma.calls[0]
    assert updated is True
    assert "SET status = 'superseded'" in sql
    assert "processed_at = NOW()" in sql
    assert "attempt_count" in sql
    assert "ON CONFLICT (scope_type, scope_id, reason)" in sql
    assert "WHERE status = 'pending'" in sql
    assert "DO NOTHING" in sql
    assert "$3::timestamp" in sql
    assert "::timestamptz" not in sql
    assert params[2] is next_attempt_at


@pytest.mark.asyncio
async def test_mark_retry_returns_false_when_processing_row_is_not_owned() -> None:
    prisma = _FakePrisma(rows=[{"updated_count": 0}])
    repository = CacheInvalidationOutboxRepository(prisma)

    updated = await repository.mark_retry(
        "invalidation-1",
        worker_id="worker-2",
        error="redis unavailable",
        next_attempt_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )

    assert updated is False


@pytest.mark.asyncio
async def test_mark_failed_sets_processed_at_for_terminal_status() -> None:
    prisma = _FakePrisma(rows=[_outbox_row(status="failed")])
    repository = CacheInvalidationOutboxRepository(prisma)

    updated = await repository.mark_failed(
        "invalidation-1",
        worker_id="worker-1",
        error="redis unavailable",
    )

    sql, params = prisma.calls[0]
    assert updated is True
    assert "SET status = 'failed'" in sql
    assert "locked_by = NULL" in sql
    assert "lease_expires_at = NULL" in sql
    assert "processed_at = NOW()" in sql
    assert "AND status = 'processing'" in sql
    assert "AND locked_by = $3" in sql
    assert params == ("invalidation-1", "redis unavailable", "worker-1")


def test_cache_invalidation_status_contract_includes_superseded() -> None:
    assert "superseded" in CACHE_INVALIDATION_STATUSES
