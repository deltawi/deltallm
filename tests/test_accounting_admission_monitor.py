"""Dependency failure blocks warm issue without adding per-request calls."""

import asyncio

import pytest

from src.billing.accounting.health.accounting_admission_monitor import AccountingAdmissionMonitor
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.health.accounting_native_observation import NativeAccountingObservation
from src.billing.accounting.health.accounting_presence import ProjectionPresence
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.permits.accounting_permit_results import invalid_result
from src.concurrency import CapacityGateFull
from src.telemetry.lifecycle import WorkerState
from src.billing.accounting.health.accounting_health import (
    AccountingBacklogPolicy,
    AccountingBacklogProbe,
)
from tests.test_accounting_health import Persistence as BacklogPersistence
from tests.test_accounting_local_issuer import state as owners, items
from tests.test_accounting_presence import Processing


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


class Observation:
    def __init__(self):
        self.calls = []
        self.ready = True
        self.error = None
        self.entered = asyncio.Event()
        self.resume = None

    async def observe_ready(self, **kwargs):
        self.calls.append(kwargs)
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        if self.error is not None:
            raise self.error
        return self.ready


async def started():
    observation = Observation()
    worker = AccountingAdmissionMonitor(observation)
    await worker.start(expires_at=deadline())
    return worker, observation


