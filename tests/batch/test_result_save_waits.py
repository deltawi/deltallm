"""A result save waits out a busy database only under a known lease, and stops on drain."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.batch import result_save_waits
from src.batch.result_save_waits import CLOCK_SKEW_SECONDS, BusyWait, known_lease_seconds
from src.batch.worker_persistence import WorkerPersistenceMixin
from src.batch.worker_types import BatchWorkerConfig
from src.bootstrap.batch_runtime.pool_sizing import batch_database_call_seconds
from src.db.runtime.allocation_config import DatabasePolicy
from src.db.runtime.allocated_client import (
    AllocatedTransaction,
    DatabaseOwner,
    DatabaseUnavailableError,
)

_real_sleep = asyncio.sleep


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
        self.on_renew = None
        # Another item's save holds the save slot until this is set.
        self.other_save_done = asyncio.Event()
        self.other_saving = False
        # Renewals that pass their caller's deadline while the UPDATE goes on
        # and holds the item row for 3 s more.
        self.renewal_deadlines = 0
        self.row_locked_until = float("-inf")
        # Ownership reads and lease renewals that the busy pool refuses.
        self.busy_reads = 0
        self.refused_renewals = 0
        self.renewal_latency: float | None = None
        self.renewal_calls = 0

    def _call(self, latency: float | None = None) -> float:
        started = self.clock.now
        self.clock.now += self.latency if latency is None else latency
        if self.clock.now > self.lease_until:
            # Another worker claims the item as soon as its lease runs out.
            self.claimed_by_other = True
        return started

    async def complete_items_with_outbox_bulk(self, *, items, worker_id, **kwargs):
        del worker_id, kwargs
        if items[0]["item_id"] == "other":
            self.other_saving = True
            await self.other_save_done.wait()
            return "completed"
        self.saves += 1
        if self.on_save is not None:
            self.on_save(self.saves)
        started = self._call()
        if started < self.busy_until:
            raise DatabaseUnavailableError()
        if self.claimed_by_other or started < self.row_locked_until:
            # FOR UPDATE SKIP LOCKED skips a row that another statement holds.
            return "not_owned"
        self.saved_at = self.clock.now
        return "completed"

    async def renew_item_leases(self, *, item_claim_epochs, worker_id, lease_seconds):
        del worker_id
        # A database call lets other tasks run.
        await _real_sleep(0)
        self.renewal_calls += 1
        started = self._call(self.renewal_latency)
        if self.refused_renewals:
            self.refused_renewals -= 1
            raise DatabaseUnavailableError()
        if self.renewal_deadlines:
            self.renewal_deadlines -= 1
            self.row_locked_until = self.clock.now + 3.0
            raise DatabaseUnavailableError()
        if self.renewals_busy and started < self.busy_until:
            raise DatabaseUnavailableError()
        self.renewals.append(started)
        if self.claimed_by_other:
            return set()
        self.lease_until = self.clock.now + lease_seconds
        if self.on_renew is not None:
            await self.on_renew()
        return set(item_claim_epochs)

    async def list_items_by_ids(self, item_ids):
        self._call()
        if self.busy_reads:
            self.busy_reads -= 1
            raise DatabaseUnavailableError()
        owner = "w2" if self.claimed_by_other else "w1"
        return [
            SimpleNamespace(item_id=item_id, status="in_progress", locked_by=owner, claim_epoch=1)
            for item_id in item_ids
        ]

    async def release_items_for_retry(self, *, item_ids, **kwargs):
        del kwargs
        started = self._call()
        if started < self.busy_until:
            raise DatabaseUnavailableError()
        self.requeues.append(started)
        return [] if self.claimed_by_other else list(item_ids)


class _Engine(WorkerPersistenceMixin):
    def __init__(
        self,
        database: _Database,
        *,
        lease_seconds: int,
        save_slots: int = 0,
        call_seconds: float = 0.0,
    ) -> None:
        self.repository = database
        self.config = BatchWorkerConfig(
            worker_id="w1",
            item_lease_seconds=lease_seconds,
            result_save_concurrency=save_slots,
            write_grouping_enabled=save_slots > 0,
            database_call_seconds=call_seconds,
        )


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    clock = _Clock(1000.0)

    async def fake_sleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        clock.now += seconds
        await _real_sleep(0)

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
    # accounting_closed. The claimed lease covers the longer waits, so no
    # renewal is needed: no query is sent but the saves.
    database = _Database(clock, lease_until=1340.0, busy_until=1012.0, latency=0.01)
    engine = _Engine(database, lease_seconds=360)

    assert await _save(engine, lease_left=340.0)

    assert clock.sleeps == [0.5, 1.0, 2.0, 4.0, 8.0]
    assert database.saved_at is not None and database.saved_at < database.lease_until
    assert database.renewal_calls == 0
    assert database.requeues == []


async def test_a_slow_refused_renewal_cannot_use_the_lease_left_for_the_save(clock):
    # Codex review 2026-11-10: a 30 s lease, and database calls that can take
    # 7 s (pool admission and statement timeout). A renewal refused after
    # 14 s would leave no lease for the save. The known lease (to 1025) does
    # not cover a long wait plus a renewal and a save, nor a renewal and a
    # save after it, so the worker sends no renewal and saves at once, as
    # before the long waits.
    database = _Database(clock, lease_until=1025.0, busy_until=1011.0, latency=2.0)
    database.refused_renewals = 1
    database.renewal_latency = 14.0
    engine = _Engine(database, lease_seconds=30, save_slots=1, call_seconds=7.0)

    assert await _save(engine, lease_left=25.0)

    assert clock.sleeps == [0.5, 1.0, 2.0]
    assert database.renewal_calls == 0
    assert database.saved_at == 1013.5
    assert not database.claimed_by_other


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


async def _save_while_another_save_holds_the_slot(
    engine: _Engine, *, lease_left: float, hold_seconds: float
) -> bool:
    """Another save takes the only save slot when the fourth (busy) save ends.

    If the answer then waits in the save queue, the other save holds the slot
    for ``hold_seconds`` more.
    """
    database = engine.repository
    queue = engine._result_save_groups()
    assert queue is not None
    others: list[asyncio.Task] = []

    def other_save_takes_the_slot(count: int) -> None:
        if count == 4:
            others.append(asyncio.create_task(queue.run([{"item_id": "other", "claim_epoch": 1}])))

    database.on_save = other_save_takes_the_slot
    saving = asyncio.create_task(_save(engine, lease_left=lease_left))
    while not saving.done() and not (database.other_saving and queue.waiting):
        await _real_sleep(0)
    if queue.waiting:
        database.clock.now += hold_seconds
    database.other_save_done.set()
    saved = await saving
    await asyncio.gather(*others)
    assert database.other_saving
    return saved


async def test_a_save_after_a_long_wait_skips_the_save_queue(clock):
    # Codex review 2026-11-10: the save queue has no deadline, and a save can
    # wait in it behind other saves for longer than a whole lease. Here
    # another save holds the only save slot for 65 s, more than the 60 s
    # lease that a renewal would give. The save after the long wait goes to
    # the database without the queue, under the lease that the wait check
    # kept (to 1040), and no renewal is sent.
    database = _Database(
        clock, lease_until=1040.0, busy_until=1010.0, latency=1.0, renewals_busy=False
    )
    engine = _Engine(database, lease_seconds=60, save_slots=1)

    assert await _save_while_another_save_holds_the_slot(engine, lease_left=40.0, hold_seconds=65.0)

    assert clock.sleeps == [0.5, 1.0, 2.0, 4.0]
    assert database.renewal_calls == 0
    assert database.saved_at == 1012.5
    assert not database.claimed_by_other


async def test_a_save_skips_the_save_queue_when_the_renewal_before_a_long_wait_is_refused(clock):
    # Codex review 2026-11-10: a 60 s lease, 12.2 s database calls, and a
    # known lease end of 1039 (the real lease ends at 1044). The 4 s wait is
    # not covered, so the worker renews the lease; the busy pool refuses the
    # renewal after 7.2 s. The save then comes later than on main, with less
    # lease, while another save holds the only save slot for 30 s more. It
    # goes to the database without the queue.
    database = _Database(
        clock, lease_until=1044.0, busy_until=1011.0, latency=2.0, renewals_busy=False
    )
    database.refused_renewals = 1
    database.renewal_latency = 7.2
    engine = _Engine(database, lease_seconds=60, save_slots=1, call_seconds=12.2)

    assert await _save_while_another_save_holds_the_slot(engine, lease_left=39.0, hold_seconds=30.0)

    assert clock.sleeps == [0.5, 1.0, 2.0]
    assert database.renewal_calls == 1
    assert database.saved_at == pytest.approx(1020.7)
    assert not database.claimed_by_other


@pytest.mark.parametrize("busy_reads", [0, 1])
async def test_a_save_that_skips_a_row_held_by_a_late_renewal_is_tried_again(clock, busy_reads):
    # Codex review 2026-11-10: the lease left (60 s) does not cover a long wait
    # and its reserve, so the worker renews it. The renewal reports a timeout
    # at its caller's deadline, but its UPDATE goes on and holds the item row
    # for 3 s more. The next save skips the row and reports not_owned. The
    # item is still claimed by this worker at the same epoch, so the worker
    # waits and saves again instead of giving up the answer. A busy pool that
    # refuses the ownership read gets the same wait as a busy save.
    database = _Database(
        clock, lease_until=1060.0, busy_until=1003.545, latency=0.01, renewals_busy=False
    )
    database.renewal_deadlines = 1
    database.busy_reads = busy_reads
    engine = _Engine(database, lease_seconds=360, save_slots=1)

    assert await _save(engine, lease_left=60.0)

    assert database.busy_reads == 0
    # The first long wait is replaced by the late renewal, the second by a
    # renewal that succeeds; the save after it still finds the row held.
    assert clock.sleeps == [0.5, 1.0, 2.0, 8.0]
    assert database.saves == 7
    assert database.saved_at is not None and database.saved_at < database.lease_until
    assert database.requeues == []


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


class _FastNativePolicy(DatabasePolicy):
    @property
    def native_query_seconds(self) -> float:
        return 0.2


async def test_database_call_time_bounds_a_transaction_that_times_out_and_rolls_back_slowly():
    # Codex review 2026-11-10: after its timeout, a transaction still waits for
    # its last native query and for the rollback. The call time must cover
    # both.
    policy = _FastNativePolicy("control", 1, 0.05, 0.05, 0.05, 0.1, 0.0)
    owner = DatabaseOwner(policy)
    app = SimpleNamespace(state=SimpleNamespace(prisma_manager=SimpleNamespace(allocation=owner)))

    async def never_ends() -> None:
        await asyncio.Event().wait()

    native = SimpleNamespace(
        start=AsyncMock(return_value=SimpleNamespace()),
        commit=AsyncMock(),
        rollback=never_ends,
    )
    transaction = AllocatedTransaction(owner, native)
    started = monotonic()
    with pytest.raises(DatabaseUnavailableError):
        async with transaction:
            # The last query starts just before the transaction timeout.
            await asyncio.sleep(0.09)
            await transaction.query(never_ends)
    elapsed = monotonic() - started
    await owner.close()

    assert elapsed > 0.4
    assert elapsed <= batch_database_call_seconds(app)
    assert batch_database_call_seconds(app) == pytest.approx(0.05 + 0.05 + 0.1 + 0.4)
    assert batch_database_call_seconds(SimpleNamespace(state=SimpleNamespace())) == 0.0


def test_known_lease_is_the_earliest_claimed_lease_end_minus_clock_skew():
    now = datetime.now(UTC)
    later = now + timedelta(seconds=100)
    sooner = now + timedelta(seconds=60)

    assert known_lease_seconds([later, sooner]) == pytest.approx(60 - CLOCK_SKEW_SECONDS, abs=1)
    assert known_lease_seconds([]) is None
    assert known_lease_seconds([later, None]) is None
