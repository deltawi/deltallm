"""The worker claims its next slice while the previous slice's tail runs."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.batch.models import BatchJobStatus, BatchWorkClaim
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig

_ENDPOINT = "/v1/responses"


class _Storage:
    async def write_lines(self, **kwargs):  # pragma: no cover
        raise AssertionError(f"unexpected artifact write: {kwargs}")


class _PoolRepository:
    def __init__(self, slices: list[tuple[str, list[str]]]) -> None:
        self.pending = deque(slices)
        self.claimed: list[str] = []
        self.released: list[tuple[str, ...]] = []
        self.refreshed: list[str] = []
        self.finalizing: set[str] = set()
        self.finalizable: deque[str] = deque()
        self.job_leases_released: list[str] = []

    async def claim_next_finalization(self, **kwargs):
        del kwargs
        if not self.finalizable:
            return None
        return SimpleNamespace(batch_id=self.finalizable.popleft())

    async def renew_job_lease(self, **kwargs) -> bool:
        del kwargs
        return True

    async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
        del worker_id
        self.job_leases_released.append(batch_id)

    async def claim_next_work(self, **kwargs):
        del kwargs
        if not self.pending:
            return None
        batch_id, item_ids = self.pending.popleft()
        self.claimed.append(batch_id)
        return BatchWorkClaim(
            claim_id=f"claim-{batch_id}",
            worker_id="w1",
            batch_id=batch_id,
            endpoint=_ENDPOINT,
            model_group="m-1",
            tenant_scope_type="api_key",
            tenant_scope_id="tok-1",
            service_tier="standard",
            item_ids=list(item_ids),
            claimed_work_units=len(item_ids),
            lease_expires_at=datetime.now(tz=UTC) + timedelta(seconds=60),
        )

    async def get_job(self, batch_id: str):
        return SimpleNamespace(
            batch_id=batch_id, endpoint=_ENDPOINT, cancel_requested_at=None, model="m-1"
        )

    async def load_claim_items(self, item_ids: list[str]):
        return [
            SimpleNamespace(item_id=item_id, batch_id=item_id.split(":")[0], request_body={})
            for item_id in item_ids
        ]

    async def release_claim_items(self, *, item_ids: list[str], worker_id: str) -> int:
        del worker_id
        self.released.append(tuple(item_ids))
        return 0

    async def refresh_jobs_after_claim(self, batch_ids: list[str]):
        self.refreshed.extend(batch_ids)
        done = [batch_id for batch_id in batch_ids if batch_id in self.finalizing]
        self.finalizable.extend(done)
        return [SimpleNamespace(batch_id=b, status=BatchJobStatus.FINALIZING) for b in done]

    async def summarize_runtime_statuses(self, *, now):
        del now
        return {}


class _Items:
    """Fake provider work: each item runs until the test releases it."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.active = 0
        self.peak = 0
        self._release: dict[str, asyncio.Event] = {}
        self._changed = asyncio.Event()

    def release(self, *item_ids: str) -> None:
        for item_id in item_ids:
            self._release.setdefault(item_id, asyncio.Event()).set()

    async def until(self, predicate, timeout: float = 2.0) -> None:
        async def _wait() -> None:
            while not predicate():
                event = self._changed
                await event.wait()

        await asyncio.wait_for(_wait(), timeout)

    def _notify(self) -> None:
        event, self._changed = self._changed, asyncio.Event()
        event.set()

    async def process(self, job, item) -> None:
        del job
        self.started.append(item.item_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        self._notify()
        try:
            await self._release.setdefault(item.item_id, asyncio.Event()).wait()
        finally:
            self.active -= 1
            self.finished.append(item.item_id)
            self._notify()


def _worker(repository: _PoolRepository, *, concurrency: int, max_slices: int):
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repository,  # type: ignore[arg-type]
        storage=_Storage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            worker_concurrency=concurrency,
            max_slices_in_flight=max_slices,
            poll_interval_seconds=0.01,
        ),
    )
    items = _Items()
    worker._process_item = items.process  # type: ignore[assignment]
    return worker, items


