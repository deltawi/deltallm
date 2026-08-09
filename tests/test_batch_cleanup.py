from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging

import pytest

from src.batch.cleanup import BatchCleanupConfig, BatchRetentionCleanupWorker
from src.batch.models import (
    BatchFileRecord,
    BatchWebhookDeliveryStatus,
    BatchWebhookOwnershipConflictError,
    BatchWebhookQueueSummary,
)


class _RepoStub:
    def __init__(self) -> None:
        self.deleted_jobs: list[str] = []
        self.deleted_files: list[str] = []
        self.deleted_webhook_cutoffs: list[datetime] = []
        self.webhook_cleanup_limits: list[int] = []
        self.cleanup_job_calls = 0
        self.pending_jobs = ["b-1", "b-2"]
        self.ownership_conflicts = 0
        now = datetime.now(tz=UTC) - timedelta(days=1)
        self.files = [
            BatchFileRecord(
                file_id="f-1",
                purpose="batch_output",
                filename="out.jsonl",
                bytes=10,
                status="processed",
                storage_backend="local",
                storage_key="batch_output/f-1",
                checksum=None,
                created_by_api_key=None,
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=now,
            )
        ]

    async def cleanup_next_expired_terminal_job(self, *, now: datetime) -> bool:
        del now
        self.cleanup_job_calls += 1
        if not self.pending_jobs:
            return False
        self.deleted_jobs.append(self.pending_jobs.pop(0))
        return True

    async def list_expired_unreferenced_files(self, *, now: datetime, limit: int = 100):
        del now, limit
        return list(self.files)

    async def delete_file(self, file_id: str) -> None:
        self.deleted_files.append(file_id)

    async def delete_terminal_webhook_outbox_before(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> dict[str, int]:
        self.deleted_webhook_cutoffs.append(cutoff)
        self.webhook_cleanup_limits.append(limit)
        return {"delivered": 1, "failed": 1}

    async def count_expired_terminal_job_ownership_conflicts(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> int:
        del now, limit
        return self.ownership_conflicts

    async def summarize_webhook_outbox(self) -> BatchWebhookQueueSummary:
        return BatchWebhookQueueSummary(
            counts={status: 0 for status in BatchWebhookDeliveryStatus},
            oldest_pending_age_seconds=0.0,
        )


class _StorageStub:
    backend_name = "local"

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, storage_key: str) -> None:
        self.deleted.append(storage_key)


@pytest.mark.asyncio
async def test_batch_cleanup_worker_deletes_expired_jobs_and_files():
    repo = _RepoStub()
    storage = _StorageStub()
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        config=BatchCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )
    deleted_jobs, deleted_files = await worker.process_once()
    assert deleted_jobs == 2
    assert deleted_files == 1
    assert repo.deleted_jobs == ["b-1", "b-2"]
    assert repo.cleanup_job_calls == 3
    assert repo.deleted_files == ["f-1"]
    assert storage.deleted == ["batch_output/f-1"]


@pytest.mark.asyncio
async def test_batch_cleanup_worker_stops_at_metadata_scan_limit() -> None:
    repo = _RepoStub()
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(scan_limit=1),
    )

    deleted_jobs, deleted_files = await worker.process_once()

    assert (deleted_jobs, deleted_files) == (1, 1)
    assert repo.deleted_jobs == ["b-1"]
    assert repo.cleanup_job_calls == 1


@pytest.mark.asyncio
async def test_batch_cleanup_worker_continues_when_webhook_cleanup_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Repo(_RepoStub):
        async def delete_terminal_webhook_outbox_before(
            self,
            *,
            cutoff: datetime,
            limit: int,
        ) -> dict[str, int]:
            del cutoff, limit
            raise RuntimeError("webhook table unavailable")

    repo = _Repo()
    storage = _StorageStub()
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        config=BatchCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    with caplog.at_level(logging.WARNING, logger="src.batch.cleanup"):
        deleted_jobs, deleted_files = await worker.process_once()

    assert (deleted_jobs, deleted_files) == (2, 1)
    assert repo.deleted_jobs == ["b-1", "b-2"]
    assert repo.deleted_files == ["f-1"]
    assert storage.deleted == ["batch_output/f-1"]
    assert "batch webhook cleanup failed" in caplog.text
    assert "webhook table unavailable" not in caplog.text