@pytest.mark.parametrize("first_failure", ["negative", "database", "timeout"])
async def test_start_waits_for_ready_within_the_existing_deadline(first_failure):
    class InitialFailure(Observation):
        async def observe_ready(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                if first_failure == "database":
                    raise invalid_result()
                if first_failure == "timeout":
                    raise TimeoutError()
                return False
            return True

    observation = InitialFailure()
    worker = AccountingAdmissionMonitor(observation, poll_seconds=0.1, call_seconds=0.04)
    try:
        await worker.start(expires_at=deadline())
        assert len(observation.calls) == 2
        assert worker.worker_health.state is WorkerState.READY
        assert worker.task is not None and not worker.task.done()
    finally:
        await worker.close(expires_at=deadline())


async def test_start_deadline_does_not_accept_an_unready_dependency_or_leave_a_task():
    observation = Observation()
    observation.ready = False
    worker = AccountingAdmissionMonitor(observation)
    with pytest.raises(TimeoutError, match="startup deadline"):
        await worker.start(expires_at=deadline(0.03))
    assert len(observation.calls) == 1
    assert worker.task is not None
    await asyncio.gather(worker.task, return_exceptions=True)
    assert worker.task.done()
    assert worker.worker_health.state is WorkerState.STOPPING


async def test_cancelled_start_closes_its_one_observation_task():
    observation = Observation()
    observation.resume = asyncio.Event()
    worker = AccountingAdmissionMonitor(observation)
    task = asyncio.create_task(worker.start(expires_at=deadline()))
    await observation.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.task is not None
    await asyncio.gather(worker.task, return_exceptions=True)
    assert worker.task.done()
    assert worker.worker_health.state is WorkerState.STOPPING


async def test_start_preserves_an_unexpected_observation_error_and_stops_the_task():
    observation = Observation()
    observation.error = RuntimeError("observation failed")
    worker = AccountingAdmissionMonitor(observation)
    with pytest.raises(RuntimeError, match="observation failed"):
        await worker.start(expires_at=deadline())
    assert len(observation.calls) == 1
    assert worker.task is not None and worker.task.done()
    assert worker.worker_health.state is WorkerState.FAILED


async def test_real_start_one_call_freshness_and_close_reject_restart():
    worker, observation = await started()
    try:
        assert worker.worker_health.state is WorkerState.READY and len(observation.calls) == 1
        assert worker.task is not None and not worker.task.done()
        worker._observed_at -= 5
        assert worker.worker_health.state is WorkerState.DEGRADED
        end = deadline()
        assert await worker.refresh(expires_at=end)
        assert observation.calls[-1] == dict(expires_at=end)
        assert worker.worker_health.state is WorkerState.READY
    finally:
        assert await worker.close(expires_at=deadline())
    assert worker.task.done() and worker.worker_health.state is WorkerState.STOPPING
    with pytest.raises(RuntimeError):
        await worker.start(expires_at=deadline())


@pytest.mark.parametrize(
    "kind", ["negative", "invalid", "database", "unexpected", "cancel", "timeout"]
)
async def test_failure_is_immediately_unready_and_does_not_expose_private_text(kind):
    worker, observation = await started()
    try:
        if kind == "negative":
            observation.ready = False
        elif kind == "invalid":
            observation.ready = 1
        elif kind in {"database", "unexpected"}:
            observation.error = invalid_result() if kind == "database" else RuntimeError("private")
        else:
            observation.entered.clear()
            observation.resume = asyncio.Event()
        task = asyncio.create_task(worker.refresh(expires_at=deadline(0.03)))
        if kind in {"cancel", "timeout"}:
            await observation.entered.wait()
        if kind == "cancel":
            task.cancel()
        if kind in {"unexpected", "cancel"}:
            with pytest.raises(asyncio.CancelledError if kind == "cancel" else RuntimeError):
                await task
        else:
            assert await task is False
        assert worker.worker_health.state is WorkerState.DEGRADED
        assert "private" not in worker.worker_health.detail
        observation.ready = True
        observation.error = observation.resume = None
        assert await worker.refresh(expires_at=deadline())
    finally:
        assert await worker.close(expires_at=deadline())


async def test_overlap_no_waiter_and_late_completion_after_close_does_not_restore_ready():
    worker, observation = await started()
    observation.entered.clear()
    observation.resume = asyncio.Event()
    task = asyncio.create_task(worker.refresh(expires_at=deadline()))
    await observation.entered.wait()
    with pytest.raises(CapacityGateFull):
        await worker.refresh(expires_at=deadline())
    assert len(observation.calls) == 2
    assert not await worker.close(expires_at=deadline())
    observation.resume.set()
    assert await task is False
    assert worker.worker_health.state is WorkerState.STOPPING
    assert await worker.close(expires_at=deadline())


async def test_refresh_retains_fresh_ready_but_not_stale_or_failed_health():
    worker, observation = await started()
    observation.entered.clear()
    observation.resume = asyncio.Event()
    task = asyncio.create_task(worker.refresh(expires_at=deadline()))
    try:
        await observation.entered.wait()
        assert worker.worker_health.state is WorkerState.READY
        worker._observed_at -= 5
        assert worker.worker_health.state is WorkerState.DEGRADED
        observation.ready = False
        observation.resume.set()
        assert await task is False
        assert worker.worker_health.state is WorkerState.DEGRADED
    finally:
        observation.resume.set()
        await task
        assert await worker.close(expires_at=deadline())


@pytest.mark.parametrize("end", [True, float("nan"), float("inf"), -1])
async def test_invalid_or_expired_start_cannot_issue_a_query(end):
    observation = Observation()
    worker = AccountingAdmissionMonitor(observation)
    with pytest.raises((ValueError, TimeoutError, RuntimeError)):
        await worker.start(expires_at=end)
    assert not observation.calls


class Presence:
    def __init__(self, ready=1):
        self.calls = []
        self.value = ProjectionPresence(generation=7, present_slots=1, ready_slots=ready)

    async def snapshot(self, **kwargs):
        self.calls.append(kwargs)
        return self.value


@pytest.mark.parametrize("ready", [0, 1])
async def test_native_observation_needs_backlog_and_actual_presence(ready):
    backlog = BacklogPersistence()
    runtime = AccountingBacklogProbe(backlog, AccountingBacklogPolicy(generation=7))
    presence = Presence(ready)
    owner = NativeAccountingObservation(runtime, presence, generation=7)
    end = deadline()
    assert await owner.observe_ready(expires_at=end) is bool(ready)
    assert len(backlog.calls) == 1 and presence.calls == [dict(generation=7, expires_at=end)]
    presence.value = presence.value.model_copy(update={"generation": 8})
    with pytest.raises(AccountingProtocolUnavailable):
        await owner.observe_ready(expires_at=deadline())


@pytest.mark.parametrize("state", [WorkerState.DISABLED, WorkerState.DEGRADED, WorkerState.FAILED])
async def test_health_loss_blocks_even_a_warm_prefix_before_issuing(state):
    persistence, cursors, receipts, _ = owners()
    health = Processing()
    issuer = LocalPermitIssuer(persistence, cursors, receipts, admission_health=health)
    first = items(1)[0]
    await issuer.reserve_batch([first], expires_at=deadline())
    calls = len(persistence.calls)
    entries = receipts.entries
    health.state = state
    with pytest.raises(AccountingProtocolUnavailable):
        await issuer.reserve_batch(items(1, first), expires_at=deadline())
    assert len(persistence.calls) == calls and receipts.entries == entries


async def test_task_failure_cannot_keep_cached_ready():
    worker, observation = await started()
    worker.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker.task
    assert worker.worker_health.state is WorkerState.FAILED
    assert not await worker.close(expires_at=deadline())
    assert worker.worker_health.state is WorkerState.FAILED
    assert not await worker.close(expires_at=deadline())


async def test_health_loss_during_funding_returns_the_unissued_suffix_without_dispatch():
    persistence, cursors, receipts, _ = owners()
    health = Processing()
    persistence.continue_funding = asyncio.Event()
    issuer = LocalPermitIssuer(persistence, cursors, receipts, admission_health=health)
    task = asyncio.create_task(issuer.reserve_batch(items(1), expires_at=deadline()))
    await persistence.entered.wait()
    health.state = WorkerState.DEGRADED
    persistence.continue_funding.set()
    with pytest.raises(AccountingProtocolUnavailable):
        await task
    assert receipts.entries == 0 and cursors.staged_grants == 0
    assert cursors.available_permits == 0
    returns = cursors.return_candidates(limit=1)
    assert len(returns) == 1 and returns[0].first_unused_ordinal == 0
