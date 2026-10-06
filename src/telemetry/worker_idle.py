"""Bound empty outbox polling without delaying local durable wakeups."""

from __future__ import annotations

import asyncio
import math


class IdleWorkerPoll:
    def __init__(self, wake: asyncio.Event) -> None:
        self._wake = wake
        self._delay = 0.0

    def reset(self) -> None:
        self._delay = 0.0

    def begin_claim(self) -> None:
        # A durable enqueue during the claim must remain visible to the wait.
        self._wake.clear()

    def next_delay(self, interval: float) -> float:
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("worker flush interval must be finite and positive")
        self._delay = min(max(interval, 1.0), max(interval, self._delay * 2))
        return self._delay

    async def wait(self, interval: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self.next_delay(interval))
        except TimeoutError:
            return
        self.reset()
