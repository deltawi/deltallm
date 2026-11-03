import asyncio

import pytest

from src.batch.chat_capacity import ChatAttemptCounts, ChatDeploymentCapacity
from src.batch.execution_slots import BatchExecutionSlots
from src.models.errors import ServiceUnavailableError
from src.router.execution import RequestDeadline
from src.router.router import Deployment


def _slots(capacity: int, published: list[tuple[int, int]] | None = None) -> BatchExecutionSlots:
    return BatchExecutionSlots(
        capacity=lambda: capacity,
        on_change=None if published is None else lambda *args: published.append(args),
    )


async def test_units_never_exceed_capacity_across_slices():
    slots = _slots(2)
    release = asyncio.Event()
    active = peak = 0

    async def unit(tracker):
        nonlocal active, peak
        token = slots.bind_slice(tracker)
        try:
            async with slots.unit(items=1):
                active += 1
                peak = max(peak, active)
                await release.wait()
                active -= 1
        finally:
            slots.unbind_slice(token)

    first, second = slots.open_slice(2), slots.open_slice(2)
    tasks = [asyncio.create_task(unit(tracker)) for tracker in (first, first, second, second)]
    while slots.active < 2:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert slots.free == 0
    assert slots.waiting == 2
    release.set()
    await asyncio.gather(*tasks)
    assert peak == 2
    assert slots.active == 0
    assert slots.waiting == 0


async def test_waiting_counts_down_only_for_the_bound_slice():
    slots = _slots(4)
    tracker = slots.open_slice(5)
    other = slots.open_slice(3)
    token = slots.bind_slice(tracker)
    try:
        async with slots.unit(items=2):
            assert tracker.waiting == 3
        async with slots.unit():
            assert tracker.waiting == 3
        slots.settle(1)
        assert tracker.waiting == 2
        slots.set_waiting(0)
    finally:
        slots.unbind_slice(token)
    assert tracker.waiting == 0
    assert other.waiting == 3
    assert slots.waiting == 3
    slots.close_slice(other)
    slots.close_slice(tracker)
    assert slots.open_slices == 0


async def test_unbound_units_still_take_slots():
    slots = _slots(1)
    async with slots.unit(items=3):
        assert slots.active == 1
        assert slots.free == 0
        assert slots.waiting == 0
    assert slots.active == 0


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_a_failed_or_cancelled_unit_returns_its_slot(failure):
    slots = _slots(1)
    with pytest.raises(failure):
        async with slots.unit(items=1):
            raise failure()
    assert slots.active == 0


async def test_a_cancelled_waiter_never_takes_a_slot():
    slots = _slots(1)
    entered = asyncio.Event()

    async def waiter():
        entered.set()
        async with slots.unit(items=1):
            pytest.fail("admitted while the only slot is held")

    async with slots.unit(items=1):
        task = asyncio.create_task(waiter())
        await entered.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert slots.active == 1
    assert slots.active == 0


async def test_saturation_is_published_pod_wide():
    published: list[tuple[int, int]] = []
    slots = _slots(2, published)
    async with slots.unit():
        async with slots.unit():
            assert published[-1] == (2, 2)
    assert published[-1] == (0, 2)


async def test_wait_changed_wakes_on_a_slot_release_and_times_out_quietly():
    slots = _slots(1)
    await slots.wait_changed(timeout=0.01)
    async with slots.unit():
        waiter = asyncio.create_task(slots.wait_changed(timeout=5))
        await asyncio.sleep(0)
    await asyncio.wait_for(waiter, 1)


def _deployment(cap: int = 1) -> Deployment:
    return Deployment(
        "answer",
        "answer",
        {"model": "openai/answer", "chat_batching": {"mode": "concurrent", "max_in_flight": cap}},
    )


async def test_slices_share_one_deployment_allowance():
    counts = ChatAttemptCounts()
    first = ChatDeploymentCapacity(4, counts)
    second = ChatDeploymentCapacity(4, counts)
    target = _deployment(cap=1)

    async with first.slot(target, RequestDeadline.after(1)):
        assert counts.total == 1
        with pytest.raises(ServiceUnavailableError):
            async with second.slot(target, RequestDeadline.after(0.02)):
                pytest.fail("two slices exceeded the deployment's max_in_flight")
    assert counts.total == 0
    assert counts.active == {}
    async with second.slot(target, RequestDeadline.after(1)):
        assert second._total == 1


async def test_slices_share_the_worker_total():
    counts = ChatAttemptCounts()
    first = ChatDeploymentCapacity(1, counts)
    second = ChatDeploymentCapacity(1, counts)
    one = _deployment(cap=4)
    other = Deployment("other", "other", one.deltallm_params)

    async with first.slot(one, RequestDeadline.after(1)):
        with pytest.raises(ServiceUnavailableError):
            async with second.slot(other, RequestDeadline.after(0.02)):
                pytest.fail("two slices exceeded worker_concurrency")
    assert counts.total == 0


async def test_a_released_slot_goes_to_the_longest_waiter():
    slots = _slots(1)
    order: list[str] = []
    release = asyncio.Event()

    async def unit(name: str) -> None:
        async with slots.unit():
            order.append(name)
            await release.wait()

    holder = asyncio.create_task(unit("holder"))
    await asyncio.sleep(0)
    waiters = [asyncio.create_task(unit(name)) for name in ("first", "second", "third")]
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(holder, *waiters)
    assert order == ["holder", "first", "second", "third"]
    assert slots.active == 0


async def test_a_waiter_cancelled_after_its_handoff_returns_the_slot():
    slots = _slots(1)
    entered = asyncio.Event()

    async def waiter():
        entered.set()
        async with slots.unit():
            pytest.fail("a cancelled waiter must not run")

    async with slots.unit():
        task = asyncio.create_task(waiter())
        await entered.wait()
        await asyncio.sleep(0)
    # The slot was handed to the waiter; cancel it before it resumes.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert slots.active == 0
    async with slots.unit():
        assert slots.active == 1


async def test_a_lower_limit_from_another_slice_holds_for_both_until_it_closes():
    counts = ChatAttemptCounts()
    old = ChatDeploymentCapacity(4, counts, shared=True)
    new = ChatDeploymentCapacity(4, counts, shared=True)
    old.register(_deployment(cap=4))
    new.register(_deployment(cap=2))
    target = _deployment(cap=4)

    async with old.slot(target, RequestDeadline.after(1)):
        async with old.slot(target, RequestDeadline.after(1)):
            # The new generation lowered the deployment to 2: the old slice obeys it.
            with pytest.raises(ServiceUnavailableError):
                async with old.slot(target, RequestDeadline.after(0.02)):
                    pytest.fail("exceeded the lowest registered max_in_flight")
            await new.close()
            async with old.slot(target, RequestDeadline.after(1)):
                assert counts.total == 3
    await old.close()
    assert counts.total == 0
    assert counts.limit("answer", 4) == 4


async def test_an_unshared_capacity_never_registers_pod_wide_limits():
    counts = ChatAttemptCounts()
    local = ChatDeploymentCapacity(4, counts)
    local.register(_deployment(cap=1))
    assert counts.limit("answer", 4) == 4
