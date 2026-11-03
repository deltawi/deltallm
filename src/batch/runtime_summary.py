"""Publish the batch job and item gauges on an interval, off the claim path.

Executor workers used to run these summary queries after every work slice, so
the next claim waited for them and every pod repeated the same global work
once per slice. One loop per worker process now refreshes them on a fixed
interval instead.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.batch.repository import BatchRepository
from src.metrics import publish_batch_runtime_summary

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BatchRuntimeSummaryWorkerConfig:
    refresh_interval_seconds: float = 15.0
    failure_interval_seconds: float = 5.0


class BatchRuntimeSummaryWorker:
    """Refresh the cluster-wide batch runtime gauges on an interval."""

    def __init__(
        self,
        *,
        repository: BatchRepository,
        config: BatchRuntimeSummaryWorkerConfig,
    ) -> None:
        self.repository = repository
        self.config = config
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def process_once(self) -> None:
        summary = await self.repository.summarize_runtime_statuses(now=datetime.now(tz=UTC))
        publish_batch_runtime_summary(summary)

    async def run(self) -> None:
        while not self._stop_event.is_set():
            failed = False
            try:
                await self.process_once()
            except Exception:
                failed = True
                logger.debug("batch runtime summary refresh failed", exc_info=True)
            interval = (
                self.config.failure_interval_seconds
                if failed
                else self.config.refresh_interval_seconds
            )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=max(1.0, float(interval))
                )
            except asyncio.TimeoutError:
                continue
