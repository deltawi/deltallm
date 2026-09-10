from __future__ import annotations

import asyncio


class CapacityGateFull(RuntimeError):
    """Raised when both operation execution slots and waiter slots are full."""


class CapacityGateTimedOut(RuntimeError):
    """Raised when a bounded operation waiter cannot acquire before its deadline."""


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
