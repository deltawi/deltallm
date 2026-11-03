"""The batch runtime gauges refresh on an interval, never on the claim path."""

from __future__ import annotations

import asyncio
import inspect
import logging
from types import SimpleNamespace

import pytest

from src.batch.runtime_summary import (
    BatchRuntimeSummaryWorker,
    BatchRuntimeSummaryWorkerConfig,
)
from src.batch.worker import BatchExecutorWorker
from src.bootstrap.batch_runtime import workers as batch_workers
from src.bootstrap.batch_runtime.lifecycle import shutdown_batch_runtime
from src.bootstrap.batch_runtime.runtime import BatchRuntime

_SUMMARY = {"queued": 2, "in_progress": 1, "finalizing": 0}


class _Repository:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0
        self.worker: BatchRuntimeSummaryWorker | None = None
        self.stop_after: int | None = None

    async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
        assert now.tzinfo is not None
        self.calls += 1
        if self.stop_after is not None and self.calls >= self.stop_after:
            assert self.worker is not None
            self.worker.stop()
        if self.calls <= self.failures:
            raise RuntimeError("database unavailable")
        return dict(_SUMMARY)


def _summary_worker(repository: _Repository, **config: float) -> BatchRuntimeSummaryWorker:
    worker = BatchRuntimeSummaryWorker(
        repository=repository,  # type: ignore[arg-type]
        config=BatchRuntimeSummaryWorkerConfig(**config),
    )
    repository.worker = worker
    return worker


async def test_one_refresh_publishes_the_summary(monkeypatch: pytest.MonkeyPatch):
    published: list[dict[str, int]] = []
    monkeypatch.setattr(
        "src.batch.runtime_summary.publish_batch_runtime_summary",
        lambda summary: published.append(dict(summary)),
    )
    await _summary_worker(_Repository()).process_once()
    assert published == [_SUMMARY]


async def test_the_loop_waits_its_interval_and_retries_sooner_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(
        "src.batch.runtime_summary.publish_batch_runtime_summary", lambda summary: None
    )
    waits: list[float] = []

    async def _no_wait(awaitable, *, timeout):  # noqa: ANN001
        waits.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr("src.batch.runtime_summary.asyncio.wait_for", _no_wait)
    repository = _Repository(failures=1)
    repository.stop_after = 2
    worker = _summary_worker(
        repository, refresh_interval_seconds=15.0, failure_interval_seconds=5.0
    )

    with caplog.at_level(logging.DEBUG):
        await worker.run()

    assert repository.calls == 2
    # Failed, then refreshed: the next wait follows each outcome.
    assert waits == [5.0, 15.0]
    assert "batch runtime summary refresh failed" in caplog.text


async def test_a_publish_failure_does_not_stop_the_loop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    def _fail(summary):  # noqa: ANN001
        raise RuntimeError("publish unavailable")

    monkeypatch.setattr("src.batch.runtime_summary.publish_batch_runtime_summary", _fail)
    repository = _Repository()
    repository.stop_after = 1
    worker = _summary_worker(repository)

    with caplog.at_level(logging.DEBUG):
        await asyncio.wait_for(worker.run(), 1)

    assert "batch runtime summary refresh failed" in caplog.text


async def test_stop_wakes_the_interval_wait(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "src.batch.runtime_summary.publish_batch_runtime_summary", lambda summary: None
    )
    repository = _Repository()
    worker = _summary_worker(repository, refresh_interval_seconds=300.0)
    task = asyncio.create_task(worker.run())
    while repository.calls == 0:
        await asyncio.sleep(0)
    worker.stop()
    await asyncio.wait_for(task, 1)
    assert repository.calls == 1


def test_the_executor_never_runs_the_runtime_summary_itself():
    # It used to run after every work slice, job pass and finalization, so the
    # next claim waited for three global queries on every pod.
    assert "summarize_runtime_statuses" not in inspect.getsource(BatchExecutorWorker)


@pytest.mark.parametrize(
    ("worker_enabled", "interval", "expected"),
    [(True, 15.0, (15.0, 5.0)), (True, 2.0, (2.0, 2.0)), (False, 15.0, None)],
)
async def test_executor_processes_start_the_summary_loop(
    monkeypatch: pytest.MonkeyPatch,
    worker_enabled: bool,
    interval: float,
    expected: tuple[float, float] | None,
):
    monkeypatch.setattr(
        "src.batch.runtime_summary.publish_batch_runtime_summary", lambda summary: None
    )
    cfg = SimpleNamespace(
        general_settings=SimpleNamespace(
            embeddings_batch_worker_enabled=worker_enabled,
            embeddings_batch_runtime_summary_interval_seconds=interval,
        )
    )
    runtime = BatchRuntime()
    repository = _Repository()

    batch_workers._start_runtime_summary_worker(cfg, repository, runtime)  # type: ignore[arg-type]

    if expected is None:
        assert runtime.runtime_summary_worker is None
        assert runtime.runtime_summary_task is None
        return
    worker = runtime.runtime_summary_worker
    assert worker is not None and runtime.runtime_summary_task is not None
    assert (
        worker.config.refresh_interval_seconds,
        worker.config.failure_interval_seconds,
    ) == expected
    await asyncio.wait_for(shutdown_batch_runtime(runtime), 2)
    assert runtime.runtime_summary_task.done()
