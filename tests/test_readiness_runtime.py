import asyncio

import pytest

from src.lifecycle_settings import LifecycleSettings
from src.process_lifecycle import ProcessLifecycle
from src.readiness import HealthCheck, ReadinessRuntime


def runtime(probes, *, workers=lambda: ({}, {}), clock=None, **settings):
    lifecycle = ProcessLifecycle(LifecycleSettings(**settings))
    lifecycle.mark_serving()
    kwargs = {"clock": clock} if clock else {}
    return ReadinessRuntime(lifecycle=lifecycle, probes=probes, workers=workers, **kwargs)


async def test_concurrent_readers_share_probes_and_cached_success_expires():
    now = [0.0]
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def probe():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return True

    owner = runtime({"database": probe}, clock=lambda: now[0])
    readers = [asyncio.create_task(owner.payload()) for _ in range(4)]
    await entered.wait()
    overflow = await owner.payload()
    assert overflow["status"] == "degraded"
    assert overflow["details"]["database"]["state"] == "busy"
    assert calls == 1
    release.set()
    assert all(row["status"] == "ok" for row in await asyncio.gather(*readers))
    assert (await owner.payload())["status"] == "ok"
    assert calls == 1
    now[0] = 1.1
    await owner.payload()
    assert calls == 2


async def test_worker_death_and_drain_override_cached_success_without_dependency_io():
    ready = [True]
    calls = 0

    async def probe():
        nonlocal calls
        calls += 1
        return True

    owner = runtime(
        {"redis": probe},
        workers=lambda: ({"worker": HealthCheck(ready[0], "ready" if ready[0] else "failed")}, {}),
    )
    assert (await owner.payload())["status"] == "ok"
    ready[0] = False
    assert (await owner.payload())["status"] == "degraded"
    ready[0] = True
    assert (await owner.payload())["status"] == "ok"
    owner.lifecycle.begin_drain()
    assert (await owner.payload())["details"]["process"]["state"] == "draining"
    assert calls == 1


async def test_cancellation_resistant_probe_keeps_capacity_until_actual_completion():
    release = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0
    now = [0.0]

    async def blocked():
        nonlocal calls
        calls += 1
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
        return True

    owner = runtime(
        {"database": blocked}, clock=lambda: now[0], readiness_probe_timeout_seconds=0.01
    )
    try:
        async with asyncio.timeout(1):
            result = await owner.payload()
            await cancelled.wait()
        assert result["details"]["database"]["state"] == "timeout"
        for _ in range(20):
            now[0] += 2
            assert (await owner.payload())["status"] == "degraded"
        assert calls == 1
        release.set()
        await asyncio.gather(*owner._probes.values())
        assert (await owner.payload())["status"] == "ok"
        assert calls == 2
    finally:
        release.set()
        await owner.close(deadline=asyncio.get_running_loop().time() + 1)


async def test_one_cancelled_reader_cannot_cancel_other_readers():
    entered, release = asyncio.Event(), asyncio.Event()

    async def probe():
        entered.set()
        await release.wait()
        return True

    owner = runtime({"database": probe})
    readers = [asyncio.create_task(owner.payload()) for _ in range(2)]
    await entered.wait()
    readers[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await readers[0]
    release.set()
    assert (await readers[1])["status"] == "ok"


async def test_final_reader_cancels_probes_and_close_does_not_restart_them():
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def probe():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    owner = runtime({"database": probe})
    task = asyncio.create_task(owner.payload())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await owner.close(deadline=asyncio.get_running_loop().time() + 1)
    assert stopped.is_set()
    assert (await owner.payload())["status"] == "degraded"
