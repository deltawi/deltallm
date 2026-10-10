"""Empty consumers use bounded polling and do not lose durable local wakeups."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.billing.spend.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.services.audit.audit_service import AuditIngestionConfig, AuditService
from src.telemetry.worker_idle import IdleWorkerPoll
from tests.services.test_audit_service import FakeAuditRepository
from tests.test_spend_ingestion import _OutboxDB, _Writer


@pytest.mark.parametrize("interval", [0.01, 0.1, 1.0, 2.0])
def test_idle_delay_is_bounded_and_resets_without_shortening_the_configured_interval(interval):
    poll = IdleWorkerPoll(asyncio.Event())
    delays = [poll.next_delay(interval) for _ in range(20)]
    assert delays[0] == interval
    assert all(interval <= delay <= max(1.0, interval) for delay in delays)
    assert delays[-1] == max(1.0, interval)
    poll.reset()
    assert poll.next_delay(interval) == interval


def test_one_minute_of_idle_polling_uses_at_most_64_claims_at_the_default_interval():
    poll = IdleWorkerPoll(asyncio.Event())
    elapsed, claims = 0.0, 0
    while elapsed < 60:
        elapsed += poll.next_delay(0.1)
        claims += 1
    assert claims <= 64


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_intervals_are_rejected(interval):
    with pytest.raises(ValueError):
        IdleWorkerPoll(asyncio.Event()).next_delay(interval)


async def test_wakeup_during_claim_is_retained_and_resets_idle_delay():
    wake = asyncio.Event()
    poll = IdleWorkerPoll(wake)
    for _ in range(10):
        poll.next_delay(0.1)
    wake.set()
    poll.begin_claim()
    assert not wake.is_set()
    wake.set()
    await poll.wait(0.1)
    assert poll.next_delay(0.1) == 0.1


async def test_cross_process_work_is_checked_again_within_the_idle_cap(monkeypatch):
    poll = IdleWorkerPoll(asyncio.Event())
    delays = []

    async def expire(waiter, *, timeout):
        delays.append(timeout)
        waiter.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", expire)
    for _ in range(12):
        poll.begin_claim()
        await poll.wait(0.1)
    assert delays == [0.1, 0.2, 0.4, 0.8] + [1.0] * 8


async def test_cancellation_is_not_hidden_or_retried(monkeypatch):
    async def cancel(waiter, *, timeout):
        waiter.close()
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "wait_for", cancel)
    with pytest.raises(asyncio.CancelledError):
        await IdleWorkerPoll(asyncio.Event()).wait(0.1)


def service(kind, *, workers=False):
    if kind == "spend":
        return SpendIngestionService(
            db_client=_OutboxDB() if workers else None,
            writer=_Writer(),
            config=SpendIngestionConfig(enabled=True, worker_enabled=workers),
        )
    return AuditService(
        FakeAuditRepository(),
        db_client=_OutboxDB() if workers else None,
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=workers),
    )


async def iteration(owner):
    if isinstance(owner, SpendIngestionService):
        await owner._worker_iteration()
    else:
        await owner._durable_worker_iteration()


@pytest.mark.parametrize("kind", ["spend", "audit"])
async def test_existing_worker_does_not_clear_a_wakeup_received_during_claim(monkeypatch, kind):
    owner = service(kind)

    async def claim(**kwargs):
        owner._wake.set()
        return []

    async def require_visible_signal(waiter, *, timeout):
        try:
            assert owner._wake.is_set(), "worker lost a wakeup during its claim"
            await waiter
        finally:
            waiter.close()

    if isinstance(owner, SpendIngestionService):
        monkeypatch.setattr(owner, "_claim_batch", claim)
    else:
        monkeypatch.setattr(owner.ingestion_repository, "claim_batch", claim)
        monkeypatch.setattr(owner, "_publish_durable_backlog", AsyncMock())
    monkeypatch.setattr(asyncio, "wait_for", require_visible_signal)
    await iteration(owner)
    assert owner._idle_poll.next_delay(0.1) == 0.1


@pytest.mark.parametrize("kind", ["spend", "audit"])
async def test_existing_busy_worker_resets_idle_delay_without_waiting(monkeypatch, kind):
    owner = service(kind)
    for _ in range(10):
        owner._idle_poll.next_delay(0.1)
    records = [object()]
    process = AsyncMock()
    if isinstance(owner, SpendIngestionService):
        monkeypatch.setattr(owner, "_claim_batch", AsyncMock(return_value=records))
        monkeypatch.setattr(owner, "_process_batch", process)
        monkeypatch.setattr(owner, "_publish_backlog", AsyncMock())
    else:
        monkeypatch.setattr(
            owner.ingestion_repository, "claim_batch", AsyncMock(return_value=records)
        )
        monkeypatch.setattr(owner, "_process_durable_batch", process)
        monkeypatch.setattr(owner, "_publish_durable_backlog", AsyncMock())
    await iteration(owner)
    process.assert_awaited_once_with(records)
    assert owner._idle_poll.next_delay(0.1) == 0.1


@pytest.mark.parametrize("kind", ["spend", "audit"])
async def test_reconfiguration_resets_delay_and_interrupts_an_idle_wait(kind):
    owner = service(kind)
    for _ in range(10):
        owner._idle_poll.next_delay(0.1)
    config = owner.config if isinstance(owner, SpendIngestionService) else owner.ingestion_config
    await owner.reconfigure(replace(config, flush_interval_seconds=0.01))
    assert owner._wake.is_set()
    assert owner._idle_poll.next_delay(0.01) == 0.01


@pytest.mark.parametrize("kind", ["spend", "audit"])
async def test_startup_resets_idle_delay_before_the_owned_workers_run(monkeypatch, kind):
    owner = service(kind, workers=True)
    for _ in range(10):
        owner._idle_poll.next_delay(0.1)
    original = owner._idle_poll.reset
    previous_delays = []

    def reset():
        previous_delays.append(owner._idle_poll._delay)
        original()

    monkeypatch.setattr(owner._idle_poll, "reset", reset)
    repository = (
        owner.worker_repository
        if isinstance(owner, SpendIngestionService)
        else owner.worker_ingestion_repository
    )
    monkeypatch.setattr(repository, "reconcile_capacity", AsyncMock())
    monkeypatch.setattr(repository, "claim_batch", AsyncMock(return_value=[]))
    monkeypatch.setattr(repository, "pending_stats", AsyncMock(return_value=(0, 0)))
    try:
        await owner.start()
        assert owner.worker_health.ready
        assert previous_delays[0] == 1.0
    finally:
        await owner.shutdown()
