import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

import pytest

from src.realtime.contracts import RealtimeLimits
from src.realtime.lifecycle import RealtimeDrain
from src.realtime.runtime import RealtimeRuntime
from tests.realtime.fakes import Admission


async def test_drain_budget_is_cleanup_plus_write_and_is_never_restarted():
    drain = RealtimeDrain(RealtimeLimits(cleanup_seconds=2, write_seconds=3))
    deadline = drain.begin()
    assert deadline - drain.cleanup_deadline == 3
    async with drain.cleanup():
        await asyncio.sleep(0)
    async with drain.close_socket():
        await asyncio.sleep(0)
    assert drain.begin() == deadline


async def test_upstream_close_timeout_still_attempts_admission_with_the_same_deadline():
    drain = RealtimeDrain(RealtimeLimits(cleanup_seconds=0.02, write_seconds=1))
    stack = AsyncExitStack()
    exits = []

    @asynccontextmanager
    async def resource(name):
        try:
            yield
        finally:
            exits.append((name, drain.cleanup_deadline))
            await asyncio.Event().wait()

    await drain.enter_context(stack, resource("admission"))
    await drain.enter_context(stack, resource("upstream"))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(stack.aclose(), 1)
    assert exits == [("upstream", drain.cleanup_deadline), ("admission", drain.cleanup_deadline)]
    assert drain.failed


async def test_dependency_suppressing_timeout_does_not_hide_cleanup_failure():
    drain = RealtimeDrain(RealtimeLimits(cleanup_seconds=0.02))
    with pytest.raises(TimeoutError):
        async with drain.cleanup():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
    assert drain.failed


async def test_shutdown_deadline_retains_ownership_of_unfinished_cleanup():
    runtime = RealtimeRuntime(
        admission=Admission(), limits=RealtimeLimits(cleanup_seconds=0.02, write_seconds=0.02)
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def owner():
        with runtime.reserve():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await release.wait()

    task = asyncio.create_task(owner())
    try:
        await entered.wait()
        with pytest.raises(RuntimeError, match="before shutdown"):
            await asyncio.wait_for(runtime.close(), 1)
        assert not task.done()
        assert runtime.active_sessions == 1
        # A second shutdown must report the same exhausted budget, without
        # cancelling this still-owned finalizer a second time.
        with pytest.raises(RuntimeError, match="before shutdown"):
            await asyncio.wait_for(runtime.close(), 1)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert runtime.active_sessions == 0


@pytest.mark.parametrize("error", [RuntimeError("session failure"), asyncio.CancelledError()])
async def test_successful_resource_exits_do_not_mark_session_errors_as_cleanup_failures(error):
    drain = RealtimeDrain(RealtimeLimits())
    stack = AsyncExitStack()
    exits = []

    @asynccontextmanager
    async def resource(name):
        try:
            yield
        finally:
            exits.append(name)

    await drain.enter_context(stack, resource("admission"))
    await drain.enter_context(stack, resource("upstream"))
    assert not await stack.__aexit__(type(error), error, None)
    assert exits == ["upstream", "admission"]
    assert not drain.failed


@pytest.mark.parametrize(
    "error",
    [RuntimeError("session failure"), TimeoutError("session timeout"), asyncio.CancelledError()],
)
async def test_context_reraising_original_session_error_is_not_a_cleanup_failure(error):
    drain = RealtimeDrain(RealtimeLimits())
    stack = AsyncExitStack()

    class Resource:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            raise exc

    await drain.enter_context(stack, Resource())
    with pytest.raises(type(error)) as caught:
        await stack.__aexit__(type(error), error, None)
    assert caught.value is error
    assert not drain.failed
