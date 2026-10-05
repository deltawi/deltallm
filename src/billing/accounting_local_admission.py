"""Bound local admission waiters and release ownership without an await."""

from __future__ import annotations

import asyncio
import math

from src.concurrency import CapacityGateFull, CapacityGateTimedOut


class LocalAdmissionOwner:
    """The local issuer must hold this owner through funding and issue commit."""

    def __init__(self, *, max_waiters: int = 1) -> None:
        if type(max_waiters) is not int or not 0 <= max_waiters <= 1:
            raise ValueError("local admission must have zero or one waiter")
        self._max_waiters = max_waiters
        self._lock = asyncio.Lock()
        self._waiters = 0

    @property
    def active(self) -> bool:
        return self._lock.locked()

    @property
    def waiters(self) -> int:
        return self._waiters

    async def acquire(self, *, expires_at: float) -> None:
        remaining = expires_at - asyncio.get_running_loop().time()
        if not math.isfinite(remaining) or remaining <= 0:
            raise CapacityGateTimedOut("local admission deadline expired")
        waiting = self._lock.locked() or self._waiters > 0
        if waiting and self._waiters >= self._max_waiters:
            raise CapacityGateFull("local admission waiter capacity is full")
        self._waiters += int(waiting)
        try:
            # Direct acquisition avoids a second lock task at cancellation.
            async with asyncio.timeout(remaining):
                await self._lock.acquire()
        except TimeoutError:
            raise CapacityGateTimedOut("local admission deadline expired") from None
        finally:
            self._waiters -= int(waiting)

    def release(self) -> None:
        self._lock.release()
