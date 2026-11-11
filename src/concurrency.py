from __future__ import annotations

import asyncio
import heapq
import itertools
from dataclasses import dataclass, field


class CapacityGateFull(RuntimeError):
    """Raised when both operation execution slots and waiter slots are full."""


class CapacityGateTimedOut(RuntimeError):
    """Raised when a bounded operation waiter cannot acquire before its deadline."""


class CapacityGateDisplaced(CapacityGateFull):
    """Raised for a waiter whose waiting position a more urgent caller took."""


class BoundedCapacityGate:
    """Owns a live-reconfigurable concurrency limit and a bounded waiter set."""

    def __init__(self, *, concurrency: int, max_waiters: int) -> None:
        self._concurrency = max(1, int(concurrency))
        self._max_waiters = max(0, int(max_waiters))
        self._active = 0
        self._waiters = 0
        self._condition = asyncio.Condition()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiters(self) -> int:
        return self._waiters

    @property
    def concurrency(self) -> int:
        return self._concurrency

    @property
    def max_waiters(self) -> int:
        return self._max_waiters

    async def acquire(self, *, timeout_seconds: float) -> None:
        async with self._condition:
            if self._active >= self._concurrency:
                if self._waiters >= self._max_waiters:
                    raise CapacityGateFull("bounded operation capacity is full")
                self._waiters += 1
                try:
                    async with asyncio.timeout(max(0.001, float(timeout_seconds))):
                        await self._condition.wait_for(lambda: self._active < self._concurrency)
                except TimeoutError as exc:
                    raise CapacityGateTimedOut("bounded operation queue deadline exceeded") from exc
                finally:
                    self._waiters -= 1
            self._active += 1

    async def release(self) -> None:
        async with self._condition:
            if self._active <= 0:
                raise RuntimeError("bounded operation gate released without ownership")
            self._active -= 1
            self._condition.notify_all()

    async def reconfigure(self, *, concurrency: int, max_waiters: int) -> None:
        async with self._condition:
            self._concurrency = max(1, int(concurrency))
            self._max_waiters = max(0, int(max_waiters))
            self._condition.notify_all()


@dataclass(eq=False, slots=True)
class _PriorityWaiter:
    priority: int
    order: int
    capped: bool
    future: asyncio.Future[None] = field(repr=False)