async def _stop(worker: BatchExecutorWorker, task: asyncio.Task[None]) -> None:
    worker.stop()
    await asyncio.wait_for(task, 2)


async def test_next_slice_fills_the_previous_slice_tail():
    repository = _PoolRepository([("a", ["a:1", "a:2", "a:3"]), ("b", ["b:1"])])
    worker, items = _worker(repository, concurrency=2, max_slices=2)
    task = asyncio.create_task(worker.run())
    try:
        await items.until(lambda: items.started == ["a:1", "a:2"])
        # a:3 still waits for a slot, so the worker must not claim yet.
        await asyncio.sleep(0.05)
        assert repository.claimed == ["a"]

        items.release("a:1")
        await items.until(lambda: "a:3" in items.started)
        # Every item of "a" has started and no slot is idle: still no claim.
        await asyncio.sleep(0.05)
        assert repository.claimed == ["a"]

        items.release("a:2")
        # One idle slot while a:3 runs: the next slice takes it.
        await items.until(lambda: "b:1" in items.started)
        assert repository.claimed == ["a", "b"]
        assert "a:3" not in items.finished
        assert items.peak == 2

        items.release("a:3", "b:1")
        await items.until(lambda: len(items.finished) == 4)
    finally:
        items.release("a:1", "a:2", "a:3", "b:1")
        await _stop(worker, task)
    assert sorted(repository.refreshed) == ["a", "b"]
    assert worker._execution_slots.open_slices == 0
    assert worker._execution_slots.active == 0


async def test_one_slice_in_flight_keeps_the_old_barrier():
    repository = _PoolRepository([("a", ["a:1", "a:2"]), ("b", ["b:1"])])
    worker, items = _worker(repository, concurrency=2, max_slices=1)
    task = asyncio.create_task(worker.run())
    try:
        await items.until(lambda: items.started == ["a:1", "a:2"])
        items.release("a:1")
        await items.until(lambda: "a:1" in items.finished)
        await asyncio.sleep(0.05)
        assert repository.claimed == ["a"]
        assert "b:1" not in items.started

        items.release("a:2")
        await items.until(lambda: "b:1" in items.started)
        items.release("b:1")
        await items.until(lambda: len(items.finished) == 3)
    finally:
        items.release("a:1", "a:2", "b:1")
        await _stop(worker, task)


async def test_slices_in_flight_are_capped_and_share_the_slots():
    repository = _PoolRepository(
        [("a", ["a:1", "a:2"]), ("b", ["b:1"]), ("c", ["c:1"]), ("d", ["d:1"])]
    )
    worker, items = _worker(repository, concurrency=4, max_slices=2)
    task = asyncio.create_task(worker.run())
    try:
        await items.until(lambda: {"a:1", "a:2", "b:1"} <= set(items.started))
        # Two slices are open and two slots are idle, but the cap is two slices.
        await asyncio.sleep(0.05)
        assert repository.claimed == ["a", "b"]

        items.release("b:1")
        await items.until(lambda: "c:1" in items.started)
        assert repository.claimed == ["a", "b", "c"]
        assert items.peak <= 4

        items.release("a:1", "a:2", "c:1")
        await items.until(lambda: "d:1" in items.started)
        items.release("d:1")
        await items.until(lambda: len(items.finished) == 5)
    finally:
        items.release("a:1", "a:2", "b:1", "c:1", "d:1")
        await _stop(worker, task)


