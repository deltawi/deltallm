from __future__ import annotations

import asyncio

import pytest

from src.batch.scheduler_backfill import BatchSchedulerBackfillConfig, BatchSchedulerBackfillWorker


class _BackfillRepository:
    def __init__(self, *, result: dict[str, int] | None = None, fail: bool = False) -> None:
        self.result = result or {"jobs": 0, "items": 0}
        self.fail = fail
        self.backfill_limits: list[int] = []
        self.summary_calls = 0

    async def backfill_scheduler_dimensions(self, *, limit: int) -> dict[str, int]:
        self.backfill_limits.append(limit)
        if self.fail:
            raise RuntimeError("backfill failed")
        return dict(self.result)

    async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
        del now
        self.summary_calls += 1
        return {
            "queued": 0,
            "in_progress": 0,
            "finalizing": 0,
            "pending_items": 0,
            "in_progress_items": 0,
            "oldest_pending_item_age_seconds": 0.0,
            "oldest_in_progress_item_age_seconds": 0.0,
        }


@pytest.mark.asyncio
async def test_scheduler_backfill_worker_runs_bounded_pass_and_refreshes_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict[str, object]] = []
    row_counts: list[tuple[str, int]] = []
    run_statuses: list[str] = []
    durations: list[float] = []
    monkeypatch.setattr(
        "src.batch.scheduler_backfill.publish_batch_runtime_summary",
        lambda summary: published.append(dict(summary)),
    )
    monkeypatch.setattr(
        "src.batch.scheduler_backfill.increment_batch_scheduler_backfill_rows",
        lambda *, kind, count: row_counts.append((kind, count)),
    )
    monkeypatch.setattr(
        "src.batch.scheduler_backfill.increment_batch_scheduler_backfill_run",
        lambda *, status: run_statuses.append(status),
    )
    monkeypatch.setattr(
        "src.batch.scheduler_backfill.observe_batch_scheduler_backfill_duration",
        lambda *, duration_seconds: durations.append(duration_seconds),
    )
    repository = _BackfillRepository(result={"jobs": 2, "items": 7})
    worker = BatchSchedulerBackfillWorker(
        repository=repository,
        config=BatchSchedulerBackfillConfig(scan_limit=10_000),
    )

    result = await worker.process_once()

    assert result == {"jobs": 2, "items": 7}
    assert repository.backfill_limits == [5_000]
    assert repository.summary_calls == 1
    assert run_statuses == ["success"]
    assert row_counts == [("jobs", 2), ("items", 7)]
    assert durations and durations[0] >= 0.0
    assert published


@pytest.mark.asyncio
async def test_scheduler_backfill_worker_records_error_without_metrics_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_statuses: list[str] = []
    monkeypatch.setattr(
        "src.batch.scheduler_backfill.increment_batch_scheduler_backfill_run",
        lambda *, status: run_statuses.append(status),
    )
    repository = _BackfillRepository(fail=True)
    worker = BatchSchedulerBackfillWorker(repository=repository)

    with pytest.raises(RuntimeError, match="backfill failed"):
        await worker.process_once()

    assert run_statuses == ["error"]
    assert repository.summary_calls == 0


@pytest.mark.asyncio
async def test_scheduler_backfill_worker_records_lock_skip_without_row_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row_counts: list[tuple[str, int]] = []
    run_statuses: list[str] = []
    durations: list[float] = []
    monkeypatch.setattr(
        "src.batch.scheduler_backfill.increment_batch_scheduler_backfill_rows",
        lambda *, kind, count: row_counts.append((kind, count)),
    )
    monkeypatch.setattr(
        "src.batch.scheduler_backfill.increment_batch_scheduler_backfill_run",
        lambda *, status: run_statuses.append(status),
    )
    monkeypatch.setattr(
        "src.batch.scheduler_backfill.observe_batch_scheduler_backfill_duration",
        lambda *, duration_seconds: durations.append(duration_seconds),
    )
    repository = _BackfillRepository(result={"jobs": 0, "items": 0, "skipped": 1})
    worker = BatchSchedulerBackfillWorker(repository=repository)

    result = await worker.process_once()

    assert result == {"jobs": 0, "items": 0, "skipped": 1}
    assert run_statuses == ["skipped_lock"]
    assert row_counts == []
    assert durations and durations[0] >= 0.0
    assert repository.summary_calls == 0


@pytest.mark.asyncio
async def test_scheduler_backfill_worker_stops_cleanly_after_iteration() -> None:
    repository = _BackfillRepository()
    worker = BatchSchedulerBackfillWorker(
        repository=repository,
        config=BatchSchedulerBackfillConfig(interval_seconds=60.0),
    )

    task = asyncio.create_task(worker.run())
    while not repository.backfill_limits:
        await asyncio.sleep(0)

    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert repository.backfill_limits == [500]
