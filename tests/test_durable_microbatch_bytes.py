"""Byte collection and retained capacity have one bounded lifecycle owner."""

import asyncio

import pytest

from src.billing.durable_microbatch import DurableBatchClosed, DurableBatchFull, DurableMicrobatcher


def owner(handler, **overrides):
    return DurableMicrobatcher(
        handler,
        **{
            "max_batch_size": 8,
            "max_pending": 16,
            "dwell_seconds": 0.01,
            "payload_size": len,
            "max_batch_bytes": 15,
            "max_retained_bytes": 8 * 1024 * 1024,
            **overrides,
        },
    )


async def test_collection_splits_at_exact_json_list_byte_limit_in_input_order():
    calls = []

    async def handler(values):
        calls.append(list(values))
        assert len(b"[" + b",".join(values) + b"]") <= 15
        return values

    batcher = owner(handler)
    batcher.start()
    values = [b'"aaaa"', b'"bbbb"', b'"cccc"', b'"dddd"', b'"eeee"']
    try:
        assert await asyncio.gather(*(batcher.submit(value) for value in values)) == values
    finally:
        await batcher.close()
    assert calls == [values[:2], values[2:4], values[4:]]
    assert batcher.pending == batcher.retained_bytes == 0


async def test_single_oversized_item_is_rejected_without_handler_work():
    calls = []

    async def handler(values):
        calls.append(list(values))
        return values

    batcher = owner(handler)
    batcher.start()
    try:
        with pytest.raises(DurableBatchFull):
            await batcher.submit(b"x" * 14)
        assert batcher.pending == batcher.retained_bytes == 0
    finally:
        await batcher.close()
    assert calls == []


async def test_cancelled_queued_item_releases_its_bytes_without_collection():
    entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(values):
        calls.append(list(values))
        entered.set()
        await release.wait()
        return values

    batcher = owner(
        handler,
        dwell_seconds=0,
        max_retained_bytes=8204,
        set_queue_depth=lambda depth: queued.set() if depth else None,
    )
    batcher.start()
    first = asyncio.create_task(batcher.submit(b'"aaaa"'))
    await entered.wait()
    queued.clear()
    second = asyncio.create_task(batcher.submit(b'"bbbb"'))
    await queued.wait()
    assert batcher.retained_bytes == 8204
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
    assert batcher.pending == 0
    assert batcher.retained_bytes == 4102
    release.set()
    assert await first == b'"aaaa"'
    await batcher.close()
    assert calls == [[b'"aaaa"']]
    assert batcher.retained_bytes == 0


async def test_selected_work_keeps_its_charge_after_caller_cancellation():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(values):
        calls.append(list(values))
        entered.set()
        await release.wait()
        return values

    batcher = owner(handler, dwell_seconds=0, max_retained_bytes=4102)
    batcher.start()
    first = asyncio.create_task(batcher.submit(b'"aaaa"'))
    await entered.wait()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    assert batcher.pending == 0 and batcher.retained_bytes == 4102
    with pytest.raises(DurableBatchFull):
        await batcher.submit(b'"bbbb"')
    release.set()
    await batcher.close()
    assert calls == [[b'"aaaa"']]
    assert batcher.retained_bytes == 0


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_handler_failure_releases_selected_bytes_once(failure):
    async def handler(values):
        raise failure("failure without payload")

    batcher = owner(handler, dwell_seconds=0)
    batcher.start()
    try:
        with pytest.raises(failure):
            await batcher.submit(b'"aaaa"')
        assert batcher.retained_bytes == 0
        if failure is asyncio.CancelledError:
            assert batcher.task.cancelled()
            with pytest.raises(DurableBatchClosed):
                await batcher.submit(b'"bbbb"')
    finally:
        await batcher.close()
    assert batcher.retained_bytes == 0


