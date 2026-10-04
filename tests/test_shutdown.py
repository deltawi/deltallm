import asyncio

import pytest

from src.lifecycle_settings import LifecycleSettings
from src.process_lifecycle import ProcessLifecycle
from src.shutdown import BoundedExitStack, ShutdownOwner, cleanup_deadline, shutdown_owner


async def test_resistant_closer_cannot_prevent_later_cleanup_or_restart_deadline():
    lifecycle = ProcessLifecycle(
        LifecycleSettings(
            lifecycle_withdrawal_seconds=0,
            lifecycle_request_drain_seconds=0.01,
            lifecycle_cancellation_seconds=0.01,
            lifecycle_worker_drain_seconds=0.02,
            lifecycle_close_seconds=0.1,
            lifecycle_shutdown_seconds=0.2,
        )
    )
    lifecycle.mark_serving()
    owner = ShutdownOwner(lifecycle)
    token = shutdown_owner.set(owner)
    release = asyncio.Event()
    closed = []

    async def pool():
        closed.append("pool")

    async def broken():
        closed.append("worker")
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    try:
        async with asyncio.timeout(1):
            async with BoundedExitStack() as stack:
                stack.push_async_callback(pool, cleanup_phase="close")
                stack.push_async_callback(broken)
        assert closed == ["worker", "pool"]
        assert owner.failed
        assert len(owner.pending) == 1
        assert cleanup_deadline(60) == lifecycle.deadlines.workers
        assert cleanup_deadline(60, phase="close") == lifecycle.deadlines.close
    finally:
        release.set()
        await asyncio.gather(*owner.pending, return_exceptions=True)
        shutdown_owner.reset(token)


async def test_service_reconfiguration_does_not_start_process_shutdown():
    lifecycle = ProcessLifecycle(LifecycleSettings())
    lifecycle.mark_serving()
    token = shutdown_owner.set(ShutdownOwner(lifecycle))
    try:
        assert cleanup_deadline(5) > asyncio.get_running_loop().time()
        assert lifecycle.ready
        assert not lifecycle.draining
    finally:
        shutdown_owner.reset(token)


def test_watchdog_bounds_interpreter_thread_join_after_async_shutdown():
    import subprocess
    import sys
    import textwrap

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""
            import threading
            from src.lifecycle_settings import LifecycleSettings
            from src.process_lifecycle import ProcessLifecycle
            from src.shutdown_watchdog import ShutdownWatchdog
            lifecycle = ProcessLifecycle(LifecycleSettings(
                lifecycle_withdrawal_seconds=0,
                lifecycle_request_drain_seconds=.01,
                lifecycle_cancellation_seconds=.01,
                lifecycle_worker_drain_seconds=.01,
                lifecycle_close_seconds=.01,
                lifecycle_shutdown_seconds=.1,
            ))
            watchdog = ShutdownWatchdog()
            lifecycle.on_drain = watchdog.arm
            threading.Thread(target=threading.Event().wait, daemon=False).start()
            lifecycle.begin_drain()
            lifecycle.mark_stopped()
        """),
        ],
        timeout=5,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 70
    assert "forced exit" in result.stderr


@pytest.mark.parametrize("cancel_cleanup", [False, True])
def test_watchdog_bounds_real_callback_thread_after_cleanup(cancel_cleanup):
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.performance.lifecycle_blocked_callback",
            *(["--cancel-cleanup"] if cancel_cleanup else []),
        ],
        timeout=10,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 70, result.stderr
    assert "forced exit" in result.stderr
    assert "AssertionError" not in result.stderr


async def test_cancel_resistant_optional_callback_remains_owned_after_service_shutdown():
    from src.callbacks import CallbackManager, CustomLogger
    from src.request_work_settings import RequestWorkSettings
    from tests.callbacks.test_bounded_delivery import payload

    manager = CallbackManager(RequestWorkSettings(callback_shutdown_seconds=0.01))
    entered, release = asyncio.Event(), asyncio.Event()

    class Resistant(CustomLogger):
        async def async_log_success_event(self, **kwargs):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass

    lifecycle = ProcessLifecycle(LifecycleSettings())
    owner = ShutdownOwner(lifecycle)
    token = shutdown_owner.set(owner)
    manager.register_callback(Resistant())
    manager.dispatch_success_callbacks(payload())
    try:
        await entered.wait()
        lifecycle.begin_drain()
        await manager.shutdown()
        assert owner.failed
        assert len(owner.pending) == 1
        assert manager.delivery.pending == 1
    finally:
        release.set()
        await asyncio.gather(*owner.pending, return_exceptions=True)
        shutdown_owner.reset(token)
