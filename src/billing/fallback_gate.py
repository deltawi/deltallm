from __future__ import annotations

import asyncio


class FallbackGateFull(RuntimeError):
    """Raised when both fallback execution slots and waiter slots are full."""


class FallbackGateTimedOut(RuntimeError):
    """Raised when a bounded fallback waiter cannot acquire before its deadline."""


class BoundedFallbackGate:
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
                    raise FallbackGateFull("synchronous spend fallback capacity is full")
                self._waiters += 1
                try:
                    async with asyncio.timeout(max(0.001, float(timeout_seconds))):
                        await self._condition.wait_for(lambda: self._active < self._concurrency)
                except TimeoutError as exc:
                    raise FallbackGateTimedOut(
                        "synchronous spend fallback queue deadline exceeded"
                    ) from exc
                finally:
                    self._waiters -= 1
            self._active += 1

    async def release(self) -> None:
        async with self._condition:
            if self._active <= 0:
                raise RuntimeError("synchronous spend fallback gate released without ownership")
            self._active -= 1
            self._condition.notify_all()

    async def reconfigure(self, *, concurrency: int, max_waiters: int) -> None:
        async with self._condition:
            self._concurrency = max(1, int(concurrency))
            self._max_waiters = max(0, int(max_waiters))
            self._condition.notify_all()
