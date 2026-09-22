"""Disposable subprocess fixture: a real callback whose native thread never exits."""

import asyncio
from datetime import UTC, datetime
import sys
import threading

from src.callbacks import CallbackManager, CustomLogger, build_standard_logging_payload
from src.lifecycle_settings import LifecycleSettings
from src.process_lifecycle import ProcessLifecycle
from src.request_work_settings import RequestWorkSettings
from src.shutdown import BoundedExitStack, ShutdownOwner, shutdown_owner
from src.shutdown_watchdog import ShutdownWatchdog


async def exercise(*, cancel_cleanup: bool) -> None:
    lifecycle = ProcessLifecycle(
        LifecycleSettings(
            lifecycle_withdrawal_seconds=0,
            lifecycle_request_drain_seconds=0.01,
            lifecycle_cancellation_seconds=0.01,
            lifecycle_worker_drain_seconds=0.05,
            lifecycle_close_seconds=0.02,
            lifecycle_shutdown_seconds=0.5,
        )
    )
    watchdog = ShutdownWatchdog()
    lifecycle.on_drain = watchdog.arm
    owner = ShutdownOwner(lifecycle)
    shutdown_owner.set(owner)
    manager = CallbackManager(RequestWorkSettings(callback_shutdown_seconds=0.01))
    entered = threading.Event()

    class Blocked(CustomLogger):
        def log_success_event(self, **kwargs):
            entered.set()
            threading.Event().wait()

    # The sync API accepts positional arguments through CustomLogger's adapter.
    class Handler(Blocked):
        def log_success_event(self, *args):
            super().log_success_event()

    manager.register_callback(Handler())
    now = datetime.now(UTC)
    manager.dispatch_success_callbacks(
        build_standard_logging_payload(
            call_type="completion",
            request_id="blocked-fixture",
            model="fixture",
            deployment_model="fixture",
            request_payload={},
            response_obj={"choices": []},
            user_api_key_dict={},
            start_time=now,
            end_time=now,
            api_base=None,
        )
    )
    async with asyncio.timeout(5):
        while not entered.is_set():
            await asyncio.sleep(0.001)
    lifecycle.mark_serving()
    lifecycle.begin_drain()
    stack = BoundedExitStack()
    stack.push_async_callback(manager.shutdown)
    closing = asyncio.create_task(stack.aclose())
    if cancel_cleanup:
        await asyncio.sleep(0)
        closing.cancel()
    await asyncio.gather(closing, return_exceptions=True)
    assert owner.failed
    assert manager.delivery.blocking.pending == 1
    lifecycle.mark_stopped()


if __name__ == "__main__":
    asyncio.run(exercise(cancel_cleanup="--cancel-cleanup" in sys.argv))
