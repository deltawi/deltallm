"""Finite offload capacity whose owner is the real concurrent future."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import Context
from time import perf_counter
from typing import Literal, TypeVar

from src.metrics.request_work import work_bytes, work_duration, work_in_flight, work_rejections
from src.models.errors import ServiceUnavailableError

T = TypeVar("T")
Allocation = Literal["guardrail", "callback_sync"]


class WorkUnavailableError(ServiceUnavailableError):
    def __init__(self, *, reason: str = "full") -> None:
        self.reason = reason
        super().__init__(
            message="Gateway work capacity is temporarily unavailable",
            code="gateway_work_unavailable",
            affects_deployment_health=False,
        )


class BlockingWorkExecutor:
    def __init__(
        self,
        *,
        allocation: Allocation,
        workers: int,
        max_pending: int,
        max_bytes: int,
        timeout_seconds: float,
        shutdown_seconds: float,
    ) -> None:
        self.allocation = allocation
        self.max_pending = max_pending
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.shutdown_seconds = shutdown_seconds
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=allocation)
        self._pending: dict[Future[object], int] = {}
        self._bytes = 0
        self._closed = False
        self.on_completion: Callable[[], None] | None = None

    @property
    def pending(self) -> int:
        return len(self._pending)

    @property
    def retained_bytes(self) -> int:
        return self._bytes

    async def run(self, function: Callable[[], T], *, payload_bytes: int) -> T:
        # One event loop owns admission and completion accounting. Cancellation of
        # an asyncio wrapper is not completion of the actual thread's work.
        reason = (
            "closed"
            if self._closed
            else "full"
            if len(self._pending) >= self.max_pending
            else "bytes"
            if payload_bytes < 0 or self._bytes + payload_bytes > self.max_bytes
            else None
        )
        if reason:
            work_rejections.labels(self.allocation, reason).inc()
            raise WorkUnavailableError(reason=reason)
        loop = asyncio.get_running_loop()
        started = perf_counter()
        # Inference context contains the ASGI timer/task. Do not retain that
        # request graph in a thread after its waiter has been cancelled.
        context = Context()
        future = self._executor.submit(context.run, function)
        self._pending[future] = payload_bytes
        self._bytes += payload_bytes
        work_in_flight.labels(self.allocation).inc()
        work_bytes.labels(self.allocation).inc(payload_bytes)

        def completed(done: Future[object]) -> None:
            try:
                loop.call_soon_threadsafe(self._completed, done, started)
            except RuntimeError:
                # The process loop has shut down; no new work can be admitted.
                pass

        future.add_done_callback(completed)
        wrapped = Context().run(asyncio.wrap_future, future)
        # Observe late failures after a timed-out/cancelled request stops waiting.
        wrapped.add_done_callback(
            lambda done: None if done.cancelled() else done.exception(), context=Context()
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await asyncio.shield(wrapped)
        except TimeoutError as exc:
            future.cancel()  # Cancels only work that has not started.
            raise WorkUnavailableError(reason="timeout") from exc
        except BaseException:
            future.cancel()
            raise

    def _completed(self, future: Future[object], started: float) -> None:
        size = self._pending.pop(future)
        self._bytes -= size
        work_in_flight.labels(self.allocation).dec()
        work_bytes.labels(self.allocation).dec(size)
        outcome = (
            "cancelled" if future.cancelled() else "failed" if future.exception() else "completed"
        )
        work_duration.labels(self.allocation, outcome).observe(perf_counter() - started)
        if self.on_completion is not None:
            self.on_completion()

    async def shutdown(self) -> None:
        self._closed = True
        pending = tuple(self._pending)
        self._executor.shutdown(wait=False, cancel_futures=True)
        if pending:
            wrapped = [Context().run(asyncio.wrap_future, future) for future in pending]
            for item in wrapped:
                item.add_done_callback(
                    lambda done: None if done.cancelled() else done.exception(), context=Context()
                )
            await asyncio.wait(wrapped, timeout=self.shutdown_seconds)
