from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from src.batch.repository import BatchRepository
from src.metrics import (
    increment_batch_stale_lease_sweeper_rows,
    increment_batch_stale_lease_sweeper_run,
    observe_batch_stale_lease_sweeper_duration,
    publish_batch_runtime_summary,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchStaleLeaseSweeperConfig:
    interval_seconds: float = 60.0
    failure_interval_seconds: float = 30.0
    page_size: int = 100
    max_rows_per_run: int = 500
    jitter_fraction: float = 0.2


class BatchStaleLeaseSweeperWorker:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        config: BatchStaleLeaseSweeperConfig | None = None,
    ) -> None:
        self.repository = repository
        self.config = config or BatchStaleLeaseSweeperConfig()
        self._running = False
        self._stop_event = asyncio.Event()

    def _next_sleep_seconds(self, *, iteration_failed: bool) -> float:
        base = max(
            0.0,
            float(
                self.config.failure_interval_seconds
                if iteration_failed
                else self.config.interval_seconds
            ),
        )
        if base <= 0:
            return 0.0
        jitter_fraction = max(0.0, min(float(self.config.jitter_fraction or 0.0), 1.0))
        jitter = base * jitter_fraction
        lower = max(0.0, base - jitter)
        upper = base + jitter
        return lower if upper <= lower else random.uniform(lower, upper)

    async def _refresh_batch_runtime_metrics(self, *, now: datetime) -> None:
        try:
            summary = await self.repository.summarize_runtime_statuses(now=now)
            publish_batch_runtime_summary(summary)
        except Exception:
            logger.debug("batch stale lease sweeper metrics refresh failed", exc_info=True)

    async def process_once(self) -> dict[str, int]:
        started = perf_counter()
        now = datetime.now(tz=UTC)
        try:
            result = await self.repository.sweep_expired_batch_leases(
                now=now,
                page_size=max(1, min(int(self.config.page_size or 1), 1_000)),
                max_rows_per_run=max(1, min(int(self.config.max_rows_per_run or 1), 5_000)),
            )
        except Exception:
            increment_batch_stale_lease_sweeper_run(status="error")
            observe_batch_stale_lease_sweeper_duration(duration_seconds=perf_counter() - started)
            raise

        items = max(0, int(result.get("items") or 0))
        jobs = max(0, int(result.get("jobs") or 0))
        refreshed_jobs = max(0, int(result.get("refreshed_jobs") or 0))
        skipped_active_items = max(0, int(result.get("skipped_active_items") or 0))
        skipped_active_jobs = max(0, int(result.get("skipped_active_jobs") or 0))

        increment_batch_stale_lease_sweeper_run(status="success")
        increment_batch_stale_lease_sweeper_rows(kind="items", result="reclaimed", count=items)
        increment_batch_stale_lease_sweeper_rows(kind="jobs", result="released", count=jobs)
        increment_batch_stale_lease_sweeper_rows(
            kind="jobs",
            result="refreshed",
            count=refreshed_jobs,
        )
        increment_batch_stale_lease_sweeper_rows(
            kind="items",
            result="skipped_active",
            count=skipped_active_items,
        )
        increment_batch_stale_lease_sweeper_rows(
            kind="jobs",
            result="skipped_active",
            count=skipped_active_jobs,
        )
        observe_batch_stale_lease_sweeper_duration(duration_seconds=perf_counter() - started)

        if items or jobs:
            logger.info("batch stale lease sweeper reclaimed items=%s released_jobs=%s", items, jobs)
        await self._refresh_batch_runtime_metrics(now=now)
        return {
            "items": items,
            "jobs": jobs,
            "refreshed_jobs": refreshed_jobs,
            "skipped_active_items": skipped_active_items,
            "skipped_active_jobs": skipped_active_jobs,
        }

    async def run(self) -> None:
        self._running = True
        while self._running and not self._stop_event.is_set():
            iteration_failed = False
            try:
                await self.process_once()
            except Exception:
                iteration_failed = True
                logger.exception("batch stale lease sweeper iteration failed")
                if not self._running:
                    break
            if not self._running or self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._next_sleep_seconds(iteration_failed=iteration_failed),
                )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
