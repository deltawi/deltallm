import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.batch.chat_capacity import ChatDeploymentCapacity
from src.batch.chat_lease_lifecycle import (
    ChatItemLeaseWatch,
    ClaimedChatAttemptCapacity,
    stop_chat_watches,
)
from src.batch.worker_types import BatchItemLeaseLostError
from src.batch.repositories.item_repository import BatchItemRepository, RENEW_ITEM_LEASE_SQL
from src.models.errors import ServiceUnavailableError
from src.router.attempt_capacity import bind_attempt_capacity
from src.router.execution import RequestDeadline, get_failover_original_error
from tests.batch.test_chat_capacity import deployment
from tests.batch.test_chat_capacity_routing import manager_for


async def stop_task(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
async def watch():
    task = asyncio.create_task(asyncio.Event().wait())
    watch = ChatItemLeaseWatch(task, asyncio.Event(), stop_task)
    yield watch
    await watch.stop()


@pytest.mark.parametrize("outcome", [False, "unavailable", "expired"])
async def test_dispatch_guard_failure_is_local_and_never_acquires_provider(
    watch, monkeypatch, outcome
):
    target = deployment()
    manager, state = manager_for(target)
    capacity = ChatDeploymentCapacity(1)
    renew = AsyncMock(
        return_value=False, side_effect=TimeoutError() if outcome == "unavailable" else None
    )
    if outcome == "expired":
        watch.lost.set()
    context = {}
    bind_attempt_capacity(context, ClaimedChatAttemptCapacity(capacity, watch, renew))
    provider, latency = AsyncMock(), AsyncMock()
    acquire = AsyncMock(wraps=manager.candidate_planner.acquire_attempt)
    monkeypatch.setattr(manager.candidate_planner, "acquire_attempt", acquire)
    monkeypatch.setattr(manager, "_record_attempt_latency", latency)
    with pytest.raises(ServiceUnavailableError) as error:
        await manager.execute_with_failover(target, "first", provider, routing_context=context)
    assert isinstance(get_failover_original_error(error.value), BatchItemLeaseLostError)
    assert error.value.affects_deployment_health is False
    provider.assert_not_awaited()
    acquire.assert_not_awaited()
    latency.assert_not_awaited()
    assert capacity._total == 0 and watch.lost.is_set()
    assert not await state.is_cooled_down(target.health_ref)


async def test_guard_checks_after_wait_and_releases_without_stealing_other_slot(watch):
    target = deployment()
    capacity = ChatDeploymentCapacity(1)
    renew, entered = AsyncMock(return_value=False), asyncio.Event()
    guard = ClaimedChatAttemptCapacity(capacity, watch, renew)

    async def attempt():
        entered.set()
        async with guard.slot(target, RequestDeadline.after(1)):
            pytest.fail("stale item reached provider")

    async with capacity.slot(target, RequestDeadline.after(1)):
        task = asyncio.create_task(attempt())
        await entered.wait()
        renew.assert_not_awaited()
        assert capacity._total == 1
    with pytest.raises(BatchItemLeaseLostError):
        await task
    renew.assert_awaited_once()
    assert capacity._total == 0


async def test_watch_cleanup_attempts_all_callbacks_when_one_fails():
    tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]

    async def broken_stop(task):
        await stop_task(task)
        raise RuntimeError("stop failed")

    watches = [
        ChatItemLeaseWatch(tasks[0], asyncio.Event(), stop_task),
        ChatItemLeaseWatch(tasks[1], asyncio.Event(), broken_stop),
    ]
    with pytest.raises(RuntimeError, match="stop failed"):
        await stop_chat_watches(watches)
    assert all(task.done() for task in tasks)
    await stop_chat_watches(watches)


async def test_cancellation_during_dispatch_renewal_releases_slot(watch):
    entered = asyncio.Event()
    capacity = ChatDeploymentCapacity(1)

    async def renew(_):
        entered.set()
        await asyncio.Event().wait()

    async def attempt():
        async with ClaimedChatAttemptCapacity(capacity, watch, renew).slot(
            deployment(), RequestDeadline.after(1)
        ):
            pytest.fail("cancelled renewal dispatched")

    task = asyncio.create_task(attempt())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert capacity._total == 0


@pytest.mark.parametrize("bounded", [False, True])
async def test_renewal_statement_budget_and_transaction_closed_before_dispatch(bounded):
    events = []
    query = AsyncMock(return_value=[{"item_id": "item"}])

    @asynccontextmanager
    async def transaction(**kwargs):
        assert timedelta(0) < kwargs["timeout"] <= timedelta(milliseconds=250)
        assert kwargs["timeout"] == kwargs["max_wait"]
        events.append("begin")
        try:
            yield SimpleNamespace(query_raw=query)
        finally:
            events.append("end")

    repository = BatchItemRepository(SimpleNamespace(query_raw=query, tx=transaction))
    assert await repository.renew_item_lease(
        item_id="item",
        worker_id="worker",
        claim_epoch=7,
        lease_seconds=30,
        expires_at=asyncio.get_running_loop().time() + 1 if bounded else None,
    )
    assert query.await_count == (2 if bounded else 1)
    assert events == (["begin", "end"] if bounded else [])
    if bounded:
        assert "statement_timeout" in query.await_args_list[0].args[0]
        assert "lock_timeout" in query.await_args_list[0].args[0]
    assert query.await_args.args == (RENEW_ITEM_LEASE_SQL, "item", "worker", 30, 7)


async def test_cancelled_renewal_closes_transaction():
    entered, exited = asyncio.Event(), asyncio.Event()

    async def query(sql, *args):
        if sql == RENEW_ITEM_LEASE_SQL:
            entered.set()
            await asyncio.Event().wait()
        return []

    @asynccontextmanager
    async def transaction(**kwargs):
        try:
            yield SimpleNamespace(query_raw=query)
        finally:
            exited.set()

    repository = BatchItemRepository(SimpleNamespace(tx=transaction))
    task = asyncio.create_task(
        repository.renew_item_lease(
            item_id="item",
            worker_id="worker",
            claim_epoch=7,
            lease_seconds=30,
            expires_at=asyncio.get_running_loop().time() + 1,
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()
