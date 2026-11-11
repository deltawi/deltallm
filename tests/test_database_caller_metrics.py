import asyncio
from unittest.mock import AsyncMock

import pytest
from prisma import Prisma

from src.db.runtime.allocated_client import (
    AllocatedPrisma,
    AllocatedTransaction,
    DatabaseOwner,
    DatabaseUnavailableError,
)
from src.db.runtime.allocation_config import DatabasePolicy
from src.database_caller import (
    current_database_caller,
    database_caller,
    with_database_caller,
)
from src.metrics.prometheus import get_prometheus_registry

pytestmark = pytest.mark.hermetic

_registry = get_prometheus_registry()


def owner(allocation: str, *, size=1, statement=1.0, queue=0.0):
    # Each test uses its own allocation name, so counts start at zero.
    return DatabaseOwner(DatabasePolicy(allocation, size, 0.01, statement, 0.05, 2, queue))


def sample(name: str, **labels: str) -> float:
    return _registry.get_sample_value(name, labels) or 0.0


def caller_event(allocation: str, caller: str, outcome: str) -> float:
    return sample(
        "deltallm_database_caller_events_total",
        allocation=allocation,
        caller=caller,
        outcome=outcome,
    )


async def test_query_counts_slot_use_for_the_marked_caller():
    allocation = owner("caller_query")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def native():
        entered.set()
        await finish.wait()
        return "saved"

    with database_caller("batch_result_save"):
        query = asyncio.create_task(allocation.query(native))
    await entered.wait()
    assert (
        sample(
            "deltallm_database_caller_slots", allocation="caller_query", caller="batch_result_save"
        )
        == 1
    )
    finish.set()
    assert await query == "saved"

    assert (
        sample(
            "deltallm_database_caller_slots", allocation="caller_query", caller="batch_result_save"
        )
        == 0
    )
    assert caller_event("caller_query", "batch_result_save", "acquired") == 1
    assert caller_event("caller_query", "batch_result_save", "completed") == 1
    assert caller_event("caller_query", "other", "acquired") == 0
    assert (
        sample(
            "deltallm_database_caller_hold_seconds_count",
            allocation="caller_query",
            caller="batch_result_save",
        )
        == 1
    )
    assert (
        sample(
            "deltallm_database_caller_wait_seconds_count",
            allocation="caller_query",
            caller="batch_result_save",
        )
        == 1
    )
    await allocation.close()


async def test_unmarked_work_counts_as_other():
    allocation = owner("caller_default")
    assert current_database_caller() == "other"
    assert await allocation.query(AsyncMock(return_value="ok")) == "ok"
    assert caller_event("caller_default", "other", "completed") == 1
    await allocation.close()


async def test_refused_slot_counts_for_the_caller_that_was_refused():
    allocation = owner("caller_full")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await finish.wait()

    with database_caller("batch_outbox"):
        holder = asyncio.create_task(allocation.query(blocked))
    await entered.wait()
    with database_caller("batch_checkpoint"), pytest.raises(DatabaseUnavailableError):
        await allocation.query(AsyncMock())
    assert caller_event("caller_full", "batch_checkpoint", "full") == 1
    assert caller_event("caller_full", "batch_outbox", "full") == 0
    finish.set()
    await holder
    await allocation.close()


async def test_waiters_gauge_follows_queued_callers():
    allocation = owner("caller_waiters", queue=1.0)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await finish.wait()

    holder = asyncio.create_task(allocation.query(blocked))
    await entered.wait()
    waiter = asyncio.create_task(allocation.query(AsyncMock(return_value="queued")))
    await asyncio.sleep(0.02)
    assert sample("deltallm_database_allocation_waiters", allocation="caller_waiters") == 1
    finish.set()
    await holder
    assert await waiter == "queued"
    assert sample("deltallm_database_allocation_waiters", allocation="caller_waiters") == 0
    await allocation.close()


async def test_abandoned_query_is_counted_until_native_work_ends():
    allocation = owner("caller_abandoned", statement=0.02)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await finish.wait()

    with database_caller("batch_lease"), pytest.raises(DatabaseUnavailableError):
        await allocation.query(slow)
    assert caller_event("caller_abandoned", "batch_lease", "caller_deadline") == 1
    assert sample("deltallm_database_allocation_abandoned", allocation="caller_abandoned") == 1
    # The slot is still held, and still charged to the caller that took it.
    assert (
        sample(
            "deltallm_database_caller_slots", allocation="caller_abandoned", caller="batch_lease"
        )
        == 1
    )
    finish.set()
    await asyncio.gather(*tuple(allocation.tasks))
    assert sample("deltallm_database_allocation_abandoned", allocation="caller_abandoned") == 0
    assert (
        sample(
            "deltallm_database_caller_slots", allocation="caller_abandoned", caller="batch_lease"
        )
        == 0
    )
    await allocation.close()