async def test_stop_finishes_the_claimed_slices_before_returning():
    repository = _PoolRepository([("a", ["a:1", "a:2"]), ("b", ["b:1"])])
    worker, items = _worker(repository, concurrency=2, max_slices=2)
    task = asyncio.create_task(worker.run())
    try:
        await items.until(lambda: items.started == ["a:1", "a:2"])
        items.release("a:1")
        await items.until(lambda: "b:1" in items.started)

        worker.stop()
        await asyncio.sleep(0.05)
        assert not task.done()
        assert repository.released == []

        items.release("a:2", "b:1")
        await asyncio.wait_for(task, 2)
    finally:
        items.release("a:1", "a:2", "b:1")
        if not task.done():
            await _stop(worker, task)
    assert sorted(repository.released) == [("a:1", "a:2"), ("b:1",)]
    assert sorted(items.finished) == ["a:1", "a:2", "b:1"]


async def test_cancelling_the_worker_cancels_and_releases_every_slice():
    repository = _PoolRepository([("a", ["a:1", "a:2"]), ("b", ["b:1"])])
    worker, items = _worker(repository, concurrency=2, max_slices=2)
    task = asyncio.create_task(worker.run())
    await items.until(lambda: items.started == ["a:1", "a:2"])
    items.release("a:1")
    await items.until(lambda: "b:1" in items.started)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert worker._pool_tasks == {}
    assert sorted(repository.released) == [("a:1", "a:2"), ("b:1",)]
    assert worker._execution_slots.active == 0
    assert worker._execution_slots.open_slices == 0


async def test_a_failed_slice_does_not_stop_the_worker():
    repository = _PoolRepository([("a", ["a:1"]), ("b", ["b:1"])])
    worker, items = _worker(repository, concurrency=2, max_slices=2)
    original_get_job = repository.get_job

    async def get_job(batch_id: str):
        if batch_id == "a":
            raise RuntimeError("database went away")
        return await original_get_job(batch_id)

    repository.get_job = get_job  # type: ignore[method-assign]
    task = asyncio.create_task(worker.run())
    try:
        await items.until(lambda: "b:1" in items.started)
        items.release("b:1")
        await items.until(lambda: items.finished == ["b:1"])
    finally:
        items.release("b:1")
        await _stop(worker, task)
    assert ("a:1",) in repository.released
    assert worker._execution_slots.open_slices == 0


async def test_a_slice_cancelled_before_it_starts_still_releases_its_claim():
    repository = _PoolRepository([("a", ["a:1", "a:2"])])
    worker, items = _worker(repository, concurrency=2, max_slices=2)

    assert await worker._fill_slice_pool() is True
    assert repository.claimed == ["a"]
    assert worker._execution_slots.open_slices == 1
    # Cancel before the pooled task has run a single step.
    await worker._cancel_pool_tasks()

    assert items.started == []
    assert repository.released == [("a:1", "a:2")]
    assert worker._pool_tasks == {}
    assert worker._execution_slots.open_slices == 0


async def test_finalization_runs_beside_the_slices():
    repository = _PoolRepository([("a", ["a:1"]), ("b", ["b:1"]), ("c", ["c:1"])])
    repository.finalizing.add("a")
    worker, items = _worker(repository, concurrency=2, max_slices=2)
    finalizing = asyncio.Event()
    finish = asyncio.Event()
    finalized: list[str] = []

    async def finalize(job) -> None:
        finalizing.set()
        await finish.wait()
        finalized.append(job.batch_id)

    worker._finalize_with_retry = finalize  # type: ignore[method-assign]
    task = asyncio.create_task(worker.run())
    try:
        await items.until(lambda: {"a:1", "b:1"} <= set(items.started))
        items.release("a:1")
        await asyncio.wait_for(finalizing.wait(), 2)
        # The finalization of "a" holds no slot and no slice place: "c" still runs.
        await items.until(lambda: "c:1" in items.started)
        assert finalized == []

        finish.set()
        items.release("b:1", "c:1")
        await items.until(lambda: len(items.finished) == 3)
    finally:
        finish.set()
        items.release("a:1", "b:1", "c:1")
        await _stop(worker, task)
    assert finalized == ["a"]
    assert repository.job_leases_released == ["a"]
