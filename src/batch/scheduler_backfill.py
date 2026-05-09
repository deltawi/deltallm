from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from src.batch.repository import BatchRepository
from src.metrics import (
    increment_batch_scheduler_backfill_rows,
    increment_batch_scheduler_backfill_run,
    observe_batch_scheduler_backfill_duration,
    publish_batch_runtime_summary,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchSchedulerBackfillConfig:
    interval_seconds: float = 60.0
    failure_interval_seconds: float = 30.0
    scan_limit: int = 500


class BatchSchedulerBackfillWorker:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        config: BatchSchedulerBackfillConfig | None = None,
    ) -> None:
        self.repository = repository
        self.config = config or BatchSchedulerBackfillConfig()
        self._running = False
        self._stop_event = asyncio.Event()

    async def _refresh_batch_runtime_metrics(self, *, now: datetime) -> None:
        try:
            summary = await self.repository.summarize_runtime_statuses(now=now)
            publish_batch_runtime_summary(summary)
        except Exception:
            logger.debug("batch scheduler backfill metrics refresh failed", exc_info=True)

    async def process_once(self) -> dict[str, int]:
        started = perf_counter()
        try:
            result = await self.repository.backfill_scheduler_dimensions(
                limit=max(1, min(int(self.config.scan_limit), 5_000)),
            )
        except Exception:
            increment_batch_scheduler_backfill_run(status="error")
            raise

        jobs = max(0, int(result.get("jobs") or 0))
        items = max(0, int(result.get("items") or 0))
        skipped = max(0, int(result.get("skipped") or 0))
        if skipped:
            increment_batch_scheduler_backfill_run(status="skipped_lock")
            observe_batch_scheduler_backfill_duration(duration_seconds=perf_counter() - started)
            return {"jobs": 0, "items": 0, "skipped": skipped}

        increment_batch_scheduler_backfill_run(status="success")
        increment_batch_scheduler_backfill_rows(kind="jobs", count=jobs)
        increment_batch_scheduler_backfill_rows(kind="items", count=items)
        observe_batch_scheduler_backfill_duration(duration_seconds=perf_counter() - started)

        if jobs or items:
            logger.info("batch scheduler backfill repaired jobs=%s items=%s", jobs, items)
        await self._refresh_batch_runtime_metrics(now=datetime.now(tz=UTC))
        return {"jobs": jobs, "items": items}

    async def run(self) -> None:
        self._running = True
        while self._running and not self._stop_event.is_set():
            iteration_failed = False
            try:
                await self.process_once()
            except Exception:
                iteration_failed = True
                logger.exception("batch scheduler backfill iteration failed")
                if not self._running:
                    break
            if not self._running or self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(
                        0.0,
                        float(
                            self.config.failure_interval_seconds
                            if iteration_failed
                            else self.config.interval_seconds
                        ),
                    ),
                )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