class PriorityCapacityGate:
    """A bounded capacity gate that gives each free slot to the most urgent waiter.

    A lower ``priority`` number is more urgent. Waiters of the same priority
    get slots in arrival order. A released slot goes directly to the chosen
    waiter, so a new caller cannot take it first. When all waiting positions
    are taken, a caller that is more urgent than the least urgent waiter takes
    that waiter's position, and that waiter fails at once with
    ``CapacityGateDisplaced``. Capped work (``capped=True``) holds at most
    ``capped_slots`` slots at the same time.

    The gate never suspends while it changes its state, so it needs no lock.
    """

    def __init__(self, *, concurrency: int, max_waiters: int, capped_slots: int) -> None:
        self._concurrency = max(1, int(concurrency))
        self._max_waiters = max(0, int(max_waiters))
        self._capped_slots = max(1, int(capped_slots))
        self._active = 0
        self._capped_active = 0
        # A heap of (priority, order, waiter). Granted and withdrawn waiters
        # leave the live set at once and the heap when they reach its top.
        self._queue: list[tuple[int, int, _PriorityWaiter]] = []
        self._waiting: set[_PriorityWaiter] = set()
        self._order = itertools.count()

    @property
    def active(self) -> int:
        return self._active

    @property
    def capped_active(self) -> int:
        return self._capped_active

    @property
    def waiters(self) -> int:
        return len(self._waiting)

    @property
    def concurrency(self) -> int:
        return self._concurrency

    @property
    def max_waiters(self) -> int:
        return self._max_waiters

    @property
    def capped_slots(self) -> int:
        return self._capped_slots

    def waiters_at_or_above(self, priority: int) -> int:
        """Waiters whose priority is ``priority`` or more urgent."""
        return sum(1 for waiter in self._waiting if waiter.priority <= priority)

    async def acquire(
        self, *, timeout_seconds: float, priority: int = 0, capped: bool = False
    ) -> None:
        # After each change, no free slot is left while an eligible waiter
        # waits (see _dispatch). So a free slot here belongs to no waiter.
        if self._active < self._concurrency and (
            not capped or self._capped_active < self._capped_slots
        ):
            self._take(capped)
            return
        if len(self._waiting) >= self._max_waiters:
            self._displace_less_urgent(priority)
        loop = asyncio.get_running_loop()
        waiter = _PriorityWaiter(priority, next(self._order), capped, loop.create_future())
        heapq.heappush(self._queue, (priority, waiter.order, waiter))
        self._waiting.add(waiter)
        try:
            async with asyncio.timeout(max(0.001, float(timeout_seconds))):
                await waiter.future
        except TimeoutError as exc:
            if _granted(waiter.future):
                # The slot came together with the deadline. Keep it.
                return
            self._withdraw(waiter)
            raise CapacityGateTimedOut("bounded operation queue deadline exceeded") from exc
        except asyncio.CancelledError:
            if _granted(waiter.future):
                self._give_back(capped)
            else:
                self._withdraw(waiter)
            raise

    async def release(self, *, capped: bool = False) -> None:
        if self._active <= 0 or (capped and self._capped_active <= 0):
            raise RuntimeError("bounded operation gate released without ownership")
        self._give_back(capped)

    async def reconfigure(self, *, concurrency: int, max_waiters: int) -> None:
        self._concurrency = max(1, int(concurrency))
        self._max_waiters = max(0, int(max_waiters))
        self._dispatch()

    def _take(self, capped: bool) -> None:
        self._active += 1
        if capped:
            self._capped_active += 1

    def _give_back(self, capped: bool) -> None:
        self._active -= 1
        if capped:
            self._capped_active -= 1
        self._dispatch()

    def _displace_less_urgent(self, priority: int) -> None:
        # The newest of the least urgent waiters gives up its position: older
        # waiters are closer to a slot.
        victim = max(
            self._waiting, key=lambda waiter: (waiter.priority, waiter.order), default=None
        )
        if victim is None or victim.priority <= priority:
            raise CapacityGateFull("bounded operation capacity is full")
        self._waiting.discard(victim)
        if not victim.future.done():
            victim.future.set_exception(
                CapacityGateDisplaced("a more urgent caller took this waiting position")
            )
        self._compact()

    def _withdraw(self, waiter: _PriorityWaiter) -> None:
        self._waiting.discard(waiter)
        if waiter.future.done() and not waiter.future.cancelled():
            waiter.future.exception()  # Displaced while cancelled: mark it seen.
        self._compact()
        # A capped waiter that leaves can unblock no one, but an uncapped one
        # may have been the next choice. Recheck so no slot stays idle.
        self._dispatch()

    def _next_waiter(self) -> _PriorityWaiter | None:
        while self._queue and self._queue[0][2] not in self._waiting:
            heapq.heappop(self._queue)
        if not self._queue:
            return None
        first = self._queue[0][2]
        if not first.capped or self._capped_active < self._capped_slots:
            return first
        # The most urgent waiter is capped work at its cap. Serve the most
        # urgent uncapped waiter instead.
        return min(
            (waiter for waiter in self._waiting if not waiter.capped),
            key=lambda waiter: (waiter.priority, waiter.order),
            default=None,
        )

    def _dispatch(self) -> None:
        while self._active < self._concurrency:
            waiter = self._next_waiter()
            if waiter is None:
                return
            self._waiting.discard(waiter)
            if waiter.future.done():
                # Cancelled while it waited; its own task withdraws it.
                continue
            self._take(waiter.capped)
            waiter.future.set_result(None)

    def _compact(self) -> None:
        if len(self._queue) > 2 * len(self._waiting) + 32:
            self._queue = [entry for entry in self._queue if entry[2] in self._waiting]
            heapq.heapify(self._queue)


def _granted(future: asyncio.Future[None]) -> bool:
    return future.done() and not future.cancelled() and future.exception() is None
