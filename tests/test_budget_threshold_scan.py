"""A failed bounded page does not hide unknown balances or stop a full pass."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.budget_notifications import (
    BudgetNotificationRepository,
    BudgetThresholdScanUnavailable,
)


def dependencies(monkeypatch):
    rows = [{"organization_id": "org-031", "soft_budget": "0.5", "max_budget": None}]
    db = SimpleNamespace(query_raw=AsyncMock(return_value=rows))
    reads = SimpleNamespace(balances=AsyncMock())
    monkeypatch.setattr(
        "src.db.budget_notifications.AccountingBudgetReadRepository", lambda db: reads
    )
    return db, reads, BudgetNotificationRepository(db)


@pytest.mark.parametrize("failure", [RuntimeError("missing authority"), TimeoutError()])
async def test_balance_failure_returns_known_page_boundary(monkeypatch, failure):
    db, reads, repository = dependencies(monkeypatch)
    reads.balances.side_effect = failure
    with pytest.raises(BudgetThresholdScanUnavailable) as error:
        await repository.enqueue_accounting_thresholds(after="", ttl_seconds=60)
    assert error.value.next_cursor == "org-031"
    db.query_raw.assert_awaited_once()
    assert "LIMIT 32" in db.query_raw.await_args.args[0]


async def test_list_failure_does_not_invent_a_page_boundary(monkeypatch):
    db, reads, repository = dependencies(monkeypatch)
    db.query_raw.side_effect = RuntimeError("list unavailable")
    with pytest.raises(RuntimeError, match="list unavailable"):
        await repository.enqueue_accounting_thresholds(after="org-001", ttl_seconds=60)
    reads.balances.assert_not_awaited()


async def test_cancellation_is_not_a_failed_page(monkeypatch):
    _, reads, repository = dependencies(monkeypatch)
    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    reads.balances.side_effect = blocked
    task = asyncio.create_task(repository.enqueue_accounting_thresholds(after="", ttl_seconds=60))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_empty_page_wraps_without_reading_balances(monkeypatch):
    db, reads, repository = dependencies(monkeypatch)
    db.query_raw.return_value = []
    assert await repository.enqueue_accounting_thresholds(after="org-031", ttl_seconds=60) == ""
    reads.balances.assert_not_awaited()
