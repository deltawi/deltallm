"""Empty native lanes back off; active work and failure recovery keep their owners."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.billing.accounting.reporting.accounting_read_model_claims import ReadModelWorkerConfig
from src.billing.accounting.reporting.accounting_read_model_runtime import ReadModelProcessingWorker
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.telemetry_acceptance import AcceptanceFailure
from src.telemetry.lifecycle import WorkerState
from tests.test_accounting_journal_runtime import Persistence as JournalPersistence, worker
from tests.test_accounting_read_model import Persistence as ReportPersistence


def idle_owner(role, interval=0.02):
    if role == "journal":
        return worker(JournalPersistence(), poll_seconds=interval)
    return ReadModelProcessingWorker(
        ReportPersistence(),
        ReadModelWorkerConfig(generation=7, worker_id="report-test", poll_seconds=interval),
    )


@pytest.mark.parametrize("role", ["journal", "report"])
async def test_empty_lanes_reduce_claim_rate_without_changing_health(monkeypatch, role):
    owner = idle_owner(role)
    calls = AsyncMock(return_value=0)
    monkeypatch.setattr(owner, "run_once", calls)
    waits = []

    async def wait(interval):
        waits.append(owner._idle.next_delay(interval))
        assert owner.worker_health.state is WorkerState.READY
        if len(waits) == 10:
            owner.stop_claims()

    monkeypatch.setattr(owner._idle, "wait", wait)
    await owner._run()
    assert calls.await_count == 10
    assert waits == [0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1, 1, 1, 1]
    assert sum(waits) == pytest.approx(5.26)
    assert sum(waits) / 0.02 == pytest.approx(263)
    assert owner.worker_health.state is WorkerState.STOPPING


@pytest.mark.parametrize("role", ["journal", "report"])
async def test_completed_work_resets_idle_delay_and_is_followed_by_an_immediate_claim(
    monkeypatch, role
):
    owner = idle_owner(role)
    calls = AsyncMock(side_effect=[0, 0, 2, 0])
    monkeypatch.setattr(owner, "run_once", calls)
    waits = []

    async def wait(interval):
        waits.append((calls.await_count, owner._idle.next_delay(interval)))
        if len(waits) == 3:
            owner.stop_claims()

    monkeypatch.setattr(owner._idle, "wait", wait)
    await owner._run()
    assert waits == [(1, 0.02), (2, 0.04), (4, 0.02)]
    assert calls.await_count == 4


@pytest.mark.parametrize("role", ["journal", "report"])
async def test_a_wake_during_claim_is_not_cleared_before_the_idle_wait(monkeypatch, role):
    owner = idle_owner(role)
    entered = asyncio.Event()
    resume = asyncio.Event()
    original_wait = owner._idle.wait
    calls = 0

    async def run_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await resume.wait()
        return 0

    async def wait(interval):
        assert owner._wake.is_set()
        await original_wait(interval)
        assert owner._idle.next_delay(interval) == interval
        owner.stop_claims()

    monkeypatch.setattr(owner, "run_once", run_once)
    monkeypatch.setattr(owner._idle, "wait", wait)
    running = asyncio.create_task(owner._run())
    try:
        await entered.wait()
        owner._wake.set()
        resume.set()
        await running
        assert calls == 1
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("role", ["journal", "report"])
async def test_dependency_failure_uses_existing_failure_backoff_not_idle_backoff(monkeypatch, role):
    owner = idle_owner(role)
    failure = AccountingProtocolUnavailable(AcceptanceFailure.CONNECTION)
    monkeypatch.setattr(owner, "run_once", AsyncMock(side_effect=failure))
    idle = AsyncMock()
    monkeypatch.setattr(owner._idle, "wait", idle)
    waits = []

    async def wait_for(awaitable, timeout):
        awaitable.close()
        waits.append(timeout)
        assert owner.worker_health.state is WorkerState.DEGRADED
        owner.stop_claims()
        raise TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    await owner._run()
    assert not idle.await_count
    assert len(waits) == 1 and 0.02 <= waits[0] <= 0.04
