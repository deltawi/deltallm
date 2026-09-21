"""Health traffic borrows capacity without an unbounded business waiter queue."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.db.allocated_client import AllocatedTransaction, DatabaseOwner, DatabaseUnavailableError
from src.db.allocation_config import DatabasePolicy

pytestmark = pytest.mark.hermetic


def owner(acquisition_seconds=1):
    return DatabaseOwner(DatabasePolicy("telemetry_settlement", 1, acquisition_seconds, 1, 0.1, 2))


async def wait_for_waiter(allocation):
    async with asyncio.timeout(1):
        while allocation.gate.waiters != 1:
            await asyncio.sleep(0)


async def test_settlement_can_wait_for_one_probe_and_normal_saturation_still_sheds():
    allocation = owner()
    entered, release = asyncio.Event(), asyncio.Event()

    async def probe():
        entered.set()
        await release.wait()
        return True

    health = asyncio.create_task(allocation.readiness_query(probe))
    await entered.wait()
    settlement = asyncio.create_task(allocation.query(AsyncMock(return_value="durable")))
    try:
        await wait_for_waiter(allocation)
        with pytest.raises(DatabaseUnavailableError):
            await allocation.query(AsyncMock())
        with pytest.raises(DatabaseUnavailableError):
            await allocation.readiness_query(AsyncMock())
        assert len(allocation.tasks) == 1
        release.set()
        assert await health is True
        assert await settlement == "durable"
        assert allocation.gate.active == allocation.gate.waiters == 0
        await allocation.acquire()
        try:
            with pytest.raises(DatabaseUnavailableError):
                await allocation.query(AsyncMock())
            assert allocation.gate.waiters == 0
        finally:
            await allocation.release()
    finally:
        release.set()
        await asyncio.gather(health, settlement, return_exceptions=True)
        await allocation.close()


async def test_cancelled_probe_retains_its_slot_and_waiters_keep_the_acquisition_deadline():
    allocation = owner(0.01)
    entered, release = asyncio.Event(), asyncio.Event()

    async def probe():
        entered.set()
        await release.wait()

    health = asyncio.create_task(allocation.readiness_query(probe))
    await entered.wait()
    try:
        health.cancel()
        with pytest.raises(asyncio.CancelledError):
            await health
        results = await asyncio.gather(
            *(allocation.query(AsyncMock()) for _ in range(100)), return_exceptions=True
        )
        assert all(isinstance(result, DatabaseUnavailableError) for result in results)
        assert allocation.gate.active == 1
        assert allocation.gate.waiters == 0
        assert len(allocation.tasks) == 1
        with pytest.raises(DatabaseUnavailableError):
            await allocation.readiness_query(AsyncMock())
    finally:
        release.set()
        await allocation.close()
    assert allocation.gate.active == allocation.gate.waiters == 0


async def test_failed_probe_restores_normal_capacity():
    allocation = owner()
    try:
        with pytest.raises(OSError):
            await allocation.readiness_query(AsyncMock(side_effect=OSError("offline")))
        assert allocation.gate.active == allocation.gate.waiters == 0
        assert await allocation.readiness_query(AsyncMock(return_value=True)) is True
    finally:
        await allocation.close()


async def test_waiter_cannot_start_new_work_after_owner_close():
    allocation = owner()
    entered, release = asyncio.Event(), asyncio.Event()

    async def probe():
        entered.set()
        await release.wait()

    health = asyncio.create_task(allocation.readiness_query(probe))
    await entered.wait()
    native = AsyncMock()
    settlement = asyncio.create_task(allocation.query(native))
    await wait_for_waiter(allocation)
    closing = asyncio.create_task(allocation.close())
    try:
        async with asyncio.timeout(1):
            while not allocation.closed:
                await asyncio.sleep(0)
        release.set()
        with pytest.raises(DatabaseUnavailableError):
            await settlement
        await health
        await closing
        native.assert_not_called()
        assert allocation.gate.active == allocation.gate.waiters == 0
    finally:
        release.set()
        await asyncio.gather(health, settlement, closing, return_exceptions=True)


async def test_shorter_transaction_acquisition_budget_also_covers_the_probe_wait():
    allocation = owner()
    entered, release = asyncio.Event(), asyncio.Event()

    async def probe():
        entered.set()
        await release.wait()

    health = asyncio.create_task(allocation.readiness_query(probe))
    await entered.wait()
    native = AsyncMock()
    transaction = AllocatedTransaction(allocation, native, acquisition_seconds=0.01)
    try:
        with pytest.raises(DatabaseUnavailableError):
            await transaction.start()
        native.start.assert_not_called()
        assert allocation.gate.waiters == 0
        assert allocation.gate.active == 1
    finally:
        release.set()
        await health
        await allocation.close()
