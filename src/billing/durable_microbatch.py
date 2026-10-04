"""Bounded, cancellation-safe microbatching for durable accounting ACKs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

Input = TypeVar("Input")
Output = TypeVar("Output")


class DurableBatchClosed(RuntimeError):
    pass


class DurableBatchFull(RuntimeError):
    pass


@dataclass(slots=True)
class _Pending(Generic[Input, Output]):
    value: Input
    result: asyncio.Future[Output]
    enqueued_at: float


class DurableMicrobatcher(Generic[Input, Output]):
    """One supervised owner that turns many caller waits into one durable commit.

    Cancellation before collection removes work. Once collected, database work is
    shielded from any one caller and must complete or fail every selected caller.
    """

    def __init__(
        self,
        handler: Callable[[Sequence[Input]], Awaitable[Sequence[Output]]],
        *,
        max_batch_size: int = 64,
        max_pending: int = 4096,
        dwell_seconds: float = 0.002,
        name: str = "durable-microbatch",
        observe_queue_wait: Callable[[float], None] | None = None,
        set_queue_depth: Callable[[int], None] | None = None,
    ) -> None:
        if not 1 <= max_batch_size <= 256:
            raise ValueError("max_batch_size must be between 1 and 256")
        if not max_batch_size <= max_pending <= 100_000:
            raise ValueError("max_pending must fit at least one batch")
        if not 0 <= dwell_seconds <= 0.050:
            raise ValueError("dwell_seconds must be between 0 and 50ms")
        self._handler = handler
        self._max_batch_size = max_batch_size
        self._dwell_seconds = dwell_seconds
        self._name = name
        self._observe_queue_wait = observe_queue_wait
        self._set_queue_depth = set_queue_depth
        self._queue: asyncio.Queue[_Pending[Input, Output]] = asyncio.Queue(max_pending)
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    def start(self) -> asyncio.Task[None]:
        if self._closed:
            raise DurableBatchClosed(self._name)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=self._name)
        return self._task

    async def submit(self, value: Input) -> Output:
        if self._closed:
            raise DurableBatchClosed(self._name)
        if self._task is None or self._task.done():
            raise DurableBatchClosed(f"{self._name} is not running")
        loop = asyncio.get_running_loop()
        pending = _Pending(value=value, result=loop.create_future(), enqueued_at=loop.time())
        try:
            self._queue.put_nowait(pending)
        except asyncio.QueueFull:
            raise DurableBatchFull(self._name) from None
        self._report_depth()
        try:
            return await asyncio.shield(pending.result)
        except asyncio.CancelledError:
            pending.result.cancel()
            raise

    async def close(self, *, timeout_seconds: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._task
        if task is None:
            self._fail_queued(DurableBatchClosed(self._name))
            return
        try:
            async with asyncio.timeout(timeout_seconds):
                await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            self._fail_queued(DurableBatchClosed(f"{self._name} stopped unexpectedly"))
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._fail_queued(DurableBatchClosed(f"{self._name} drain timed out"))
        finally:
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._closed or not self._queue.empty():
                try:
                    first = await asyncio.wait_for(self._queue.get(), timeout=0.050)
                except TimeoutError:
                    continue
                batch = [first]
                deadline = asyncio.get_running_loop().time() + self._dwell_seconds
                while len(batch) < self._max_batch_size:
                    if self._queue.empty():
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            break
                        try:
                            item = await asyncio.wait_for(self._queue.get(), remaining)
                        except TimeoutError:
                            break
                    else:
                        item = self._queue.get_nowait()
                    batch.append(item)
                self._report_depth()
                selected = [item for item in batch if not item.result.cancelled()]
                if not selected:
                    continue
                if self._observe_queue_wait is not None:
                    collected_at = asyncio.get_running_loop().time()
                    for item in selected:
                        self._observe_queue_wait(max(0.0, collected_at - item.enqueued_at))
                try:
                    outputs = await self._handler([item.value for item in selected])
                    if len(outputs) != len(selected):
                        raise RuntimeError("durable batch handler returned the wrong result count")
                except BaseException as exc:
                    for item in selected:
                        if not item.result.done():
                            item.result.set_exception(exc)
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                else:
                    for item, output in zip(selected, outputs, strict=True):
                        if not item.result.done():
                            item.result.set_result(output)
        except asyncio.CancelledError:
            self._fail_queued(DurableBatchClosed(f"{self._name} was cancelled"))
            raise
        except BaseException as exc:
            self._fail_queued(exc)
            raise

    def _fail_queued(self, exc: BaseException) -> None:
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if not item.result.done():
                item.result.set_exception(exc)
        self._report_depth()

    def _report_depth(self) -> None:
        if self._set_queue_depth is not None:
            self._set_queue_depth(self._queue.qsize())
