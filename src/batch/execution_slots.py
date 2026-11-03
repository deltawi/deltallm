"""Pod-wide execution slots shared by the work slices one batch worker runs.

A batch worker can run more than one claimed slice at a time. Every slice
takes its runners from the same pool, so a pod never has more than
``worker_concurrency`` chat preparations or work units running, however many
slices are open. Each open slice reports how many of its items have not
started yet. The worker claims the next slice only when nothing is waiting and
a slot is free, so a new slice only fills slots the previous slice's tail left
idle.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token


class SliceTracker:
    """Items of one open slice that have not started a work unit yet."""

    __slots__ = ("waiting",)

    def __init__(self, waiting: int) -> None:
        self.waiting = max(0, int(waiting))


_current_slice: ContextVar[SliceTracker | None] = ContextVar(
    "batch_current_slice_tracker", default=None
)


class BatchExecutionSlots:
    def __init__(
        self,
        *,
        capacity: Callable[[], int],
        on_change: Callable[[int, int], None] | None = None,
    ) -> None:
        self._capacity = capacity
        self._on_change = on_change
        self._active = 0
        # First come, first served: a release hands its slot to one waiter.
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._slices: list[SliceTracker] = []
        self._changed = asyncio.Event()

    @property
    def capacity(self) -> int:
        return max(1, int(self._capacity() or 1))

    @property
    def active(self) -> int:
        return self._active

    @property
    def free(self) -> int:
        return max(0, self.capacity - self._active)

    @property
    def waiting(self) -> int:
        return sum(tracker.waiting for tracker in self._slices)

    @property
    def open_slices(self) -> int:
        return len(self._slices)

    def open_slice(self, items: int) -> SliceTracker:
        tracker = SliceTracker(items)
        self._slices.append(tracker)
        self._notify()
        return tracker

    def close_slice(self, tracker: SliceTracker) -> None:
        tracker.waiting = 0
        if tracker in self._slices:
            self._slices.remove(tracker)
        self._notify()

    @staticmethod
    def bind_slice(tracker: SliceTracker | None) -> Token[SliceTracker | None]:
        return _current_slice.set(tracker)

    @staticmethod
    def unbind_slice(token: Token[SliceTracker | None]) -> None:
        _current_slice.reset(token)

    def set_waiting(self, items: int) -> None:
        """Reset the current slice's waiting count, e.g. after its items load."""
        tracker = _current_slice.get()
        if tracker is None:
            return
        tracker.waiting = max(0, int(items))
        self._notify()

    def settle(self, items: int) -> None:
        """Count items of the current slice that finished without a work unit."""
        tracker = _current_slice.get()
        if tracker is None or items <= 0:
            return
        tracker.waiting = max(0, tracker.waiting - int(items))
        self._notify()

    @asynccontextmanager
    async def unit(self, items: int = 0) -> AsyncIterator[None]:
        """Hold one slot for a work unit that starts ``items`` waiting items."""
        await self._acquire()
        tracker = _current_slice.get()
        if tracker is not None and items > 0:
            tracker.waiting = max(0, tracker.waiting - int(items))
        self._notify()
        try:
            yield
        finally:
            # Synchronous release: a cancelled unit can never strand its slot.
            self._release()

    async def _acquire(self) -> None:
        if not self._waiters and self._active < self.capacity:
            self._active += 1
            return
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            # The releasing unit counts this slot as ours before waking us.
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                self._release()
            else:
                with contextlib.suppress(ValueError):
                    self._waiters.remove(waiter)
            raise

    def _release(self) -> None:
        self._active -= 1
        while self._waiters and self._active < self.capacity:
            waiter = self._waiters.popleft()
            if not waiter.done():
                self._active += 1
                waiter.set_result(None)
        self._notify()

    async def wait_changed(self, timeout: float | None = None) -> None:
        event = self._changed
        try:
            async with asyncio.timeout(None if timeout is None else max(0.0, timeout)):
                await event.wait()
        except TimeoutError:
            return

    def notify(self) -> None:
        self._notify(publish=False)

    def publish(self) -> None:
        if self._on_change is not None:
            self._on_change(self._active, self.capacity)

    def _notify(self, *, publish: bool = True) -> None:
        # Only the worker's claim loop waits here; swapping wakes it once.
        event, self._changed = self._changed, asyncio.Event()
        event.set()
        if publish:
            self.publish()
