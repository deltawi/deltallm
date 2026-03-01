from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage

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
        config: BatchCleanupConfig,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.config = config
        self._running = False

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
                await self.storage.delete(file_record.storage_key)
            except Exception as exc:
                logger.warning("batch artifact delete failed file_id=%s error=%s", file_record.file_id, exc)
                continue
            await self.repository.delete_file(file_record.file_id)
            deleted_files += 1

        if deleted_jobs or deleted_files:
            logger.info("batch GC deleted jobs=%s files=%s", deleted_jobs, deleted_files)
        return deleted_jobs, deleted_files

