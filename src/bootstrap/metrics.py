"""Own one inexpensive timer per process; never query dependencies from a metric scrape."""

from __future__ import annotations

import asyncio

from src.metrics.runtime import event_loop_lag, event_loop_last_lag, event_loop_samplers

SAMPLE_INTERVAL_SECONDS = 1.0


class RuntimeMetricSampler:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._timer: asyncio.TimerHandle | None = None
        self._due = 0.0
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._schedule()
        self._running = True
        event_loop_samplers.inc()

    def _schedule(self) -> None:
        self._due = self._loop.time() + SAMPLE_INTERVAL_SECONDS
        self._timer = self._loop.call_later(SAMPLE_INTERVAL_SECONDS, self._sample)

    def _sample(self) -> None:
        if not self._running:
            return
        lag = max(0.0, self._loop.time() - self._due)
        try:
            event_loop_lag.observe(lag)
            event_loop_last_lag.set(lag)
        finally:
            if self._running:
                self._schedule()

    def close(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        event_loop_samplers.dec()


def start_runtime_metrics() -> RuntimeMetricSampler:
    sampler = RuntimeMetricSampler(asyncio.get_running_loop())
    sampler.start()
    return sampler
