"""Local admission has one owner, bounded waiting, and synchronous release."""

import asyncio
import inspect

import pytest

from src.billing.accounting.permits.accounting_local_admission import LocalAdmissionOwner
from src.concurrency import CapacityGateFull, CapacityGateTimedOut


def deadline():
    return asyncio.get_running_loop().time() + 1


async def queued(owner):
    entered = asyncio.Event()

    async def wait():
        entered.set()
        await owner.acquire(expires_at=deadline())

    waiter = asyncio.create_task(wait())
    await entered.wait()
    assert owner.waiters == 1
    return waiter


async def test_release_is_synchronous_after_local_issue():
    owner = LocalAdmissionOwner()
    await owner.acquire(expires_at=deadline())
    assert owner.active and not owner.waiters
    assert not inspect.iscoroutinefunction(owner.release)
    owner.release()
    assert not owner.active


async def test_one_waiter_is_bounded_and_cancellation_keeps_the_active_owner():
    owner = LocalAdmissionOwner()
    await owner.acquire(expires_at=deadline())
    waiter = await queued(owner)
    with pytest.raises(CapacityGateFull):
        await owner.acquire(expires_at=deadline())
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert owner.active and not owner.waiters
    owner.release()
    await owner.acquire(expires_at=deadline())
    owner.release()


async def test_timeout_keeps_no_unowned_lock_or_waiter():
    owner = LocalAdmissionOwner()
    await owner.acquire(expires_at=deadline())
    with pytest.raises(CapacityGateTimedOut):
        await owner.acquire(expires_at=asyncio.get_running_loop().time() + 0.01)
    assert owner.active and not owner.waiters
    owner.release()
    await owner.acquire(expires_at=deadline())
    owner.release()


async def test_release_wakes_exactly_one_existing_waiter():
    owner = LocalAdmissionOwner()
    await owner.acquire(expires_at=deadline())
    waiter = await queued(owner)
    owner.release()
    await waiter
    assert owner.active and not owner.waiters
    owner.release()


async def test_cancellation_after_wakeup_keeps_no_lock():
    owner = LocalAdmissionOwner()
    await owner.acquire(expires_at=deadline())
    waiter = await queued(owner)
    owner.release()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not owner.active and not owner.waiters
    await owner.acquire(expires_at=deadline())
    owner.release()


async def test_a_new_caller_cannot_replace_the_woken_waiter():
    owner = LocalAdmissionOwner()
    await owner.acquire(expires_at=deadline())
    waiter = await queued(owner)
    owner.release()
    with pytest.raises(CapacityGateFull):
        await owner.acquire(expires_at=deadline())
    await waiter
    assert owner.active and not owner.waiters
    owner.release()


async def test_zero_waiters_rejects_immediately():
    owner = LocalAdmissionOwner(max_waiters=0)
    await owner.acquire(expires_at=deadline())
    with pytest.raises(CapacityGateFull):
        await owner.acquire(expires_at=deadline())
    assert not owner.waiters
    owner.release()


def test_release_requires_an_active_owner():
    with pytest.raises(RuntimeError):
        LocalAdmissionOwner().release()


@pytest.mark.parametrize("limit", [-1, 2, True, 0.5])
def test_invalid_waiter_capacity_fails_before_acquisition(limit):
    with pytest.raises(ValueError):
        LocalAdmissionOwner(max_waiters=limit)


@pytest.mark.parametrize("expires", [0, float("nan"), float("inf")])
async def test_invalid_deadline_keeps_the_owner_free(expires):
    owner = LocalAdmissionOwner()
    with pytest.raises(CapacityGateTimedOut):
        await owner.acquire(expires_at=expires)
    assert not owner.active and not owner.waiters