async def test_transaction_slot_is_charged_to_its_caller_until_commit(monkeypatch):
    allocation = owner("caller_transaction")
    native_client = AllocatedPrisma(allocation=allocation)
    native_client._tx_id = "caller-transaction"
    native = AsyncMock()
    native.start = AsyncMock(return_value=native_client)
    monkeypatch.setattr(Prisma, "_execute", AsyncMock(return_value={}))

    with database_caller("batch_result_save"):
        async with AllocatedTransaction(allocation, native) as tx:
            # A statement inside the transaction uses the transaction's slot.
            with database_caller("batch_progress"):
                await tx._execute(method="query_raw", arguments={})
            assert (
                sample(
                    "deltallm_database_caller_slots",
                    allocation="caller_transaction",
                    caller="batch_result_save",
                )
                == 1
            )
    native.commit.assert_awaited_once()
    assert (
        sample(
            "deltallm_database_caller_slots",
            allocation="caller_transaction",
            caller="batch_result_save",
        )
        == 0
    )
    assert caller_event("caller_transaction", "batch_progress", "acquired") == 0
    assert (
        sample(
            "deltallm_database_caller_hold_seconds_count",
            allocation="caller_transaction",
            caller="batch_result_save",
        )
        == 1
    )
    await allocation.close()


async def test_abandoned_transaction_start_is_counted_until_rollback():
    allocation = owner("caller_tx_abandoned")
    native_client = AllocatedPrisma(allocation=allocation)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def start():
        entered.set()
        await finish.wait()
        return native_client

    native = AsyncMock()
    native.start = AsyncMock(side_effect=start)
    transaction = AllocatedTransaction(allocation, native)
    caller = asyncio.create_task(transaction.start())
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert sample("deltallm_database_allocation_abandoned", allocation="caller_tx_abandoned") == 1
    finish.set()
    await allocation.close()
    native.rollback.assert_awaited_once()
    assert sample("deltallm_database_allocation_abandoned", allocation="caller_tx_abandoned") == 0
    assert allocation.gate.active == 0


def _transaction_native(allocation, name):
    native_client = AllocatedPrisma(allocation=allocation)
    native_client._tx_id = name
    native = AsyncMock()
    native.start = AsyncMock(return_value=native_client)
    return native


async def test_transaction_results_are_counted_once_per_caller(monkeypatch):
    allocation = owner("caller_tx_results")
    execute = AsyncMock(return_value={})
    monkeypatch.setattr(Prisma, "_execute", execute)

    with database_caller("spend"):
        async with AllocatedTransaction(
            allocation, _transaction_native(allocation, "committed")
        ) as tx:
            await tx._execute(method="query_raw", arguments={})

        execute.side_effect = RuntimeError("constraint")
        with pytest.raises(RuntimeError):
            async with AllocatedTransaction(
                allocation, _transaction_native(allocation, "failed")
            ) as tx:
                await tx._execute(method="query_raw", arguments={})

        execute.side_effect = None
        with pytest.raises(LookupError):
            async with AllocatedTransaction(
                allocation, _transaction_native(allocation, "rolled-back")
            ) as tx:
                await tx._execute(method="query_raw", arguments={})
                raise LookupError("application rollback")

    assert caller_event("caller_tx_results", "spend", "acquired") == 3
    assert caller_event("caller_tx_results", "spend", "completed") == 1
    assert caller_event("caller_tx_results", "spend", "error") == 1
    assert caller_event("caller_tx_results", "spend", "rolled_back") == 1
    assert allocation.gate.active == 0
    await allocation.close()


async def test_caller_that_stops_waiting_for_commit_leaves_an_abandoned_slot(monkeypatch):
    allocation = owner("caller_tx_commit_wait")
    monkeypatch.setattr(Prisma, "_execute", AsyncMock(return_value={}))
    native = _transaction_native(allocation, "slow-commit")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def commit():
        entered.set()
        await finish.wait()

    native.commit = AsyncMock(side_effect=commit)
    with database_caller("audit"):
        with pytest.raises(DatabaseUnavailableError):
            async with AllocatedTransaction(allocation, native, transaction_seconds=0.1) as tx:
                await tx._execute(method="query_raw", arguments={})
    # The caller's deadline expired during COMMIT; the native commit still runs.
    await entered.wait()
    assert allocation.gate.active == 1
    assert sample("deltallm_database_allocation_abandoned", allocation="caller_tx_commit_wait") == 1
    assert caller_event("caller_tx_commit_wait", "audit", "caller_deadline") == 1
    finish.set()
    async with asyncio.timeout(1):
        while allocation.gate.active:
            await asyncio.sleep(0)
    assert sample("deltallm_database_allocation_abandoned", allocation="caller_tx_commit_wait") == 0
    assert caller_event("caller_tx_commit_wait", "audit", "completed") == 1
    await allocation.close()


async def test_marks_nest_and_follow_started_tasks():
    seen: list[str] = []

    @with_database_caller("batch_requeue")
    async def requeue() -> None:
        seen.append(current_database_caller())

    async def child() -> None:
        seen.append(current_database_caller())

    with database_caller("batch_item"):
        await asyncio.create_task(child())
        await requeue()
        seen.append(current_database_caller())
    seen.append(current_database_caller())
    assert seen == ["batch_item", "batch_requeue", "batch_item", "other"]
    assert requeue.__name__ == "requeue"


def test_unknown_caller_is_refused():
    with pytest.raises(ValueError):
        with database_caller("tenant-42"):  # type: ignore[arg-type]
            pass
    with pytest.raises(ValueError):
        with_database_caller("tenant-42")  # type: ignore[arg-type]
