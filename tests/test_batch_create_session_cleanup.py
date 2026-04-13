from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging

import pytest

from src.batch.create.cleanup import BatchCreateSessionCleanupConfig, BatchCreateSessionCleanupWorker
from src.batch.create.models import BatchCreateSessionRecord, BatchCreateSessionStatus


class _SessionRepositoryStub:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC) - timedelta(hours=1)
        self.sessions = [
            BatchCreateSessionRecord(
                session_id="session-1",
                target_batch_id="batch-1",
                status=BatchCreateSessionStatus.FAILED_RETRYABLE,
                endpoint="/v1/embeddings",
                input_file_id="file-1",
                staged_storage_backend="local",
                staged_storage_key="batch-create-stage/session-1.jsonl",
                staged_checksum="checksum-1",
                staged_bytes=128,
                expected_item_count=1,
                inferred_model="m1",
                metadata=None,
                requested_service_tier=None,
                effective_service_tier=None,
                service_tier_source=None,
                scheduling_scope_key=None,
                priority_quota_scope_key=None,
                idempotency_scope_key=None,
                idempotency_key=None,
                last_error_code="timeout",
                last_error_message="timed out",
                promotion_attempt_count=0,
                created_by_api_key="key-1",
                created_by_user_id=None,
                created_by_team_id=None,
                created_by_organization_id=None,
                created_at=now,
                completed_at=None,
                last_attempt_at=now,
                expires_at=now,
            )
        ]
        self.deleted_session_ids: list[str] = []
        self.skip_delete_for_session_ids: set[str] = set()
        self.referenced_stage_keys: set[tuple[str, str]] = set()
        self.summary = {
            "staged": 0,
            "completed": 0,
            "failed_retryable": 1,
            "failed_permanent": 0,
            "expired": 0,
        }

    async def list_cleanup_candidates(
        self,
        *,
        now: datetime,
        completed_before: datetime,
        retryable_before: datetime,
        failed_before: datetime,
        limit: int = 100,
    ):
        del now, completed_before, retryable_before, failed_before, limit
        return list(self.sessions)

    async def delete_cleanup_candidate(self, session: BatchCreateSessionRecord) -> BatchCreateSessionRecord | None:
        if session.session_id in self.skip_delete_for_session_ids:
            return None
        session_id = session.session_id
        self.deleted_session_ids.append(session_id)
        return session

    async def is_stage_artifact_referenced(self, *, storage_backend: str, storage_key: str) -> bool:
        return (storage_backend, storage_key) in self.referenced_stage_keys

    async def summarize_statuses(self) -> dict[str, int]:
        return dict(self.summary)


class _StagingStub:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.raise_on_delete = False
        self.orphan_candidates: list[object] = []

    async def delete(self, artifact) -> None:  # noqa: ANN001
        if self.raise_on_delete:
            raise RuntimeError("delete failed")
        self.deleted.append(str(artifact.storage_key))

    async def list_orphan_candidates(self, *, older_than: datetime, limit: int):  # noqa: ANN001
        del older_than, limit
        return list(self.orphan_candidates)