@pytest.mark.parametrize("phase", ["handler", "collection"])
async def test_owner_cancellation_fails_all_retained_callers_and_clears_bytes(phase):
    entered = asyncio.Event()

    async def handler(values):
        entered.set()
        await asyncio.Event().wait()

    collected = asyncio.Event()
    batcher = owner(
        handler,
        dwell_seconds=0 if phase == "handler" else 0.05,
        set_queue_depth=lambda depth: collected.set() if not depth else None,
    )
    batcher.start()
    first = asyncio.create_task(batcher.submit(b'"aaaa"'))
    if phase == "handler":
        await entered.wait()
    else:
        await collected.wait()
    batcher.task.cancel()
    await asyncio.gather(batcher.task, return_exceptions=True)
    result = (await asyncio.gather(first, return_exceptions=True))[0]
    assert isinstance(result, (DurableBatchClosed, asyncio.CancelledError))
    assert batcher.pending == batcher.retained_bytes == 0
    await batcher.close()


async def test_timeout_drain_releases_inflight_and_queued_byte_charges():
    entered, queued = asyncio.Event(), asyncio.Event()

    async def handler(values):
        entered.set()
        await asyncio.Event().wait()

    batcher = owner(
        handler,
        dwell_seconds=0,
        set_queue_depth=lambda depth: queued.set() if depth else None,
    )
    batcher.start()
    first = asyncio.create_task(batcher.submit(b'"aaaa"'))
    await entered.wait()
    queued.clear()
    second = asyncio.create_task(batcher.submit(b'"bbbb"'))
    await queued.wait()
    await batcher.close(timeout_seconds=0.01)
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(value, (DurableBatchClosed, asyncio.CancelledError)) for value in results)
    assert batcher.pending == batcher.retained_bytes == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_batch_bytes": 2},
        {"max_batch_bytes": 1048577},
        {"max_retained_bytes": 0},
        {"max_retained_bytes": 64 * 1024 * 1024 + 1},
        {"payload_size": None},
    ],
)
def test_byte_mode_requires_finite_limits_and_an_explicit_size_owner(overrides):
    with pytest.raises(ValueError):
        owner(None, **overrides)


@pytest.mark.parametrize("size", [-1, True, 1.5])
async def test_invalid_size_cannot_enter_the_queue(size):
    async def handler(values):
        return values

    batcher = owner(handler, payload_size=lambda _: size)
    batcher.start()
    try:
        with pytest.raises(ValueError):
            await batcher.submit(b'"aaaa"')
        assert batcher.pending == batcher.retained_bytes == 0
    finally:
        await batcher.close()


@pytest.mark.parametrize("phase", ["enqueue", "collection", "queue_wait"])
async def test_observer_failure_never_leaves_an_unowned_waiter_or_payload(phase):
    calls = []

    async def handler(values):
        calls.append(list(values))
        return values

    def observe(value):
        if phase == "queue_wait" or (bool(value) == (phase == "enqueue")):
            raise RuntimeError("bounded observation failure")

    batcher = owner(
        handler,
        dwell_seconds=0,
        set_queue_depth=None if phase == "queue_wait" else observe,
        observe_queue_wait=observe if phase == "queue_wait" else None,
    )
    batcher.start()
    try:
        with pytest.raises(RuntimeError, match="observation failure"):
            await asyncio.wait_for(batcher.submit(b'"aaaa"'), 1)
        assert calls == []
        assert batcher.pending == batcher.retained_bytes == 0
    finally:
        batcher._set_queue_depth = batcher._observe_queue_wait = None
        if phase == "enqueue":
            await batcher.close()
        else:
            with pytest.raises(RuntimeError, match="observation failure"):
                await batcher.close()


async def test_wrong_handler_result_count_releases_the_whole_selected_batch():
    async def handler(values):
        return []

    batcher = owner(handler)
    batcher.start()
    try:
        outputs = await asyncio.gather(
            batcher.submit(b'"aaaa"'), batcher.submit(b'"bbbb"'), return_exceptions=True
        )
        assert all(isinstance(value, RuntimeError) for value in outputs)
        assert batcher.pending == batcher.retained_bytes == 0
    finally:
        await batcher.close()
