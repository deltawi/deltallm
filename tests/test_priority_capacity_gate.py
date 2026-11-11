import asyncio

import pytest

from src.concurrency import (
    CapacityGateDisplaced,
    CapacityGateFull,
    CapacityGateTimedOut,
    PriorityCapacityGate,
)
from src.database_caller import (
    BACKGROUND_PRIORITY,
    BILLING_PRIORITY,
    CLAIM_PRIORITY,
    OUTBOX_PRIORITY,
    PAID_RESULT_PRIORITY,
    REQUEST_PRIORITY,
    background_slots,
    caller_priority,
    database_caller,
)
from src.db.runtime.allocated_client import DatabaseOwner, DatabaseUnavailableError
from src.db.runtime.allocation_config import DatabasePolicy

pytestmark = pytest.mark.hermetic


def gate(concurrency=1, max_waiters=8, capped_slots=1):
    return PriorityCapacityGate(
        concurrency=concurrency, max_waiters=max_waiters, capped_slots=capped_slots
    )


async def _queued(gate: PriorityCapacityGate, waiters: int) -> None:
    for _ in range(100):
        if gate.waiters == waiters:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"expected {waiters} waiters, got {gate.waiters}")


async def test_free_slot_goes_to_the_most_urgent_waiter_then_in_arrival_order():
    capacity = gate()
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    order: list[str] = []

    async def wait(name: str, priority: int) -> None:
        await capacity.acquire(timeout_seconds=1, priority=priority)
        order.append(name)
        await capacity.release()

    tasks = [
        asyncio.create_task(wait("claim-1", CLAIM_PRIORITY)),
        asyncio.create_task(wait("save-1", PAID_RESULT_PRIORITY)),
        asyncio.create_task(wait("outbox", OUTBOX_PRIORITY)),
        asyncio.create_task(wait("save-2", PAID_RESULT_PRIORITY)),
        asyncio.create_task(wait("lease", BILLING_PRIORITY)),
    ]
    await _queued(capacity, 5)
    await capacity.release()
    await asyncio.gather(*tasks)

    assert order == ["save-1", "save-2", "lease", "outbox", "claim-1"]
    assert capacity.active == 0 and capacity.waiters == 0


async def test_released_slot_is_handed_over_so_a_new_caller_cannot_take_it_first():
    capacity = gate()
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    waiting = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY))
    await _queued(capacity, 1)

    await capacity.release()
    # The slot already belongs to the waiter, before its task runs again.
    assert capacity.active == 1
    with pytest.raises(CapacityGateTimedOut):
        await capacity.acquire(timeout_seconds=0.01, priority=REQUEST_PRIORITY)
    await waiting
    assert capacity.active == 1


async def test_urgent_caller_takes_the_position_of_the_newest_least_urgent_waiter():
    capacity = gate(max_waiters=2)
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    older = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY))
    newer = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY))
    await _queued(capacity, 2)

    save = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=PAID_RESULT_PRIORITY))
    with pytest.raises(CapacityGateDisplaced):
        await newer
    assert capacity.waiters == 2

    await capacity.release()
    await save
    await capacity.release()
    await older
    await capacity.release()
    assert capacity.active == 0 and capacity.waiters == 0


async def test_full_queue_of_equally_or_more_urgent_waiters_refuses_at_once():
    capacity = gate(max_waiters=1)
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    save = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=PAID_RESULT_PRIORITY))
    await _queued(capacity, 1)

    for priority in (PAID_RESULT_PRIORITY, BILLING_PRIORITY, CLAIM_PRIORITY):
        with pytest.raises(CapacityGateFull) as refused:
            await capacity.acquire(timeout_seconds=1, priority=priority)
        assert not isinstance(refused.value, CapacityGateDisplaced)

    await capacity.release()
    await save
    await capacity.release()