@pytest.mark.asyncio
async def test_batch_cleanup_worker_drains_webhooks_in_bounded_pages() -> None:
    class _Repo(_RepoStub):
        def __init__(self) -> None:
            super().__init__()
            self.pages = [
                {"delivered": 2, "failed": 0},
                {"delivered": 1, "failed": 1},
                {"delivered": 0, "failed": 1},
            ]

        async def delete_terminal_webhook_outbox_before(
            self,
            *,
            cutoff: datetime,
            limit: int,
        ) -> dict[str, int]:
            self.deleted_webhook_cutoffs.append(cutoff)
            self.webhook_cleanup_limits.append(limit)
            return self.pages.pop(0)

    repo = _Repo()
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(
            scan_limit=2,
            webhook_cleanup_max_rows_per_run=10,
        ),
    )

    deleted = await worker._cleanup_webhook_deliveries(now=datetime.now(tz=UTC))

    assert deleted == {"delivered": 3, "failed": 2}
    assert repo.webhook_cleanup_limits == [2, 2, 2]


@pytest.mark.asyncio
async def test_batch_cleanup_worker_stops_at_webhook_per_run_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Repo(_RepoStub):
        async def delete_terminal_webhook_outbox_before(
            self,
            *,
            cutoff: datetime,
            limit: int,
        ) -> dict[str, int]:
            self.deleted_webhook_cutoffs.append(cutoff)
            self.webhook_cleanup_limits.append(limit)
            return {"delivered": limit, "failed": 0}

    repo = _Repo()
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(
            scan_limit=2,
            webhook_cleanup_max_rows_per_run=5,
        ),
    )

    with caplog.at_level(logging.INFO, logger="src.batch.cleanup"):
        deleted = await worker._cleanup_webhook_deliveries(now=datetime.now(tz=UTC))

    assert deleted == {"delivered": 5, "failed": 0}
    assert repo.webhook_cleanup_limits == [2, 2, 1]
    assert "batch webhook cleanup reached per-run budget" in caplog.text


@pytest.mark.asyncio
async def test_batch_cleanup_worker_preserves_progress_when_later_webhook_page_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Repo(_RepoStub):
        async def delete_terminal_webhook_outbox_before(
            self,
            *,
            cutoff: datetime,
            limit: int,
        ) -> dict[str, int]:
            self.deleted_webhook_cutoffs.append(cutoff)
            self.webhook_cleanup_limits.append(limit)
            if len(self.webhook_cleanup_limits) > 1:
                raise RuntimeError("private repository failure")
            return {"delivered": limit, "failed": 0}

    repo = _Repo()
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(
            scan_limit=2,
            webhook_cleanup_max_rows_per_run=5,
        ),
    )

    with caplog.at_level(logging.WARNING, logger="src.batch.cleanup"):
        deleted = await worker._cleanup_webhook_deliveries(now=datetime.now(tz=UTC))

    assert deleted == {"delivered": 2, "failed": 0}
    assert repo.webhook_cleanup_limits == [2, 2]
    assert "batch webhook cleanup failed" in caplog.text
    assert "private repository failure" not in caplog.text


