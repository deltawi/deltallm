from __future__ import annotations

import asyncio

import pytest

from src.batch.stale_lease_sweeper import (
    BatchStaleLeaseSweeperConfig,
    BatchStaleLeaseSweeperWorker,
)


class _SweeperRepository:
    def __init__(self, *, result: dict[str, int] | None = None, fail: bool = False) -> None:
        self.result = result or {
            "items": 0,
            "jobs": 0,
            "refreshed_jobs": 0,
            "skipped_active_items": 0,
            "skipped_active_jobs": 0,
        }
        self.fail = fail
        self.sweep_calls: list[dict[str, object]] = []
        self.summary_calls = 0

    async def sweep_expired_batch_leases(self, *, now, page_size: int, max_rows_per_run: int):  # noqa: ANN001
        self.sweep_calls.append(
            {
                "now": now,
                "page_size": page_size,
                "max_rows_per_run": max_rows_per_run,
            }
        )
        if self.fail:
            raise RuntimeError("sweep failed")
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
async def test_stale_lease_sweeper_worker_runs_bounded_pass_and_refreshes_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict[str, object]] = []
    row_counts: list[tuple[str, str, int]] = []
    run_statuses: list[str] = []
    durations: list[float] = []
    monkeypatch.setattr(
        "src.batch.stale_lease_sweeper.publish_batch_runtime_summary",
        lambda summary: published.append(dict(summary)),
    )
    monkeypatch.setattr(
        "src.batch.stale_lease_sweeper.increment_batch_stale_lease_sweeper_rows",
        lambda *, kind, result, count: row_counts.append((kind, result, count)),
    )
    monkeypatch.setattr(
        "src.batch.stale_lease_sweeper.increment_batch_stale_lease_sweeper_run",
        lambda *, status: run_statuses.append(status),
    )
    monkeypatch.setattr(
        "src.batch.stale_lease_sweeper.observe_batch_stale_lease_sweeper_duration",
        lambda *, duration_seconds: durations.append(duration_seconds),
    )
    repository = _SweeperRepository(
        result={
            "items": 2,
            "jobs": 1,
            "refreshed_jobs": 1,
            "skipped_active_items": 3,
            "skipped_active_jobs": 4,
        }
    )
    worker = BatchStaleLeaseSweeperWorker(
        repository=repository,
        config=BatchStaleLeaseSweeperConfig(page_size=10_000, max_rows_per_run=10_000),
    )

    result = await worker.process_once()

    assert result == {
        "items": 2,
        "jobs": 1,
        "refreshed_jobs": 1,
        "skipped_active_items": 3,
        "skipped_active_jobs": 4,
    }
    assert repository.sweep_calls[0]["page_size"] == 1_000
    assert repository.sweep_calls[0]["max_rows_per_run"] == 5_000
    assert repository.summary_calls == 1
    assert run_statuses == ["success"]
    assert row_counts == [
        ("items", "reclaimed", 2),
        ("jobs", "released", 1),
        ("jobs", "refreshed", 1),
        ("items", "skipped_active", 3),
        ("jobs", "skipped_active", 4),
    ]
    assert durations and durations[0] >= 0.0
    assert published


@pytest.mark.asyncio
async def test_stale_lease_sweeper_worker_records_error_without_metrics_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_statuses: list[str] = []
    monkeypatch.setattr(
        "src.batch.stale_lease_sweeper.increment_batch_stale_lease_sweeper_run",
        lambda *, status: run_statuses.append(status),
    )
    repository = _SweeperRepository(fail=True)
    worker = BatchStaleLeaseSweeperWorker(repository=repository)

    with pytest.raises(RuntimeError, match="sweep failed"):
        await worker.process_once()

    assert run_statuses == ["error"]
    assert repository.summary_calls == 0


@pytest.mark.asyncio
async def test_stale_lease_sweeper_worker_stops_cleanly_after_iteration() -> None:
    repository = _SweeperRepository()
    worker = BatchStaleLeaseSweeperWorker(
        repository=repository,
        config=BatchStaleLeaseSweeperConfig(interval_seconds=60.0),
    )

    task = asyncio.create_task(worker.run())
    while not repository.sweep_calls:
        await asyncio.sleep(0)

    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert repository.sweep_calls[0]["page_size"] == 100