async def test_capped_work_holds_at_most_its_slots_and_lets_other_work_pass():
    capacity = gate(concurrency=3, capped_slots=1)
    await capacity.acquire(timeout_seconds=1, priority=BACKGROUND_PRIORITY, capped=True)
    second = asyncio.create_task(
        capacity.acquire(timeout_seconds=1, priority=BACKGROUND_PRIORITY, capped=True)
    )
    await _queued(capacity, 1)
    assert capacity.active == 1 and capacity.capped_active == 1

    # Free slots stay usable for other work while the capped waiter waits.
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    assert capacity.active == 3 and capacity.waiters == 1

    # An uncapped slot frees: the capped waiter is still at its cap.
    await capacity.release()
    await asyncio.sleep(0)
    assert not second.done() and capacity.active == 2

    await capacity.release(capped=True)
    await second
    assert capacity.capped_active == 1 and capacity.active == 2
    await capacity.release(capped=True)
    await capacity.release()
    assert capacity.active == 0 and capacity.capped_active == 0


async def test_uncapped_waiter_is_served_when_the_most_urgent_waiter_is_capped():
    capacity = gate(concurrency=2, capped_slots=1)
    await capacity.acquire(timeout_seconds=1, priority=REQUEST_PRIORITY, capped=True)
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    capped = asyncio.create_task(
        capacity.acquire(timeout_seconds=1, priority=REQUEST_PRIORITY, capped=True)
    )
    claim = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY))
    await _queued(capacity, 2)

    await capacity.release()
    await claim
    assert not capped.done()
    await capacity.release(capped=True)
    await capped
    await capacity.release(capped=True)
    await capacity.release()


async def test_timed_out_and_cancelled_waiters_leave_no_position_or_slot_behind():
    capacity = gate()
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    with pytest.raises(CapacityGateTimedOut):
        await capacity.acquire(timeout_seconds=0.01, priority=PAID_RESULT_PRIORITY)
    cancelled = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY))
    await _queued(capacity, 1)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert capacity.waiters == 0

    await capacity.release()
    assert capacity.active == 0
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    await capacity.release()


async def test_slot_granted_to_a_cancelled_waiter_is_handed_on():
    capacity = gate()
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    first = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=PAID_RESULT_PRIORITY))
    second = asyncio.create_task(capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY))
    await _queued(capacity, 2)

    await capacity.release()  # Granted to the first waiter.
    first.cancel()  # Cancelled before its task ran again.
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert capacity.active == 1
    await capacity.release()
    assert capacity.active == 0


async def test_slot_granted_together_with_the_deadline_is_kept():
    capacity = gate()
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    loop = asyncio.get_running_loop()
    waiter = asyncio.create_task(capacity.acquire(timeout_seconds=0.05, priority=CLAIM_PRIORITY))
    await _queued(capacity, 1)
    # Grant the slot in the same loop step as the deadline.
    loop.call_later(0.05, lambda: loop.create_task(capacity.release()))
    try:
        await waiter
    except CapacityGateTimedOut:
        # The deadline came first: nothing is held for the waiter.
        assert capacity.active == 0
    else:
        assert capacity.active == 1
        await capacity.release()
    assert capacity.waiters == 0


async def test_release_without_ownership_is_refused():
    capacity = gate()
    with pytest.raises(RuntimeError):
        await capacity.release()
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    with pytest.raises(RuntimeError):
        await capacity.release(capped=True)
    await capacity.release()


async def test_many_withdrawn_waiters_do_not_grow_the_queue_without_bound():
    capacity = gate(max_waiters=4)
    await capacity.acquire(timeout_seconds=1, priority=CLAIM_PRIORITY)
    for _ in range(200):
        with pytest.raises(CapacityGateTimedOut):
            await capacity.acquire(timeout_seconds=0.001, priority=CLAIM_PRIORITY)
    assert capacity.waiters == 0
    assert len(capacity._queue) <= 32
    await capacity.release()


