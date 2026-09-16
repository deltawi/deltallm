from __future__ import annotations

import asyncio
from collections import Counter
import gc
from threading import Event
import weakref

import pytest
from prometheus_client import REGISTRY

from src.blocking_work import BlockingWorkExecutor, WorkUnavailableError
from src.bounded_payload import PayloadCapacityExceeded, retained_size

pytestmark = pytest.mark.hermetic


def executor(**kwargs):
    return BlockingWorkExecutor(
        **{
            "allocation": "guardrail",
            "workers": 1,
            "max_pending": 1,
            "max_bytes": 1024,
            "timeout_seconds": 1,
            "shutdown_seconds": 0.02,
            **kwargs,
        }
    )


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_cancelled_waiter_does_not_free_running_thread_capacity(timeout):
    pool = executor(timeout_seconds=0.03 if timeout else 1)
    started, finish = Event(), Event()

    def work():
        started.set()
        finish.wait(2)
        return "late result"

    task = asyncio.create_task(pool.run(work, payload_bytes=500))
    try:
        await until(started.is_set)
        if not timeout:
            task.cancel()
        with pytest.raises(WorkUnavailableError if timeout else asyncio.CancelledError):
            await task
        assert pool.pending == 1
        assert pool.retained_bytes == 500
        for _ in range(20):
            with pytest.raises(WorkUnavailableError):
                await pool.run(lambda: "must not run", payload_bytes=1)
        finish.set()
        await until(lambda: pool.pending == 0)
        assert pool.retained_bytes == 0
        assert await pool.run(lambda: "recovered", payload_bytes=1) == "recovered"
    finally:
        finish.set()
        await pool.shutdown()


@pytest.mark.asyncio
async def test_shutdown_is_bounded_while_running_work_stays_accounted():
    pool = executor()
    started, finish = Event(), Event()

    def work():
        started.set()
        finish.wait(2)

    task = asyncio.create_task(pool.run(work, payload_bytes=500))
    try:
        await until(started.is_set)
        async with asyncio.timeout(0.5):
            await pool.shutdown()
        assert pool.pending == 1
        with pytest.raises(WorkUnavailableError):
            await pool.run(lambda: None, payload_bytes=1)
    finally:
        finish.set()
        await task
        await until(lambda: pool.pending == 0)


@pytest.mark.asyncio
async def test_byte_limit_rejects_before_submission():
    pool = executor()
    called = False

    def work():
        nonlocal called
        called = True

    try:
        with pytest.raises(WorkUnavailableError):
            await pool.run(work, payload_bytes=1025)
        assert not called
        assert pool.pending == pool.retained_bytes == 0
    finally:
        await pool.shutdown()


class QueuedPayload:
    def __init__(self, calls):
        self.data = bytearray(256 * 1024)
        self.calls = calls

    def __call__(self):
        self.calls.append(len(self.data))


def cancelled_work_count():
    return (
        REGISTRY.get_sample_value(
            "deltallm_bounded_work_seconds_count",
            {"allocation": "guardrail", "outcome": "cancelled"},
        )
        or 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
@pytest.mark.parametrize("bound", ["full", "bytes"])
async def test_cancelled_queue_entries_keep_retained_payloads_bounded(
    monkeypatch, interruption, bound
):
    size = 256 * 1024
    pool = executor(
        max_pending=2 if bound == "full" else 8,
        max_bytes=4 * size if bound == "full" else size + 1,
        timeout_seconds=10,
    )
    started, finish = Event(), Event()
    scopes = []
    real_timeout = asyncio.timeout

    def capture_timeout(delay):
        scope = real_timeout(delay)
        if delay == pool.timeout_seconds:
            scopes.append(scope)
        return scope

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)

    def blocked():
        started.set()
        assert finish.wait(5)

    owner = asyncio.create_task(pool.run(blocked, payload_bytes=1))
    refs, calls = [], []
    outcomes = Counter()
    before_cancelled = cancelled_work_count()
    try:
        await until(started.is_set)
        for _ in range(100):
            payload = QueuedPayload(calls)
            refs.append(weakref.ref(payload))
            queued = asyncio.create_task(pool.run(payload, payload_bytes=size))
            del payload
            await asyncio.sleep(0)  # Run admission while the only worker is blocked.
            if not queued.done():
                scope = scopes.pop()
                if interruption == "timeout":
                    scope.reschedule(asyncio.get_running_loop().time())
                else:
                    queued.cancel()
                del scope
            try:
                await queued
            except asyncio.CancelledError:
                outcomes["cancel"] += 1
            except WorkUnavailableError as exc:
                outcomes[exc.reason] += 1
            del queued
            await asyncio.sleep(0)
        gc.collect()
        # Weak references measure the actual payloads retained by the real pool,
        # independently of its bookkeeping or any private queue implementation.
        assert sum(ref() is not None for ref in refs) == 1
        assert outcomes == {interruption: 1, bound: 99}
        assert pool.pending == 2 and pool.retained_bytes == size + 1
        assert cancelled_work_count() == before_cancelled
        assert calls == []
        finish.set()
        await owner
        await until(lambda: pool.pending == 0)

        def payloads_released():
            # Accounting runs before asyncio propagates the worker result and
            # releases the last wrapper/traceback references on later loop turns.
            gc.collect()
            return all(ref() is None for ref in refs)

        await until(payloads_released)
        assert calls == []  # Dequeuing cancelled work must not execute its function.
        assert pool.retained_bytes == 0
        assert cancelled_work_count() == before_cancelled + 1
        assert await pool.run(lambda: "recovered", payload_bytes=1) == "recovered"
    finally:
        finish.set()
        await owner
        await pool.shutdown()


@pytest.mark.asyncio
async def test_shutdown_discards_cancelled_queue_entries_before_running_work_finishes():
    size = 256 * 1024
    pool = executor(max_pending=2, max_bytes=2 * size)
    started, finish = Event(), Event()

    def blocked():
        started.set()
        assert finish.wait(5)

    owner = asyncio.create_task(pool.run(blocked, payload_bytes=1))
    calls = []
    try:
        await until(started.is_set)
        payload = QueuedPayload(calls)
        ref = weakref.ref(payload)
        queued = asyncio.create_task(pool.run(payload, payload_bytes=size))
        del payload
        await until(lambda: pool.pending == 2)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        del queued
        await pool.shutdown()
        await until(lambda: pool.pending == 1)
        gc.collect()
        assert ref() is None and calls == []
        assert pool.retained_bytes == 1
    finally:
        finish.set()
        await owner
        await pool.shutdown()
    assert pool.pending == pool.retained_bytes == 0


def test_payload_bounds_cycles_large_values_and_many_small_nodes():
    cycle = []
    cycle.append(cycle)
    for value in (cycle, "a" * 2000, {"small": [None] * 1000}, object()):
        with pytest.raises(PayloadCapacityExceeded):
            retained_size(value, limit=1024)
    assert 0 < retained_size({"content": "hello"}, limit=1024) < 1024


def test_payload_measures_pydantic_extra_and_private_values_before_copying():
    from pydantic import BaseModel, ConfigDict, PrivateAttr

    class Extended(BaseModel):
        model_config = ConfigDict(extra="allow")
        _private: str = PrivateAttr(default="")

    extra = Extended(extra="a" * 2048)
    private = Extended()
    private._private = "a" * 2048
    for value in (extra, private):
        with pytest.raises(PayloadCapacityExceeded):
            retained_size(value, limit=1024)
