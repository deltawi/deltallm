from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.billing.budget_notifications import BudgetNotificationProducer, BudgetNotificationWorker
from src.db.budget_notifications import BudgetNotification


def record():
    return BudgetNotification(
        "org", "notification", Decimal(8), Decimal(7), Decimal(10), "owner", 1, "processing"
    )


def dependencies():
    repo = SimpleNamespace(
        probe=AsyncMock(),
        claim=AsyncMock(return_value=None),
        cleanup=AsyncMock(),
        enqueue=AsyncMock(return_value="queued"),
        begin_dispatch=AsyncMock(return_value=True),
        finish=AsyncMock(),
        retry_preparation=AsyncMock(),
    )
    alerts = SimpleNamespace(
        budget_notifications_enabled=lambda: True,
        prepare_budget_alert=AsyncMock(return_value=object()),
        dispatch_prepared_budget_alert=AsyncMock(return_value=True),
    )
    return repo, alerts


@pytest.mark.asyncio
async def test_acceptance_is_one_durable_call_without_discovery_or_dispatch():
    repo, alerts = dependencies()
    producer = BudgetNotificationProducer(repo, enabled=lambda: True, ttl_seconds=60)
    await producer.send_budget_alert(
        entity_type="org",
        entity_id="org",
        current_spend=Decimal(8),
        soft_budget=Decimal(7),
        hard_budget=Decimal(10),
    )
    repo.enqueue.assert_awaited_once()
    alerts.prepare_budget_alert.assert_not_awaited()
    alerts.dispatch_prepared_budget_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_acceptance_timeout_and_cancellation():
    repo, _ = dependencies()
    entered = asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    repo.enqueue.side_effect = blocked
    producer = BudgetNotificationProducer(repo, enabled=lambda: True, ttl_seconds=60)
    args = dict(
        entity_type="org",
        entity_id="org",
        current_spend=Decimal(8),
        soft_budget=Decimal(7),
        hard_budget=None,
    )
    await asyncio.wait_for(producer.send_budget_alert(**args), timeout=0.5)
    entered.clear()
    task = asyncio.create_task(producer.send_budget_alert(**args))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_worker_requires_durable_dispatch_fence():
    repo, alerts = dependencies()
    repo.begin_dispatch.return_value = False
    await BudgetNotificationWorker(repo, alerts).process(record())
    alerts.prepare_budget_alert.assert_awaited_once()
    alerts.dispatch_prepared_budget_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_preparation_failure_retries_before_external_boundary():
    repo, alerts = dependencies()
    alerts.prepare_budget_alert.side_effect = RuntimeError("db unavailable")
    await BudgetNotificationWorker(repo, alerts).process(record())
    repo.retry_preparation.assert_awaited_once()
    repo.begin_dispatch.assert_not_awaited()
    alerts.dispatch_prepared_budget_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_failure_is_unknown_without_blind_replay():
    repo, alerts = dependencies()
    alerts.dispatch_prepared_budget_alert.side_effect = RuntimeError("transport outcome unknown")
    await BudgetNotificationWorker(repo, alerts).process(record())
    repo.finish.assert_awaited_once_with(record(), delivered=False, outcome="delivery_unknown")
    repo.retry_preparation.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_cleans_the_owned_task():
    repo, alerts = dependencies()
    worker = BudgetNotificationWorker(repo, alerts)
    await worker.start()
    assert worker.worker_health.ready
    await worker.shutdown()
    assert worker.task.done()


@pytest.mark.asyncio
async def test_failed_probe_does_not_start_an_unowned_worker():
    repo, alerts = dependencies()
    repo.probe.side_effect = RuntimeError("schema unavailable")
    worker = BudgetNotificationWorker(repo, alerts)
    with pytest.raises(RuntimeError):
        await worker.start()
    assert worker.task is None
