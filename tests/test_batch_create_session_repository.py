from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.batch.create.models import BatchCreateSessionStatus
from src.batch.create.session_repository import BatchCreateSessionRepository


class _PrismaSpy:
    def __init__(self, rows: list[dict] | None = None, execute_result: int = 1) -> None:
        self.rows = rows or []
        self.execute_result = execute_result
        self.sql = ""
        self.params: tuple[object, ...] = ()
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, sql: str, *params):
        self.sql = sql
        self.params = params
        return self.rows

    async def execute_raw(self, sql: str, *params):
        self.sql = sql
        self.params = params
        self.execute_calls.append((sql, params))
        return self.execute_result


def _session_row(*, status: str, now: datetime) -> dict[str, object]:
    return {
        "session_id": "session-1",
        "target_batch_id": "batch-1",
        "status": status,
        "endpoint": "/v1/embeddings",
        "input_file_id": "file-1",
        "staged_storage_backend": "local",
        "staged_storage_key": "batch-create-stage/session-1.jsonl",
        "staged_checksum": "checksum-1",
        "staged_bytes": 512,
        "expected_item_count": 3,
        "inferred_model": "m1",
        "metadata": {"source": "test"},
        "requested_service_tier": None,
        "effective_service_tier": None,
        "service_tier_source": None,
        "scheduling_scope_key": None,
        "priority_quota_scope_key": None,
        "idempotency_scope_key": None,
        "idempotency_key": None,
        "last_error_code": None,
        "last_error_message": None,
        "promotion_attempt_count": 0,
        "created_by_api_key": "key-1",
        "created_by_user_id": "user-1",
        "created_by_team_id": "team-1",
        "created_by_organization_id": "org-1",
        "created_at": now,
        "completed_at": None,
        "last_attempt_at": None,
        "expires_at": None,
    }


@pytest.mark.asyncio
async def test_mark_session_completed_updates_completed_state() -> None:
    now = datetime.now(tz=UTC)
    prisma = _PrismaSpy(rows=[_session_row(status=BatchCreateSessionStatus.COMPLETED, now=now)])
    repository = BatchCreateSessionRepository(prisma)

    record = await repository.mark_session_completed(
        "session-1",
        completed_at=now,
        expires_at=now,
    )

    assert record is not None
    assert record.status == BatchCreateSessionStatus.COMPLETED
    assert "SET status = $2" in prisma.sql
    assert "completed_at = $3::timestamp" in prisma.sql
    assert "last_error_code = $6" in prisma.sql


@pytest.mark.asyncio
async def test_mark_session_failed_retryable_updates_error_fields() -> None:
    now = datetime.now(tz=UTC)
    prisma = _PrismaSpy(rows=[_session_row(status=BatchCreateSessionStatus.FAILED_RETRYABLE, now=now)])
    repository = BatchCreateSessionRepository(prisma)

    record = await repository.mark_session_failed_retryable(
        "session-1",
        error_code="upstream_timeout",
        error_message="timed out",
        attempted_at=now,
        expires_at=now,
    )

    assert record is not None
    assert record.status == BatchCreateSessionStatus.FAILED_RETRYABLE
    assert "last_attempt_at = $3::timestamp" in prisma.sql
    assert "last_error_code = $5" in prisma.sql
    assert prisma.params[4] == "upstream_timeout"
    assert prisma.params[5] == "timed out"


@pytest.mark.asyncio
async def test_mark_session_expired_sets_expired_status() -> None:
    now = datetime.now(tz=UTC)
    prisma = _PrismaSpy(rows=[_session_row(status=BatchCreateSessionStatus.EXPIRED, now=now)])
    repository = BatchCreateSessionRepository(prisma)

    record = await repository.mark_session_expired("session-1", expired_at=now)

    assert record is not None
    assert record.status == BatchCreateSessionStatus.EXPIRED
    assert "expires_at = $3::timestamp" in prisma.sql


@pytest.mark.asyncio
async def test_summarize_statuses_returns_all_known_statuses() -> None:
    prisma = _PrismaSpy(
        rows=[
            {
                "staged": 1,
                "completed": 2,
                "failed_retryable": 3,
                "failed_permanent": 4,
                "expired": 5,
            }
        ]
    )
    repository = BatchCreateSessionRepository(prisma)

    summary = await repository.summarize_statuses()

    assert summary == {
        "staged": 1,
        "completed": 2,
        "failed_retryable": 3,
        "failed_permanent": 4,
        "expired": 5,
    }


@pytest.mark.asyncio
async def test_delete_session_returns_false_when_no_rows_deleted() -> None:
    prisma = _PrismaSpy(execute_result=0)
    repository = BatchCreateSessionRepository(prisma)

    deleted = await repository.delete_session("session-1")

    assert deleted is False
    assert "DELETE FROM deltallm_batch_create_session" in prisma.sql


@pytest.mark.asyncio
async def test_list_cleanup_candidates_prefers_explicit_expiry_or_status_retention_windows() -> None:
    now = datetime.now(tz=UTC)
    prisma = _PrismaSpy(rows=[_session_row(status=BatchCreateSessionStatus.COMPLETED, now=now)])
    repository = BatchCreateSessionRepository(prisma)

    sessions = await repository.list_cleanup_candidates(
        now=now,
        completed_before=now,
        retryable_before=now,
        failed_before=now,
        limit=25,
    )

    assert len(sessions) == 1
    assert "expires_at IS NULL" in prisma.sql
    assert "status = 'completed'" in prisma.sql
    assert "status = 'failed_retryable'" in prisma.sql
    assert "status = 'failed_permanent'" in prisma.sql
    assert prisma.params[4] == 25


@pytest.mark.asyncio
async def test_is_stage_artifact_referenced_checks_backend_and_key() -> None:
    prisma = _PrismaSpy(rows=[{"?column?": 1}])
    repository = BatchCreateSessionRepository(prisma)

    referenced = await repository.is_stage_artifact_referenced(
        storage_backend="local",
        storage_key="batch-create-stage/session-1.jsonl",
    )

    assert referenced is True
    assert "WHERE staged_storage_backend = $1" in prisma.sql
    assert prisma.params == ("local", "batch-create-stage/session-1.jsonl")


@pytest.mark.asyncio
async def test_delete_cleanup_candidate_requires_snapshot_match() -> None:
    now = datetime.now(tz=UTC)
    prisma = _PrismaSpy(rows=[_session_row(status=BatchCreateSessionStatus.FAILED_RETRYABLE, now=now)])
    repository = BatchCreateSessionRepository(prisma)
    session = await repository.get_session("session-1")

    assert session is not None

    deleted = await repository.delete_cleanup_candidate(session)

    assert deleted is not None
    assert "DELETE FROM deltallm_batch_create_session" in prisma.sql
    assert "expires_at IS NOT DISTINCT FROM $5::timestamp" in prisma.sql
    assert "completed_at IS NOT DISTINCT FROM $6::timestamp" in prisma.sql
    assert "last_attempt_at IS NOT DISTINCT FROM $7::timestamp" in prisma.sql
    assert prisma.params[0] == "session-1"
    assert prisma.params[1] == BatchCreateSessionStatus.FAILED_RETRYABLE


@pytest.mark.asyncio
async def test_session_repository_rejects_invalid_status_from_row() -> None:
    now = datetime.now(tz=UTC)
    prisma = _PrismaSpy(rows=[_session_row(status="broken", now=now)])
    repository = BatchCreateSessionRepository(prisma)

    with pytest.raises(ValueError, match="batch create session status"):
        await repository.get_session("session-1")
