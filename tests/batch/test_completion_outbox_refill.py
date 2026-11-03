"""The completion outbox refills delivery slots as rows finish."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.batch.completion_outbox import (
    BatchCompletionOutboxWorker,
    BatchCompletionOutboxWorkerConfig,
)
from src.batch.models import BatchCompletionOutboxRecord


def _record(completion_id: str) -> BatchCompletionOutboxRecord:
    now = datetime.now(tz=UTC)
    return BatchCompletionOutboxRecord(
        completion_id=completion_id,
        batch_id="batch",
        item_id=completion_id,
        payload_json={},
        status="processing",
        attempt_count=1,
        max_attempts=5,
        next_attempt_at=now,
        last_error=None,
        created_at=now,
        updated_at=now,
        processed_at=None,
        locked_by="w1",
        lease_expires_at=now,
    )


class _Outbox:
    def __init__(self, ids: str) -> None:
        self.due = deque(ids)
        self.limits: list[int] = []

    async def claim_completion_outbox_due(self, *, worker_id, lease_seconds, limit):  # noqa: ANN001
        del worker_id, lease_seconds
        self.limits.append(limit)
        claimed = []
        while self.due and len(claimed) < limit:
            claimed.append(_record(self.due.popleft()))
        return claimed


class _Deliveries:
    """Stands in for one row's delivery; a row can be held until released."""

    def __init__(self, *, held: str = "") -> None:
        self.held = set(held)
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []
        self.done: list[str] = []
        self.cancelled: list[str] = []
        self.active = 0
        self.peak = 0

    def release(self, completion_id: str) -> None:
        self.held.discard(completion_id)
        self.gates.setdefault(completion_id, asyncio.Event()).set()

    async def __call__(self, record: BatchCompletionOutboxRecord) -> None:
        name = record.completion_id
        self.started.append(name)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if name in self.held:
                await self.gates.setdefault(name, asyncio.Event()).wait()
            else:
                await asyncio.sleep(0)
            self.done.append(name)
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise
        finally:
            self.active -= 1


def _worker(
    outbox: _Outbox,
    deliveries: _Deliveries,
    *,
    concurrency: int = 2,
    batch_size: int = 50,
    poll: float = 0.01,
) -> BatchCompletionOutboxWorker:
    worker = BatchCompletionOutboxWorker(
        app=SimpleNamespace(state=SimpleNamespace(spend_tracking_service=None)),
        repository=outbox,  # type: ignore[arg-type]
        config=BatchCompletionOutboxWorkerConfig(
            worker_id="w1",
            poll_interval_seconds=poll,
            max_batch_size=batch_size,
            max_concurrency=concurrency,
        ),
    )
    worker._process_record = deliveries  # type: ignore[method-assign]
    return worker


async def _until(condition, *, timeout: float = 1.0) -> None:  # noqa: ANN001
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_a_slow_row_does_not_hold_back_the_next_claim():
    outbox = _Outbox("abcdef")
    deliveries = _Deliveries(held="a")
    worker = _worker(outbox, deliveries, concurrency=2, batch_size=3)
    run = asyncio.create_task(worker.run())

    # "a" stays in flight while every other row is claimed and delivered
    # through the second slot.
    await _until(lambda: sorted(deliveries.done) == list("bcdef"))
    assert "a" not in deliveries.done
    assert deliveries.peak == 2

    deliveries.release("a")
    await _until(lambda: "a" in deliveries.done)
    worker.stop()
    await asyncio.wait_for(run, timeout=1)


@pytest.mark.asyncio
async def test_a_claim_asks_for_free_slots_plus_one_round():
    outbox = _Outbox("abcdefghij")
    deliveries = _Deliveries(held="abcdefghij")
    worker = _worker(outbox, deliveries, concurrency=3, batch_size=50)
    run = asyncio.create_task(worker.run())

    await _until(lambda: len(deliveries.started) == 3)
    # Three free slots plus one round of three; the rest stay in the queue.
    assert outbox.limits == [6]
    assert list(outbox.due) == list("ghij")

    worker.stop()
    for name in "abcdefghij":
        deliveries.release(name)
    await asyncio.wait_for(run, timeout=1)


@pytest.mark.asyncio
async def test_a_claim_never_asks_for_more_than_the_batch_size():
    outbox = _Outbox("abcdef")
    deliveries = _Deliveries()
    worker = _worker(outbox, deliveries, concurrency=4, batch_size=3)
    run = asyncio.create_task(worker.run())

    await _until(lambda: len(deliveries.done) == 6)
    assert set(outbox.limits) == {3}
    worker.stop()
    await asyncio.wait_for(run, timeout=1)


@pytest.mark.asyncio
async def test_an_emptied_queue_waits_the_poll_interval_before_claiming_again():
    outbox = _Outbox("ab")
    deliveries = _Deliveries()
    worker = _worker(outbox, deliveries, concurrency=2, poll=30)
    run = asyncio.create_task(worker.run())

    await _until(lambda: len(deliveries.done) == 2)
    for _ in range(20):
        await asyncio.sleep(0)
    # Finished rows freed both slots, but the claim came back short: no
    # second claim until the poll interval.
    assert len(outbox.limits) == 1

    worker.stop()
    await asyncio.wait_for(run, timeout=1)


@pytest.mark.asyncio
async def test_a_stopped_worker_finishes_what_it_claimed_and_claims_nothing_more():
    outbox = _Outbox("abcdefgh")
    deliveries = _Deliveries(held="ab")
    worker = _worker(outbox, deliveries, concurrency=2, batch_size=4)
    run = asyncio.create_task(worker.run())

    await _until(lambda: deliveries.started == ["a", "b"])
    worker.stop()
    claims = len(outbox.limits)
    deliveries.release("a")
    deliveries.release("b")
    await asyncio.wait_for(run, timeout=1)

    # The two rows waiting for a slot were delivered too; nothing new was claimed.
    assert sorted(deliveries.done) == list("abcd")
    assert len(outbox.limits) == claims
    assert list(outbox.due) == list("efgh")


@pytest.mark.asyncio
async def test_cancelling_the_worker_cancels_rows_in_flight():
    outbox = _Outbox("abc")
    deliveries = _Deliveries(held="abc")
    worker = _worker(outbox, deliveries, concurrency=2)
    run = asyncio.create_task(worker.run())

    await _until(lambda: len(deliveries.started) == 2)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert sorted(deliveries.cancelled) == ["a", "b"]
    assert worker._deliveries == set()


@pytest.mark.asyncio
async def test_one_row_failing_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch):
    failures: list[dict] = []
    monkeypatch.setattr(
        "src.batch.completion_outbox.increment_batch_completion_outbox_failure",
        lambda **kwargs: failures.append(kwargs),
    )
    outbox = _Outbox("abc")
    deliveries = _Deliveries()

    async def _deliver(record: BatchCompletionOutboxRecord) -> None:
        if record.completion_id == "b":
            raise RuntimeError("boom")
        await deliveries(record)

    worker = _worker(outbox, deliveries, concurrency=1)
    worker._process_record = _deliver  # type: ignore[method-assign]
    run = asyncio.create_task(worker.run())

    await _until(lambda: deliveries.done == ["a", "c"])
    assert failures == [{"reason": "record_unhandled"}]
    worker.stop()
    await asyncio.wait_for(run, timeout=1)
