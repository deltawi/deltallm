from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.metrics import (
    increment_batch_artifact_failure,
    publish_batch_runtime_summary,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchCleanupConfig:
    interval_seconds: float = 86_400.0
    scan_limit: int = 200


class BatchRetentionCleanupWorker:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        storage: BatchArtifactStorage,
        storage_registry: dict[str, BatchArtifactStorage] | None = None,
        config: BatchCleanupConfig,
    ) -> None:
        self.repository = repository
        self.storage = storage
        active_backend = str(getattr(storage, "backend_name", "local") or "local").strip().lower()
        self.storage_registry = {
            str(key).strip().lower(): value
            for key, value in (storage_registry or {}).items()
        }
        self.storage_registry.setdefault(active_backend, storage)
        self.config = config
        self._running = False

    def _storage_for_backend(self, backend: str | None) -> BatchArtifactStorage:
        normalized = str(backend or getattr(self.storage, "backend_name", "local") or "local").strip().lower()
        storage = self.storage_registry.get(normalized)
        if storage is None:
            raise RuntimeError(
                f"Storage backend '{normalized}' is unavailable; keep legacy batch storage configured until referenced files expire"
            )
        return storage

    async def _refresh_batch_runtime_metrics(self, *, now: datetime) -> None:
        try:
            summary = await self.repository.summarize_runtime_statuses(now=now)
            publish_batch_runtime_summary(summary)
        except Exception:
            logger.debug("batch cleanup runtime metrics refresh failed", exc_info=True)
            return

    async def run(self) -> None:
        self._running = True
        while self._running:
            await self.process_once()
            await asyncio.sleep(self.config.interval_seconds)

    def stop(self) -> None:
        self._running = False

    async def process_once(self) -> tuple[int, int]:
        now = datetime.now(tz=UTC)
        deleted_jobs = 0
        deleted_files = 0

        expired_job_ids = await self.repository.list_expired_terminal_job_ids(
            now=now,
            limit=self.config.scan_limit,
        )
        for batch_id in expired_job_ids:
            await self.repository.delete_job_metadata(batch_id)
            deleted_jobs += 1

        expired_files = await self.repository.list_expired_unreferenced_files(
            now=now,
            limit=self.config.scan_limit,
        )
        for file_record in expired_files:
            try:
                await self._storage_for_backend(file_record.storage_backend).delete(file_record.storage_key)
            except Exception as exc:
                increment_batch_artifact_failure(
                    operation="delete",
                    backend=str(file_record.storage_backend or getattr(self.storage, "backend_name", "unknown")),
                )
                logger.warning("batch artifact delete failed file_id=%s error=%s", file_record.file_id, exc)
                continue
            await self.repository.delete_file(file_record.file_id)
            deleted_files += 1

        if deleted_jobs or deleted_files:
            logger.info("batch GC deleted jobs=%s files=%s", deleted_jobs, deleted_files)
        await self._refresh_batch_runtime_metrics(now=now)
        return deleted_jobs, deleted_files
