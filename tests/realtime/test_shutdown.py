import asyncio
from contextlib import asynccontextmanager, suppress

import pytest
from fastapi import WebSocket

from src.api.v1.endpoints.realtime import realtime
from src.realtime.contracts import RealtimeLimits
from src.realtime.runtime import RealtimeRuntime
from src.lifecycle_settings import LifecycleSettings
from src.process_lifecycle import ProcessLifecycle
from tests.realtime.fakes import Admission, Connector, Socket
from tests.realtime.test_session import assert_no_pumps

pytestmark = pytest.mark.app


class PausedAdmission(Admission):
    def __init__(self):
        super().__init__()
        self.finalizing = asyncio.Event()
        self.release = asyncio.Event()
        self.interrupted = False

    @asynccontextmanager
    async def admit(self, request):
        async with super().admit(request) as admitted:
            try:
                yield admitted
            finally:
                self.finalizing.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.interrupted = True
                    raise


class EdgeSocket:
    def __init__(self, app):
        self.accepted = asyncio.Event()
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self.incoming = asyncio.Queue()
        self.incoming.put_nowait({"type": "websocket.connect"})
        self.messages = []
        self.websocket = WebSocket(
            {
                "type": "websocket",
                "path": "/v1/realtime",
                "app": app,
                "query_string": b"model=voice",
                "headers": [(b"authorization", b"Bearer sk-test")],
            },
            self.incoming.get,
            self.send,
        )

    async def send(self, message):
        self.messages.append(message)
        if message["type"] == "websocket.accept":
            self.accepted.set()
        elif message["type"] == "websocket.close":
            self.closing.set()
            await self.release.wait()


@asynccontextmanager
async def session(app, **limits):
    admission, connector = PausedAdmission(), Connector(Socket())
    runtime = RealtimeRuntime(
        admission=admission,
        connector=connector,
        limits=RealtimeLimits(
            cleanup_seconds=limits.get("cleanup_seconds", 2),
            write_seconds=limits.get("write_seconds", 2),
        ),
    )
    app.state.realtime_runtime = runtime
    edge = EdgeSocket(app)
    owner = asyncio.create_task(realtime(edge.websocket))
    try:
        await asyncio.wait_for(edge.accepted.wait(), 1)
        yield runtime, admission, connector, edge, owner
    finally:
        admission.release.set()
        edge.release.set()
        if not owner.done():
            owner.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(owner, 1)


async def test_shutdown_allows_resource_cleanup_and_downstream_close_their_shared_budget(test_app):
    async with session(test_app, cleanup_seconds=0.1, write_seconds=0.5) as values:
        runtime, admission, connector, edge, owner = values
        shutdown = asyncio.create_task(runtime.close())
        await asyncio.wait_for(admission.finalizing.wait(), 1)
        admission.release.set()
        await asyncio.wait_for(edge.closing.wait(), 1)
        assert runtime.active_sessions == 1
        # Keep the downstream close pending beyond the old resource-only
        # shutdown deadline, but comfortably inside its own write allowance.
        release = asyncio.get_running_loop().call_later(0.2, edge.release.set)
        try:
            await asyncio.wait_for(shutdown, 1)
        finally:
            release.cancel()
        assert owner.cancelled()
        assert connector.closed == admission.closed == 1
        assert runtime.active_sessions == 0
        assert_no_pumps()


@pytest.mark.parametrize("disconnect_first", [False, True])
async def test_repeated_shutdown_does_not_interrupt_cleanup_or_finalize_twice(
    test_app, disconnect_first
):
    async with session(test_app) as values:
        runtime, admission, connector, edge, owner = values
        if disconnect_first:
            edge.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(admission.finalizing.wait(), 1)
        first = asyncio.create_task(runtime.close())
        await asyncio.wait_for(admission.finalizing.wait(), 1)
        second = asyncio.create_task(runtime.close())
        edge.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        # Both callers must reach their wait before cleanup is released.
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
        assert runtime.active_sessions == 1
        admission.release.set()
        await asyncio.wait_for(edge.closing.wait(), 1)
        assert connector.closed == admission.closed == 1
        assert not admission.interrupted
        assert not first.done() and not second.done()
        edge.release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        await runtime.close()
        assert runtime.active_sessions == 0
        assert owner.cancelled() is not disconnect_first
        assert_no_pumps()


async def test_process_drain_starts_realtime_cleanup_once_before_worker_shutdown(test_app):
    async with session(test_app) as values:
        runtime, admission, connector, edge, owner = values
        lifecycle = ProcessLifecycle(LifecycleSettings())
        lifecycle.register_claim_stop(runtime.begin_drain)
        lifecycle.mark_serving()
        deadlines = lifecycle.begin_drain()
        await asyncio.wait_for(admission.finalizing.wait(), 1)
        assert not runtime.ready
        assert lifecycle.begin_drain() is deadlines
        shutdown = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        assert not admission.interrupted
        assert not shutdown.done()
        admission.release.set()
        await asyncio.wait_for(edge.closing.wait(), 1)
        edge.release.set()
        await asyncio.wait_for(shutdown, 1)
        assert owner.cancelled()
        assert connector.closed == admission.closed == 1
        assert runtime.active_sessions == 0
        assert_no_pumps()


