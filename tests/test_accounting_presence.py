"""Health leases have fixed state, full fences, and no financial authority."""

import asyncio
from uuid import uuid4

import pytest

from src.billing.accounting.health.accounting_presence import (
    ProjectionLease,
    ProjectionPresence,
    ProjectionPresencePublisher,
)
from src.billing.accounting.journal.accounting_recovery import (
    AccountingRecoveryWorker,
    RecoveryConfig,
)
from src.concurrency import CapacityGateFull
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.permits.accounting_permit_results import invalid_result
from src.db.accounting.health.accounting_presence import AccountingPresenceRepository
from src.telemetry.lifecycle import WorkerHealth, WorkerState
from tests.test_accounting_recovery import Persistence as RecoveryPersistence, worker as recovery


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


class Processing:
    def __init__(self, state=WorkerState.READY):
        self.state = state

    @property
    def worker_health(self):
        return WorkerHealth(self.state)


class Persistence:
    def __init__(self):
        self.calls = []
        self.accepted = True
        self.error = None
        self.entered = asyncio.Event()
        self.resume = None
        self.lease = None

    async def initialize(self, **kwargs):
        self.calls.append(("initialize", kwargs))

    async def acquire(self, **kwargs):
        self.calls.append(("acquire", kwargs))
        self.lease = ProjectionLease(
            generation=kwargs["generation"], slot=0, owner_token=kwargs["owner_token"]
        )
        return self.lease

    async def publish(self, lease, **kwargs):
        self.calls.append(("publish", lease, kwargs))
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        if self.error is not None:
            raise self.error
        return self.accepted

    async def release(self, lease, **kwargs):
        self.calls.append(("release", lease, kwargs))
        return True


async def publisher(processing=None):
    persistence = Persistence()
    clock = [100.0]
    processing = processing or Processing()
    instance = ProjectionPresencePublisher(
        persistence, generation=7, processing=processing, clock=lambda: clock[0]
    )
    await instance.start(expires_at=deadline())
    return instance, persistence, clock, processing


async def test_start_does_not_publish_ready_until_actual_observation():
    instance, persistence, _, _ = await publisher()
    assert instance.worker_health.state is WorkerState.DEGRADED
    end = deadline()
    await instance.observe(expires_at=end)
    assert instance.worker_health.state is WorkerState.READY
    assert [call[0] for call in persistence.calls] == ["initialize", "acquire", "publish"]
    assert persistence.calls[-1][1] == instance.lease
    assert persistence.calls[-1][2] == dict(ready=True, lease_seconds=10, expires_at=end)
    await instance.close(expires_at=end)
    assert instance.worker_health.state is WorkerState.STOPPING
    assert persistence.calls[-1][0] == "release"
    with pytest.raises(RuntimeError):
        await instance.start(expires_at=end)
    with pytest.raises(AccountingProtocolUnavailable):
        await instance.observe(expires_at=end)


@pytest.mark.parametrize("state", list(WorkerState))
async def test_only_a_live_ready_processor_can_publish_ready(state):
    instance, persistence, _, processing = await publisher(Processing(state))
    if state is WorkerState.READY:
        await instance.observe(expires_at=deadline())
        processing.state = WorkerState.DISABLED
    else:
        with pytest.raises(AccountingProtocolUnavailable):
            await instance.observe(expires_at=deadline())
    assert instance.worker_health.state is WorkerState.DEGRADED
    assert persistence.calls[-1][2]["ready"] is (state is WorkerState.READY)


@pytest.mark.parametrize("elapsed", [10, -1, float("nan"), float("inf")])
async def test_elapsed_or_invalid_clock_never_proves_health(elapsed):
    instance, _, clock, _ = await publisher()
    await instance.observe(expires_at=deadline())
    clock[0] += elapsed
    assert instance.worker_health.state is WorkerState.DEGRADED


async def test_expiry_requires_a_new_fence_and_only_two_calls():
    instance, persistence, clock, _ = await publisher()
    await instance.observe(expires_at=deadline())
    original = instance.lease
    clock[0] += 10
    await instance.observe(expires_at=deadline())
    assert instance.lease.owner_token != original.owner_token
    assert [call[0] for call in persistence.calls[-2:]] == ["acquire", "publish"]


@pytest.mark.parametrize("accepted", [False, None, 1, "true"])
async def test_failed_or_malformed_reply_is_not_healthy(accepted):
    instance, persistence, _, _ = await publisher()
    persistence.accepted = accepted
    with pytest.raises(AccountingProtocolUnavailable):
        await instance.observe(expires_at=deadline())
    assert instance.worker_health.state is WorkerState.DEGRADED


