from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.batch.create.defaults import (
    DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS,
)


@dataclass
class BatchCreateSessionCleanupConfig:
    interval_seconds: float = DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS
    completed_retention_seconds: int = DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS
    retryable_retention_seconds: int = DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS
    failed_retention_seconds: int = DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS


class BatchCreateSessionCleanupWorker:
    """Dark-launch scaffold for future create-session retention work.

    PR 1 intentionally does not delete or mutate any rows. This class exists so
    later PRs can add real session cleanup without rewriting bootstrap seams.
    """

    def __init__(self, *, config: BatchCreateSessionCleanupConfig | None = None) -> None:
        self.config = config or BatchCreateSessionCleanupConfig()
        self._running = True

    async def process_once(self) -> int:
        return 0

    async def run(self) -> None:
        while self._running:
            await self.process_once()
            await asyncio.sleep(max(0.1, float(self.config.interval_seconds)))

    def stop(self) -> None:
        self._running = False
