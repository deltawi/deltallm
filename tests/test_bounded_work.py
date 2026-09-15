from __future__ import annotations

import asyncio
from threading import Event

import pytest

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