@pytest.mark.parametrize("failure", ["cancel", "timeout", "unexpected", "database"])
async def test_failed_observation_keeps_the_fence_and_cannot_restore_health(failure):
    instance, persistence, _, _ = await publisher()
    lease = instance.lease
    persistence.resume = asyncio.Event()
    task = asyncio.create_task(instance.observe(expires_at=deadline(0.04)))
    await persistence.entered.wait()
    if failure == "cancel":
        task.cancel()
        error = asyncio.CancelledError
    elif failure == "timeout":
        error = TimeoutError
    else:
        persistence.error = RuntimeError("private") if failure == "unexpected" else invalid_result()
        persistence.resume.set()
        error = RuntimeError
    with pytest.raises(error):
        await task
    assert instance.lease == lease and instance.worker_health.state is WorkerState.DEGRADED
    assert "private" not in instance.worker_health.detail
    persistence.resume = persistence.error = None
    await instance.observe(expires_at=deadline())
    assert instance.worker_health.state is WorkerState.READY


async def test_overlap_has_no_waiter_and_late_publish_after_close_is_not_ready():
    instance, persistence, _, _ = await publisher()
    persistence.resume = asyncio.Event()
    task = asyncio.create_task(instance.observe(expires_at=deadline()))
    await persistence.entered.wait()
    with pytest.raises(CapacityGateFull):
        await instance.observe(expires_at=deadline())
    assert len(persistence.calls) == 3
    await instance.close(expires_at=deadline())
    persistence.resume.set()
    with pytest.raises(AccountingProtocolUnavailable):
        await task
    assert instance.worker_health.state is WorkerState.STOPPING


async def test_renewal_retains_only_a_fresh_healthy_completed_observation():
    instance, persistence, clock, _ = await publisher()
    await instance.observe(expires_at=deadline())
    persistence.entered.clear()
    persistence.resume = asyncio.Event()
    task = asyncio.create_task(instance.observe(expires_at=deadline()))
    try:
        await persistence.entered.wait()
        assert instance.worker_health.state is WorkerState.READY
        clock[0] += 10
        assert instance.worker_health.state is WorkerState.DEGRADED
        persistence.accepted = False
        persistence.resume.set()
        with pytest.raises(AccountingProtocolUnavailable):
            await task
        assert instance.worker_health.state is WorkerState.DEGRADED
    finally:
        persistence.resume.set()
        await instance.close(expires_at=deadline())


@pytest.mark.parametrize("end", [True, float("nan"), float("inf"), -1])
async def test_bad_deadline_does_not_start_or_query(end):
    persistence = Persistence()
    instance = ProjectionPresencePublisher(persistence, generation=7, processing=Processing())
    with pytest.raises((ValueError, AccountingProtocolUnavailable)):
        await instance.start(expires_at=end)
    assert not persistence.calls


async def test_recovery_observes_after_all_actions_and_health_under_same_deadline():
    instance, persistence, _, _ = await publisher()
    native = RecoveryPersistence()
    base = recovery(native)
    worker = AccountingRecoveryWorker(
        native, base.probe, RecoveryConfig(generation=7), observer=instance
    )
    end = deadline()
    assert await worker.run_once(expires_at=end) == 4
    assert len(native.calls) == 4 and len(native.snapshots) == 1
    assert persistence.calls[-1][2]["expires_at"] == end
    instance.generation = 8
    with pytest.raises(ValueError):
        AccountingRecoveryWorker(
            native, base.probe, RecoveryConfig(generation=7), observer=instance
        )


class QueryClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def query_raw(self, query, *parameters):
        self.calls.append((query, parameters))
        return self.rows


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{}],
        [{"generation": 7, "present_slots": True, "ready_slots": 0}],
        [{"generation": 8, "present_slots": 0, "ready_slots": 0}],
        [{"generation": 7, "present_slots": 1, "ready_slots": 2}],
    ],
)
async def test_missing_or_bad_snapshot_never_becomes_healthy_zero(rows):
    db = QueryClient(rows)
    repository = AccountingPresenceRepository(db)
    with pytest.raises(AccountingProtocolUnavailable):
        await repository.snapshot(generation=7, expires_at=deadline())
    assert len(db.calls) == 1


async def test_snapshot_and_string_database_uuid_are_strictly_validated():
    db = QueryClient([{"generation": 7, "present_slots": 0, "ready_slots": 0}])
    repository = AccountingPresenceRepository(db)
    assert await repository.snapshot(generation=7, expires_at=deadline()) == ProjectionPresence(
        generation=7, present_slots=0, ready_slots=0
    )
    token = uuid4()
    db.rows = [{"generation": 7, "slot": 0, "owner_token": str(token)}]
    lease = await repository.acquire(
        generation=7, owner_token=token, lease_seconds=10, expires_at=deadline()
    )
    assert lease == ProjectionLease(generation=7, slot=0, owner_token=token)
    db.rows = [{"slot": 0}]
    assert await repository.publish(lease, ready=False, lease_seconds=10, expires_at=deadline())
    db.rows = []
    assert not await repository.release(lease, expires_at=deadline())


@pytest.mark.parametrize("slot", [True, 1, -1, "0", None])
async def test_mutation_response_cannot_change_the_slot_identity(slot):
    repository = AccountingPresenceRepository(QueryClient([{"slot": slot}]))
    with pytest.raises(AccountingProtocolUnavailable):
        await repository.release(
            ProjectionLease(generation=7, slot=0, owner_token=uuid4()), expires_at=deadline()
        )
