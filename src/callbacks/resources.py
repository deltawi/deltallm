"""Bounded SDK ownership across callback configuration generations."""

from __future__ import annotations

from src.shutdown import cleanup_deadline, retain_unfinished

import asyncio
from contextvars import Context
from threading import Lock

from src.blocking_work import BlockingWorkExecutor, WorkUnavailableError
from src.callbacks.base import CustomLogger
from src.metrics.request_work import work_in_flight, work_rejections


class CallbackResources:
    # Two full generations, including clients whose close operation is stuck.
    # Further reloads shed optional integrations instead of leaking SDK workers.
    MAX_HANDLERS = 64

    def __init__(self, blocking: BlockingWorkExecutor) -> None:
        self.blocking = blocking
        self._handlers: dict[int, CustomLogger] = {}
        self._closing: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = False
        self.blocking.on_completion = self.pump

    @property
    def pending(self) -> int:
        return len(self._handlers)

    def bind(self, handler: CustomLogger) -> None:
        if handler.blocking_executor is not None and handler.blocking_executor is not self.blocking:
            raise ValueError("A callback handler must have exactly one runtime owner")
        if id(handler) in self._handlers:
            if handler.retired:
                raise ValueError("A retired callback cannot be registered again")
            return
        if self._stopped or len(self._handlers) >= self.MAX_HANDLERS:
            work_rejections.labels("callback_resources", "full").inc()
            raise ValueError("Callback resources are full; wait for retired handlers to close")
        handler.blocking_executor = self.blocking
        handler.blocking_lock = Lock()
        handler.retired = False
        self._handlers[id(handler)] = handler
        work_in_flight.labels("callback_resources").inc()

    def retire(self, handler: CustomLogger) -> None:
        handler.retired = True
        if type(handler).close is CustomLogger.close:
            self._release(id(handler), True)
        self.pump()

    def pump(self) -> None:
        if self._stopped:
            return
        available = self.blocking.max_pending - self.blocking.pending - len(self._tasks)
        for key, handler in tuple(self._handlers.items()):
            if available <= 0:
                break
            if not handler.retired or key in self._closing:
                continue
            self._closing.add(key)
            task = asyncio.create_task(self._close(handler), context=Context())
            self._tasks.add(task)
            task.add_done_callback(self._finished, context=Context())
            available -= 1

    async def _close(self, handler: CustomLogger) -> None:
        loop = asyncio.get_running_loop()

        def close() -> None:
            succeeded = False
            try:
                assert handler.blocking_lock is not None
                with handler.blocking_lock:
                    handler.close()
                succeeded = True
            finally:
                try:
                    loop.call_soon_threadsafe(self._release, id(handler), succeeded)
                except RuntimeError:
                    pass  # The process loop has already shut down.

        try:
            await self.blocking.run(close, payload_bytes=0)
        except WorkUnavailableError as exc:
            # A timed-out running close still owns its handler. Admission rejection
            # can be retried when another real executor future completes.
            if exc.reason != "timeout":
                self._closing.discard(id(handler))
        except Exception:
            # A failed close remains charged; do not repeatedly create SDK clients.
            pass  # _release records the classified resource-close failure.

    def _release(self, key: int, succeeded: bool) -> None:
        if succeeded:
            if self._handlers.pop(key, None) is not None:
                work_in_flight.labels("callback_resources").dec()
            self._closing.discard(key)
        else:
            work_rejections.labels("callback_resources", "close_failed").inc()

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()
        # A capacity rejection is retried by executor completion, not a busy loop.
        if self.blocking.pending < self.blocking.max_pending:
            self.pump()

    async def shutdown(self, *, timeout: float) -> None:
        for handler in tuple(self._handlers.values()):
            self.retire(handler)
        deadline = cleanup_deadline(timeout)
        while self._tasks:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.wait(tuple(self._tasks), timeout=remaining)
        self._stopped = True
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.sleep(0)
        retain_unfinished(self._tasks)