@pytest.mark.asyncio
async def test_batch_cleanup_worker_preserves_committed_jobs_when_later_metadata_cleanup_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Repo(_RepoStub):
        async def cleanup_next_expired_terminal_job(self, *, now: datetime) -> bool:
            if self.cleanup_job_calls == 1:
                raise RuntimeError("sensitive database failure")
            return await super().cleanup_next_expired_terminal_job(now=now)

    repo = _Repo()
    storage = _StorageStub()
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        config=BatchCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    with caplog.at_level(logging.WARNING, logger="src.batch.cleanup"):
        deleted_jobs, deleted_files = await worker.process_once()

    assert (deleted_jobs, deleted_files) == (1, 1)
    assert repo.deleted_jobs == ["b-1"]
    assert repo.deleted_files == ["f-1"]
    assert "batch metadata cleanup failed" in caplog.text
    assert "sensitive database failure" not in caplog.text
    record = next(
        item for item in caplog.records if item.message == "batch metadata cleanup failed"
    )
    assert record.committed_jobs == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_batch_cleanup_worker_reports_bounded_ownership_conflict(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Repo(_RepoStub):
        async def cleanup_next_expired_terminal_job(self, *, now: datetime) -> bool:
            if self.cleanup_job_calls == 1:
                raise BatchWebhookOwnershipConflictError(3)
            return await super().cleanup_next_expired_terminal_job(now=now)

    worker = BatchRetentionCleanupWorker(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    with caplog.at_level(logging.WARNING, logger="src.batch.cleanup"):
        deleted_jobs, deleted_files = await worker.process_once()

    assert (deleted_jobs, deleted_files) == (1, 1)
    assert "batch metadata cleanup failed" in caplog.text
    record = next(
        item for item in caplog.records if item.message == "batch metadata cleanup failed"
    )
    assert record.reason == "webhook_ownership_conflict"  # type: ignore[attr-defined]
    assert record.conflict_count == 3  # type: ignore[attr-defined]
    assert record.committed_jobs == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_batch_cleanup_worker_reports_skipped_conflicts_without_losing_progress(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = _RepoStub()
    repo.ownership_conflicts = 3
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(scan_limit=10),
    )

    with caplog.at_level(logging.WARNING, logger="src.batch.cleanup"):
        deleted_jobs, deleted_files = await worker.process_once()

    assert (deleted_jobs, deleted_files) == (2, 1)
    record = next(
        item
        for item in caplog.records
        if item.message == "batch metadata cleanup skipped ownership conflicts"
    )
    assert record.conflict_count == 3  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_batch_cleanup_worker_routes_delete_by_file_backend():
    repo = _RepoStub()
    repo.files[0] = BatchFileRecord(
        file_id="f-2",
        purpose="batch_output",
        filename="out.jsonl",
        bytes=10,
        status="processed",
        storage_backend="local",
        storage_key="batch_output/f-2",
        checksum=None,
        created_by_api_key=None,
        created_by_user_id=None,
        created_by_team_id=None,
        created_at=repo.files[0].created_at,
        expires_at=repo.files[0].expires_at,
    )
    active_storage = _StorageStub()
    active_storage.backend_name = "s3"
    local_storage = _StorageStub()
    worker = BatchRetentionCleanupWorker(
        repository=repo,  # type: ignore[arg-type]
        storage=active_storage,  # type: ignore[arg-type]
        storage_registry={"local": local_storage, "s3": active_storage},  # type: ignore[arg-type]
        config=BatchCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    deleted_jobs, deleted_files = await worker.process_once()

    assert deleted_jobs == 2
    assert deleted_files == 1
    assert local_storage.deleted == ["batch_output/f-2"]
    assert active_storage.deleted == []


@pytest.mark.asyncio
async def test_batch_cleanup_worker_refresh_runtime_metrics_logs_debug_on_failure(caplog: pytest.LogCaptureFixture):
    class _Repo(_RepoStub):
        async def summarize_runtime_statuses(self, *, now: datetime):
            del now
            raise RuntimeError("metrics unavailable")

    worker = BatchRetentionCleanupWorker(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    with caplog.at_level(logging.DEBUG):
        await worker._refresh_batch_runtime_metrics(now=datetime.now(tz=UTC))

    assert "batch cleanup runtime metrics refresh failed" in caplog.text


@pytest.mark.asyncio
async def test_batch_cleanup_worker_refresh_runtime_metrics_logs_debug_on_publish_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    class _Repo(_RepoStub):
        async def summarize_runtime_statuses(self, *, now: datetime):
            del now
            return {"queued": 0, "in_progress": 0, "finalizing": 0}

    worker = BatchRetentionCleanupWorker(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )
    monkeypatch.setattr(
        "src.batch.cleanup.publish_batch_runtime_summary",
        lambda summary: (_ for _ in ()).throw(RuntimeError("publish unavailable")),  # noqa: ARG005
    )

    with caplog.at_level(logging.DEBUG):
        await worker._refresh_batch_runtime_metrics(now=datetime.now(tz=UTC))

    assert "batch cleanup runtime metrics refresh failed" in caplog.text


@pytest.mark.asyncio
async def test_batch_cleanup_worker_run_survives_iteration_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = BatchRetentionCleanupWorker(
        repository=_RepoStub(),  # type: ignore[arg-type]
        storage=_StorageStub(),  # type: ignore[arg-type]
        config=BatchCleanupConfig(interval_seconds=999.0, failure_interval_seconds=0.01, scan_limit=10),
    )
    calls = 0

    async def _process_once() -> tuple[int, int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db unavailable")
        worker.stop()
        return (0, 0)

    worker.process_once = _process_once  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(worker.run(), timeout=0.5)

    assert calls == 2
    assert "batch cleanup iteration failed" in caplog.text