@pytest.mark.asyncio
async def test_batch_create_session_cleanup_worker_deletes_expired_sessions_and_artifacts() -> None:
    repository = _SessionRepositoryStub()
    staging = _StagingStub()
    worker = BatchCreateSessionCleanupWorker(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
        config=BatchCreateSessionCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    deleted_sessions, deleted_artifacts = await worker.process_once()

    assert deleted_sessions == 1
    assert deleted_artifacts == 1
    assert staging.deleted == ["batch-create-stage/session-1.jsonl"]
    assert repository.deleted_session_ids == ["session-1"]


@pytest.mark.asyncio
async def test_batch_create_session_cleanup_worker_keeps_orphan_for_retry_after_artifact_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _SessionRepositoryStub()
    staging = _StagingStub()
    staging.raise_on_delete = True
    worker = BatchCreateSessionCleanupWorker(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
        config=BatchCreateSessionCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    with caplog.at_level(logging.WARNING):
        deleted_sessions, deleted_artifacts = await worker.process_once()

    assert deleted_sessions == 1
    assert deleted_artifacts == 0
    assert repository.deleted_session_ids == ["session-1"]
    assert "batch create-session cleanup artifact delete failed after row removal" in caplog.text


@pytest.mark.asyncio
async def test_batch_create_session_cleanup_worker_skips_candidate_if_row_changed() -> None:
    repository = _SessionRepositoryStub()
    repository.skip_delete_for_session_ids.add("session-1")
    staging = _StagingStub()
    worker = BatchCreateSessionCleanupWorker(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
        config=BatchCreateSessionCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    deleted_sessions, deleted_artifacts = await worker.process_once()

    assert deleted_sessions == 0
    assert deleted_artifacts == 0
    assert staging.deleted == []
    assert repository.deleted_session_ids == []


@pytest.mark.asyncio
async def test_batch_create_session_cleanup_worker_refresh_metrics_logs_debug_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FailingRepository(_SessionRepositoryStub):
        async def summarize_statuses(self) -> dict[str, int]:
            raise RuntimeError("metrics unavailable")

    worker = BatchCreateSessionCleanupWorker(
        repository=_FailingRepository(),  # type: ignore[arg-type]
        staging=_StagingStub(),  # type: ignore[arg-type]
        config=BatchCreateSessionCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    with caplog.at_level(logging.DEBUG):
        await worker._refresh_create_session_metrics()

    assert "batch create-session cleanup metrics refresh failed" in caplog.text


@pytest.mark.asyncio
async def test_batch_create_session_cleanup_worker_deletes_unreferenced_orphan_artifacts() -> None:
    repository = _SessionRepositoryStub()
    staging = _StagingStub()
    staging.orphan_candidates = [
        type(
            "Artifact",
            (),
            {
                "storage_backend": "local",
                "storage_key": "batch-create-stage/orphan.jsonl",
            },
        )()
    ]
    worker = BatchCreateSessionCleanupWorker(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
        config=BatchCreateSessionCleanupConfig(interval_seconds=0.01, scan_limit=10, orphan_grace_seconds=60),
    )

    deleted_sessions, deleted_artifacts = await worker.process_once()

    assert deleted_sessions == 1
    assert deleted_artifacts == 2
    assert "batch-create-stage/orphan.jsonl" in staging.deleted


@pytest.mark.asyncio
async def test_batch_create_session_cleanup_worker_keeps_referenced_orphan_candidates() -> None:
    repository = _SessionRepositoryStub()
    repository.referenced_stage_keys.add(("local", "batch-create-stage/referenced.jsonl"))
    staging = _StagingStub()
    staging.orphan_candidates = [
        type(
            "Artifact",
            (),
            {
                "storage_backend": "local",
                "storage_key": "batch-create-stage/referenced.jsonl",
            },
        )()
    ]
    worker = BatchCreateSessionCleanupWorker(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
        config=BatchCreateSessionCleanupConfig(interval_seconds=0.01, scan_limit=10, orphan_grace_seconds=60),
    )

    deleted_sessions, deleted_artifacts = await worker.process_once()

    assert deleted_sessions == 1
    assert deleted_artifacts == 1
    assert "batch-create-stage/referenced.jsonl" not in staging.deleted


@pytest.mark.asyncio
async def test_batch_create_session_cleanup_worker_run_survives_iteration_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = BatchCreateSessionCleanupWorker(
        repository=_SessionRepositoryStub(),  # type: ignore[arg-type]
        staging=_StagingStub(),  # type: ignore[arg-type]
        config=BatchCreateSessionCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )
    calls = 0

    async def _process_once() -> tuple[int, int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        worker.stop()
        return 0, 0

    worker.process_once = _process_once  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(worker.run(), timeout=0.2)

    assert calls == 2
    assert "batch create-session cleanup iteration failed" in caplog.text


@pytest.mark.asyncio
async def test_batch_create_session_cleanup_worker_stop_interrupts_idle_wait() -> None:
    worker = BatchCreateSessionCleanupWorker(
        repository=_SessionRepositoryStub(),  # type: ignore[arg-type]
        staging=_StagingStub(),  # type: ignore[arg-type]
        config=BatchCreateSessionCleanupConfig(interval_seconds=60.0, scan_limit=10),
    )

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.02)
    worker.stop()
    await asyncio.wait_for(task, timeout=0.2)