@pytest.mark.parametrize("cleanup,write,valid", [(5, 10, True), (25, 25, False), (30, 30, False)])
def test_realtime_cleanup_budget_fits_before_generic_request_cancellation(cleanup, write, valid):
    from src.bootstrap.realtime import validate_realtime_drain
    from src.realtime.config import RealtimeSettings

    settings = RealtimeSettings(cleanup_seconds=cleanup, write_seconds=write)
    lifecycle = ProcessLifecycle(LifecycleSettings())
    if valid:
        validate_realtime_drain(settings, lifecycle)
    else:
        with pytest.raises(RuntimeError, match="response cutoff"):
            validate_realtime_drain(settings, lifecycle)


@pytest.mark.parametrize("phase", ["admission", "downstream"])
async def test_genuine_cleanup_timeout_is_reported_and_releases_local_capacity(test_app, phase):
    async with session(test_app, cleanup_seconds=0.03, write_seconds=0.03) as values:
        runtime, admission, connector, edge, owner = values
        if phase == "downstream":
            admission.release.set()
        else:
            edge.release.set()
        with pytest.raises(RuntimeError, match="cleanup"):
            await asyncio.wait_for(runtime.close(), 1)
        await asyncio.gather(owner, return_exceptions=True)
        assert connector.closed == admission.closed == 1
        assert admission.interrupted is (phase == "admission")
        assert runtime.active_sessions == 0
        with pytest.raises(RuntimeError, match="cleanup"):
            await runtime.close()
        assert_no_pumps()


@pytest.mark.parametrize("phase", ["admission", "upstream"])
@pytest.mark.parametrize("trigger", ["shutdown", "disconnect"])
async def test_resource_exit_failure_remains_visible_after_endpoint_handling(
    test_app, monkeypatch, phase, trigger
):
    admission, connector = Admission(), Connector(Socket())
    resource, method = (admission, "admit") if phase == "admission" else (connector, "open")
    original = getattr(resource, method)

    @asynccontextmanager
    async def failing_exit(*args):
        async with original(*args) as value:
            try:
                yield value
            finally:
                raise RuntimeError("private finalization diagnostic")

    monkeypatch.setattr(resource, method, failing_exit)
    runtime = RealtimeRuntime(admission=admission, connector=connector)
    test_app.state.realtime_runtime = runtime
    edge = EdgeSocket(test_app)
    edge.release.set()
    owner = asyncio.create_task(realtime(edge.websocket))
    try:
        await asyncio.wait_for(edge.accepted.wait(), 1)
        if trigger == "disconnect":
            edge.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(owner, 1)
        with pytest.raises(RuntimeError, match="cleanup") as caught:
            await asyncio.wait_for(runtime.close(), 1)
        assert "deadline" not in str(caught.value)
        assert "private" not in str(caught.value)
        with pytest.raises(RuntimeError, match="cleanup"):
            await runtime.close()
        assert owner.done() and runtime.active_sessions == 0
        assert connector.closed == admission.closed == 1
        assert "private finalization diagnostic" not in repr(edge.messages)
        assert_no_pumps()
    finally:
        if not owner.done():
            owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)


@pytest.mark.parametrize("phase", ["admission", "upstream"])
@pytest.mark.parametrize("trigger", ["shutdown", "disconnect"])
async def test_cancellation_during_resource_exit_is_reported_as_failed_cleanup(
    test_app, monkeypatch, phase, trigger
):
    admission, connector = Admission(), Connector(Socket())
    resource, method = (admission, "admit") if phase == "admission" else (connector, "open")
    original = getattr(resource, method)
    finalizing, release = asyncio.Event(), asyncio.Event()
    finalized = False

    @asynccontextmanager
    async def paused_exit(*args):
        nonlocal finalized
        async with original(*args) as value:
            try:
                yield value
            finally:
                finalizing.set()
                await release.wait()
                finalized = True

    monkeypatch.setattr(resource, method, paused_exit)
    runtime = RealtimeRuntime(admission=admission, connector=connector)
    test_app.state.realtime_runtime = runtime
    edge = EdgeSocket(test_app)
    edge.release.set()
    owner = asyncio.create_task(realtime(edge.websocket))
    shutdown = None
    try:
        await asyncio.wait_for(edge.accepted.wait(), 1)
        if trigger == "shutdown":
            shutdown = asyncio.create_task(runtime.close())
        else:
            edge.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(finalizing.wait(), 1)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owner, 1)
        assert not finalized
        if shutdown is not None:
            with pytest.raises(RuntimeError, match="cleanup failed"):
                await asyncio.wait_for(shutdown, 1)
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await runtime.close()
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await runtime.close()
        assert owner.cancelled() and runtime.active_sessions == 0
        assert connector.closed == admission.closed == 1
        assert_no_pumps()
    finally:
        release.set()
        if not owner.done():
            owner.cancel()
        await asyncio.gather(
            owner, *([shutdown] if shutdown is not None else []), return_exceptions=True
        )
