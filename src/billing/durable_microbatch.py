"""Bounded, cancellation-safe microbatching for durable accounting ACKs."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from src.billing.durable_batch_bytes import DurableBatchBytes

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
    payload_bytes: int
    retained: bool = True


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
        payload_size: Callable[[Input], int] | None = None,
        max_batch_bytes: int | None = None,
        max_retained_bytes: int | None = None,
        set_retained_bytes: Callable[[int], None] | None = None,
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
        self._set_retained_bytes = set_retained_bytes
        self._max_pending = max_pending
        self._bytes = DurableBatchBytes(
            payload_size, max_batch_bytes=max_batch_bytes, max_retained_bytes=max_retained_bytes
        )
        self._queue: OrderedDict[asyncio.Future[Output], _Pending[Input, Output]] = OrderedDict()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def retained_bytes(self) -> int:
        return self._bytes.retained

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
        size = self._bytes.measure(value)
        if self.pending >= self._max_pending or not self._bytes.fits(size):
            raise DurableBatchFull(self._name) from None
        pending = _Pending(
            value=value, result=loop.create_future(), enqueued_at=loop.time(), payload_bytes=size
        )
        self._queue[pending.result] = pending
        self._bytes.add(size)
        self._wake.set()
        try:
            self._report_depth()
            return await asyncio.shield(pending.result)
        except asyncio.CancelledError:
            pending.result.cancel()
            if self._queue.pop(pending.result, None) is not None:
                self._release(pending)
                self._report_depth()
            raise
        except BaseException:
            if self._queue.pop(pending.result, None) is not None:
                self._release(pending)
            raise

    async def close(self, *, timeout_seconds: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._wake.set()
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
            while not self._closed or self._queue:
                batch: list[_Pending[Input, Output]] = []
                try:
                    await self._collect(batch)
                    await self._dispatch(batch)
                except BaseException as exc:
                    for item in batch:
                        if not item.result.done():
                            failure = (
                                DurableBatchClosed(f"{self._name} was cancelled")
                                if isinstance(exc, asyncio.CancelledError)
                                else exc
                            )
                            item.result.set_exception(failure)
                    raise
                finally:
                    for item in batch:
                        self._release(item)
                    self._report_depth()
        except asyncio.CancelledError:
            self._fail_queued(DurableBatchClosed(f"{self._name} was cancelled"))
            raise
        except BaseException as exc:
            self._fail_queued(exc)
            raise

    def _fail_queued(self, exc: BaseException) -> None:
        while self._queue:
            _, item = self._queue.popitem(last=False)
            self._release(item)
            if not item.result.done():
                item.result.set_exception(exc)
        self._report_depth()

    def _report_depth(self) -> None:
        if self._set_queue_depth is not None:
            self._set_queue_depth(self.pending)
        if self._set_retained_bytes is not None:
            self._set_retained_bytes(self.retained_bytes)

    def _release(self, item: _Pending[Input, Output]) -> None:
        if item.retained:
            self._bytes.release(item.payload_bytes)
            item.retained = False

    async def _collect(self, batch: list[_Pending[Input, Output]]) -> None:
        if not self._queue:
            self._wake.clear()
            if self._closed:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.050)
            except TimeoutError:
                return
        deadline = asyncio.get_running_loop().time() + self._dwell_seconds
        size = 2  # JSON list delimiters; commas are charged only after the first item.
        while len(batch) < self._max_batch_size:
            if not self._queue:
                remaining = deadline - asyncio.get_running_loop().time()
                if self._closed or remaining <= 0:
                    break
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), remaining)
                except TimeoutError:
                    break
                continue
            item = next(iter(self._queue.values()))
            if not self._bytes.batch_fits(size, item.payload_bytes, first=not batch):
                break
            self._queue.popitem(last=False)
            size += item.payload_bytes + bool(batch)
            batch.append(item)
            self._report_depth()

    async def _dispatch(self, batch: Sequence[_Pending[Input, Output]]) -> None:
        selected = [item for item in batch if not item.result.cancelled()]
        if not selected:
            return
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
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
        else:
            for item, output in zip(selected, outputs, strict=True):
                if not item.result.done():
                    item.result.set_result(output)
