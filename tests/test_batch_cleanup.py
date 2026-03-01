from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.batch.cleanup import BatchCleanupConfig, BatchRetentionCleanupWorker
from src.batch.models import BatchFileRecord


class _RepoStub:
    def __init__(self) -> None:
        self.deleted_jobs: list[str] = []
        self.deleted_files: list[str] = []
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

    async def list_expired_terminal_job_ids(self, *, now: datetime, limit: int = 100):
        del now, limit
        return ["b-1", "b-2"]

    async def delete_job_metadata(self, batch_id: str) -> None:
        self.deleted_jobs.append(batch_id)

    async def list_expired_unreferenced_files(self, *, now: datetime, limit: int = 100):
        del now, limit
        return list(self.files)

    async def delete_file(self, file_id: str) -> None:
        self.deleted_files.append(file_id)


class _StorageStub:
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
    assert repo.deleted_files == ["f-1"]
    assert storage.deleted == ["batch_output/f-1"]