def test_caller_priorities_put_paid_work_before_claims_and_cap_background_work():
    assert caller_priority("auth") == REQUEST_PRIORITY
    assert caller_priority("batch_result_save") == PAID_RESULT_PRIORITY
    assert caller_priority("batch_requeue") == PAID_RESULT_PRIORITY
    assert caller_priority("batch_checkpoint") == BILLING_PRIORITY
    assert caller_priority("batch_lease") == BILLING_PRIORITY
    assert caller_priority("batch_outbox") == OUTBOX_PRIORITY
    for caller in ("batch_claim", "batch_item", "batch_progress", "batch_finalize", "other"):
        assert caller_priority(caller) == CLAIM_PRIORITY
    for caller in (
        "batch_webhook",
        "batch_maintenance",
        "cache_invalidation",
        "organization_deletion",
    ):
        assert caller_priority(caller) == BACKGROUND_PRIORITY
    assert [background_slots(size) for size in (1, 8, 16, 28, 40, 100)] == [1, 1, 2, 3, 4, 4]


def _batch_owner(size=1, queue_seconds=1.0):
    return DatabaseOwner(
        DatabasePolicy("control", size, 0.05, 1, 0.05, 2, queue_seconds), prioritized=True
    )


async def _hold(owner: DatabaseOwner, caller: str, release: asyncio.Event, order: list[str]):
    with database_caller(caller):

        async def operation():
            order.append(caller)
            await release.wait()

        await owner.query(operation)


async def test_prioritized_owner_serves_the_result_save_before_queued_claims():
    owner = _batch_owner(size=2)
    release = asyncio.Event()
    order: list[str] = []
    holders = [asyncio.create_task(_hold(owner, "batch_claim", release, order)) for _ in range(2)]
    await asyncio.sleep(0)
    claims = [asyncio.create_task(_hold(owner, "batch_claim", release, order)) for _ in range(3)]
    await _queued(owner.gate, 3)
    save = asyncio.create_task(_hold(owner, "batch_result_save", release, order))
    await _queued(owner.gate, 4)

    release.set()
    await asyncio.gather(*holders, save, *claims)

    assert order[:3] == ["batch_claim", "batch_claim", "batch_result_save"]
    assert order[3:] == ["batch_claim"] * 3
    assert owner.gate.active == 0
    await owner.close()


async def test_prioritized_owner_refuses_displaced_claims_and_keeps_paid_work():
    owner = _batch_owner()
    assert owner.gate.max_waiters == 2
    release = asyncio.Event()
    order: list[str] = []
    holder = asyncio.create_task(_hold(owner, "batch_claim", release, order))
    await asyncio.sleep(0)
    claims = [asyncio.create_task(_hold(owner, "batch_claim", release, order)) for _ in range(2)]
    await _queued(owner.gate, 2)
    paid = [
        asyncio.create_task(_hold(owner, caller, release, order))
        for caller in ("batch_result_save", "batch_checkpoint")
    ]
    for claim in claims:
        with pytest.raises(DatabaseUnavailableError):
            await claim

    release.set()
    await asyncio.gather(holder, *paid)
    assert order == ["batch_claim", "batch_result_save", "batch_checkpoint"]
    await owner.close()


async def test_queue_is_deep_counts_only_claimed_work_waiting_for_a_slot():
    owner = _batch_owner(size=4)
    release = asyncio.Event()
    order: list[str] = []
    holders = [asyncio.create_task(_hold(owner, "batch_claim", release, order)) for _ in range(4)]
    await asyncio.sleep(0)
    background = asyncio.create_task(_hold(owner, "batch_maintenance", release, order))
    claim = asyncio.create_task(_hold(owner, "batch_claim", release, order))
    await _queued(owner.gate, 2)
    assert owner.queue_is_deep() is False

    save = asyncio.create_task(_hold(owner, "batch_result_save", release, order))
    await _queued(owner.gate, 3)
    # A quarter of the pool (1 of 4): one waiting save is enough.
    assert owner.queue_is_deep() is True

    release.set()
    await asyncio.gather(*holders, background, claim, save)
    assert owner.queue_is_deep() is False
    await owner.close()


async def test_api_pools_keep_the_plain_gate():
    owner = DatabaseOwner(DatabasePolicy("control", 2, 0.05, 1, 0.05, 2))
    assert not isinstance(owner.gate, PriorityCapacityGate)
    assert owner.queue_is_deep() is False
    await owner.close()
