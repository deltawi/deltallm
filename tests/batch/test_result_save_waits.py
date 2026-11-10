"""A result save waits out a busy database only under a known lease, and stops on drain."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.batch import result_save_waits
from src.batch.result_save_waits import CLOCK_SKEW_SECONDS, BusyWait, known_lease_seconds
from src.batch.worker_persistence import WorkerPersistenceMixin
from src.batch.worker_types import BatchWorkerConfig
from src.db.runtime.allocated_client import DatabaseUnavailableError


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now


class _Database:
    """One claimed item row, a busy period, and a fixed time for each call."""

    def __init__(
        self,
        clock: _Clock,
        *,
        lease_until: float,
        busy_until: float,
        latency: float = 0.0,
        renewals_busy: bool = True,
    ) -> None:
        self.clock = clock
        self.lease_until = lease_until
        self.busy_until = busy_until
        self.latency = latency
        self.renewals_busy = renewals_busy
        self.claimed_by_other = False
        self.saved_at: float | None = None
        self.saves = 0
        self.renewals: list[float] = []
        self.requeues: list[float] = []
        self.on_save = None

    def _call(self) -> float:
        started = self.clock.now
        self.clock.now += self.latency
        if self.clock.now > self.lease_until:
            # Another worker claims the item as soon as its lease runs out.
            self.claimed_by_other = True
        return started

    async def complete_items_with_outbox_bulk(self, *, items, worker_id, **kwargs):
        del items, worker_id, kwargs
        self.saves += 1
        if self.on_save is not None:
            self.on_save(self.saves)
        started = self._call()
        if started < self.busy_until:
            raise DatabaseUnavailableError()
        if self.claimed_by_other:
            return "not_owned"
        self.saved_at = self.clock.now
        return "completed"

    async def renew_item_leases(self, *, item_claim_epochs, worker_id, lease_seconds):
        del worker_id
        started = self._call()
        if self.renewals_busy and started < self.busy_until:
            raise DatabaseUnavailableError()
        self.renewals.append(started)
        if self.claimed_by_other:
            return set()
        self.lease_until = self.clock.now + lease_seconds
        return set(item_claim_epochs)

    async def release_items_for_retry(self, *, item_ids, **kwargs):
        del kwargs
        started = self._call()
        if started < self.busy_until:
            raise DatabaseUnavailableError()
        self.requeues.append(started)
        return [] if self.claimed_by_other else list(item_ids)


class _Engine(WorkerPersistenceMixin):
    def __init__(self, database: _Database, *, lease_seconds: int) -> None:
        self.repository = database
        self.config = BatchWorkerConfig(worker_id="w1", item_lease_seconds=lease_seconds)


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    clock = _Clock(1000.0)
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        clock.now += seconds
        await real_sleep(0)

    monkeypatch.setattr(result_save_waits, "monotonic", clock)
    monkeypatch.setattr(result_save_waits.random, "uniform", lambda low, high: low)
    monkeypatch.setattr("src.batch.worker_persistence.asyncio.sleep", fake_sleep)
    return clock


async def _save(engine: _Engine, *, lease_left: float) -> bool:
    lease_end = datetime.now(UTC) + timedelta(seconds=lease_left + CLOCK_SKEW_SECONDS)
    return await engine._persist_completion_rows_with_outbox(
        items=[{"item_id": "i1", "claim_epoch": 1}],
        item_ids=["i1"],
        context_label="test",
        lease_ends=[lease_end],
    )


async def test_busy_period_is_waited_out_under_the_claimed_lease(clock):
    # Dev load test 2026-11-10: the pool was full for about 10 s while 12 jobs
    # started. The save gave up after 3.5 s and the item later failed as
    # accounting_closed. The claimed lease covers the longer waits, so the
    # answer is saved with no extra query.
    database = _Database(clock, lease_until=1340.0, busy_until=1012.0, latency=0.01)
    engine = _Engine(database, lease_seconds=360)

    assert await _save(engine, lease_left=340.0)

    assert clock.sleeps == [0.5, 1.0, 2.0, 4.0, 8.0]
    assert database.saved_at is not None and database.saved_at < database.lease_until
    assert database.renewals == []
    assert database.requeues == []


async def test_a_renewal_keeps_the_lease_for_the_next_save(clock):
    # A 30 s lease, a save that starts 14.9 s after the last renewal, and
    # database calls that take 2 s each. The claimed lease cannot cover a long
    # wait, so the worker renews it and saves again at once.
    database = _Database(clock, lease_until=1015.1, busy_until=1011.0, latency=2.0)
    engine = _Engine(database, lease_seconds=30)

    assert await _save(engine, lease_left=15.1)

    assert clock.sleeps == [0.5, 1.0, 2.0]
    assert database.renewals == [1011.5]
    assert not database.claimed_by_other
    assert database.saved_at == 1015.5


async def test_no_long_wait_without_a_lease_known_to_cover_it(clock):
    # The renewal is refused like the saves, so the worker does not wait
    # longer than before the long waits existed: it retries and requeues.
    database = _Database(clock, lease_until=1015.1, busy_until=1030.0, latency=2.0)
    engine = _Engine(database, lease_seconds=30)

    assert not await _save(engine, lease_left=15.1)

    assert all(not seconds > 2.0 for seconds in clock.sleeps)
    assert database.saved_at is None


async def test_a_second_claim_stops_the_waits(clock):
    database = _Database(clock, lease_until=999.0, busy_until=1003.6, renewals_busy=False)
    database.claimed_by_other = True
    engine = _Engine(database, lease_seconds=30)

    assert not await _save(engine, lease_left=1.0)

    assert clock.sleeps == [0.5, 1.0, 2.0, 0.5]
    assert len(database.renewals) == 2
    assert database.saved_at is None


async def test_drain_stops_the_long_waits_and_requeues_the_item(clock):
    database = _Database(clock, lease_until=1340.0, busy_until=float("inf"))
    engine = _Engine(database, lease_seconds=360)
    # The shutdown drain starts during the fifth save, after one long wait.
    database.on_save = lambda count: engine.start_draining() if count == 5 else None

    assert not await _save(engine, lease_left=340.0)

    assert database.saves == 6
    # Requeue waits follow the long wait; no long wait after the drain.
    assert clock.sleeps == [0.5, 1.0, 2.0, 4.0, 0.5, 1.0, 2.0]


async def test_drain_ends_a_long_wait_at_once():
    engine = _Engine(_Database(_Clock(0.0), lease_until=0.0, busy_until=0.0), lease_seconds=360)
    waiting = asyncio.create_task(engine._sleep_unless_draining(BusyWait(seconds=30, long=True)))
    await asyncio.sleep(0)
    engine.start_draining()

    await asyncio.wait_for(waiting, timeout=1)


def test_known_lease_is_the_earliest_claimed_lease_end_minus_clock_skew():
    now = datetime.now(UTC)
    later = now + timedelta(seconds=100)
    sooner = now + timedelta(seconds=60)

    assert known_lease_seconds([later, sooner]) == pytest.approx(60 - CLOCK_SKEW_SECONDS, abs=1)
    assert known_lease_seconds([]) is None
    assert known_lease_seconds([later, None]) is None
