"""Pod-local bounded queues and slots return to idle after load (docs/operations/bounded-state.md).

These stores live and drain inside one process: no other pod can do the work
that empties them, and none needs to. So each test overloads one store, mixes
in the failures and cancellations a busy pod sees, and checks it ends idle and
still serves new work, with no restart.
"""

import asyncio

import pytest

from src.batch.execution_slots import BatchExecutionSlots
from src.batch.write_groups import WriteGroups
from src.billing.accounting.durable_microbatch import DurableMicrobatcher

pytestmark = pytest.mark.hermetic


async def _settle(predicate, *, seconds: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    until = loop.time() + seconds
    while not predicate() and loop.time() < until:
        await asyncio.sleep(0.005)


async def test_accounting_microbatch_queue_drains_after_overflow_failure_and_cancel():
    """The reservation and finalization queues shed when full, then empty."""
    release = asyncio.Event()
    calls = 0

    async def handler(values):
        nonlocal calls
        calls += 1
        await release.wait()
        if calls == 1:
            raise RuntimeError("database busy")
        return [value * 2 for value in values]

    queue = DurableMicrobatcher(
        handler,
        max_batch_size=8,
        max_pending=16,
        dwell_seconds=0,
        payload_size=lambda value: 100,
        max_batch_bytes=2 + 8 * 100 + 7,  # eight payloads, list brackets and commas
        # Each entry is charged 4,096 bytes of fixed space plus its payload, so
        # the byte bound (12 entries) is hit before the entry bound (16).
        max_retained_bytes=12 * (4096 + 100),
    )
    queue.start()
    try:
        tasks = [asyncio.create_task(queue.submit(index)) for index in range(64)]
        # Items 0-7 are collected into the first (blocked) batch, 8-11 still
        # wait in the queue, and the byte bound refuses the other 52.
        await _settle(lambda: sum(task.done() for task in tasks) == 52 and queue.pending == 4)
        assert (queue.pending, sum(task.done() for task in tasks)) == (4, 52)
        # Callers that leave before collection give their space back.
        tasks[10].cancel()
        tasks[11].cancel()
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        kinds = [type(result).__name__ for result in results]
        assert (
            kinds
            == ["RuntimeError"] * 8
            + ["int"] * 2
            + ["CancelledError"] * 2
            + ["DurableBatchFull"] * 52
        )
        await _settle(lambda: queue.pending == 0)
        assert (queue.pending, queue.retained_bytes) == (0, 0)
        # Still serving after the overload: nothing stayed stuck.
        assert await queue.submit(21) == 42
        assert (queue.pending, queue.retained_bytes) == (0, 0)
    finally:
        await queue.close()


async def test_batch_write_slots_drain_after_failures_rejections_and_cancels():
    """Checkpoint write, result save and outbox sent-mark slots free every slot.

    All three are the same WriteGroups owner with different limits and writes.
    """
    gate = asyncio.Event()

    async def write(requests):
        await gate.wait()
        if any(request % 7 == 0 for request in requests):
            raise RuntimeError("database capacity is temporarily unavailable")
        # A group holding a multiple of 5 is not accepted: members retry alone.
        return not any(request % 5 == 0 for request in requests)

    groups = WriteGroups(
        kind="accounting_checkpoint",
        limit=2,
        write=write,
        accepted=bool,
        max_rows=8,
        rows=lambda request: 1,
    )
    tasks = [asyncio.create_task(groups.run(index)) for index in range(1, 61)]
    await _settle(lambda: groups.active == 2 and groups.waiting == 58)
    assert (groups.active, groups.waiting) == (2, 58)
    for task in tasks[30:34]:
        task.cancel()
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert any(result is True for result in results)
    assert any(isinstance(result, RuntimeError) for result in results)
    await _settle(lambda: groups.active == 0 and not groups._groups)
    assert (groups.active, groups.waiting, len(groups._groups)) == (0, 0, 0)
    # The next write finds a free slot at once.
    assert await asyncio.wait_for(groups.run(1), timeout=1) is True
    assert (groups.active, groups.waiting) == (0, 0)


async def test_batch_execution_slots_drain_after_failed_and_cancelled_units():
    """Pod-wide execution slots and slice trackers return to zero."""
    slots = BatchExecutionSlots(capacity=lambda: 4)
    gate = asyncio.Event()

    async def unit(index: int) -> None:
        async with slots.unit(items=1):
            await gate.wait()
            if index % 6 == 0:
                raise RuntimeError("provider failed")

    async def slice_(items: int, offset: int) -> list[object]:
        tracker = slots.open_slice(items)
        token = slots.bind_slice(tracker)
        try:
            return await asyncio.gather(
                *(unit(offset + index) for index in range(items)), return_exceptions=True
            )
        finally:
            slots.unbind_slice(token)
            slots.close_slice(tracker)

    slices = [asyncio.create_task(slice_(10, offset * 10)) for offset in range(3)]
    await _settle(lambda: slots.active == 4)
    assert (slots.active, slots.open_slices) == (4, 3)
    slices[2].cancel()
    gate.set()
    await asyncio.gather(*slices, return_exceptions=True)
    assert (slots.active, slots.waiting, slots.open_slices, len(slots._waiters)) == (0, 0, 0, 0)
    async with slots.unit():
        assert slots.active == 1
    assert slots.active == 0
