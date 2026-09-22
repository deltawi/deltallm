"""One process-local admission state and monotonic shutdown deadline."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Literal

from src.lifecycle_settings import LifecycleSettings
from src.metrics.lifecycle import process_state, shutdown_phases

logger = logging.getLogger(__name__)
Phase = Literal["withdrawal", "responses", "cancellation", "workers", "close"]
PHASES: tuple[Phase, ...] = ("withdrawal", "responses", "cancellation", "workers", "close")


class ProcessState(StrEnum):
    STARTING = "starting"
    SERVING = "serving"
    DRAINING = "draining"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True)
class ShutdownDeadlines:
    started: float
    withdrawal: float
    responses: float
    cancellation: float
    workers: float
    close: float
    total: float

    @classmethod
    def build(cls, settings: LifecycleSettings, now: float) -> ShutdownDeadlines:
        withdrawal = now + settings.lifecycle_withdrawal_seconds
        responses = withdrawal + settings.lifecycle_request_drain_seconds
        cancellation = responses + settings.lifecycle_cancellation_seconds
        workers = cancellation + settings.lifecycle_worker_drain_seconds
        return cls(
            now,
            withdrawal,
            responses,
            cancellation,
            workers,
            workers + settings.lifecycle_close_seconds,
            now + settings.lifecycle_shutdown_seconds,
        )


class ProcessLifecycle:
    def __init__(
        self, settings: LifecycleSettings, *, clock: Callable[[], float] = monotonic
    ) -> None:
        self.settings = settings
        self._clock = clock
        self.state = ProcessState.STARTING
        self._report_state()
        self.deadlines: ShutdownDeadlines | None = None
        self.on_drain: Callable[[ShutdownDeadlines], None] | None = None
        # Only bootstrap-owned synchronous stop-claim callbacks are registered.
        self._stop_claims: list[Callable[[], None]] = []
        self.producers: list[asyncio.Task[object]] = []
        self._phase: Phase | None = None
        self._phase_started = 0.0

    def enter_phase(self, phase: Phase) -> None:
        if self._phase is not None and PHASES.index(phase) <= PHASES.index(self._phase):
            return
        self._finish_phase()
        self._phase = phase
        self._phase_started = self._clock()

    def _finish_phase(self) -> None:
        if self._phase is not None:
            elapsed = max(0, self._clock() - self._phase_started)
            shutdown_phases.labels(self._phase).observe(elapsed)
            logger.info(
                "process shutdown phase completed phase=%s seconds=%.3f", self._phase, elapsed
            )
            self._phase = None

    @property
    def ready(self) -> bool:
        return self.state == ProcessState.SERVING

    @property
    def draining(self) -> bool:
        return self.deadlines is not None

    def mark_serving(self) -> None:
        if self.state == ProcessState.STARTING:
            self.state = ProcessState.SERVING
            self._report_state()

    def register_claim_stop(self, stop: Callable[[], None]) -> None:
        if self.draining:
            stop()
        else:
            if len(self._stop_claims) >= 16:
                raise ValueError("process claim-stop inventory exceeds its allocation")
            self._stop_claims.append(stop)

    def register_producer(self, stop: Callable[[], None], task: asyncio.Task[object]) -> None:
        if len(self.producers) >= 16:
            raise ValueError("process producer inventory exceeds its allocation")
        self.producers.append(task)
        self.register_claim_stop(stop)

    def begin_drain(self) -> ShutdownDeadlines:
        if self.deadlines is None:
            self.deadlines = ShutdownDeadlines.build(self.settings, self._clock())
            self.state = ProcessState.DRAINING
            self.enter_phase("withdrawal")
            self._report_state()
            if self.on_drain is not None:
                self.on_drain(self.deadlines)
            callbacks, self._stop_claims = self._stop_claims, []
            for stop in callbacks:
                try:
                    stop()
                except Exception:
                    # Callback exceptions may contain configuration secrets.
                    logger.error("process claim stop failed")
            logger.info("process draining")
        return self.deadlines

    def begin_stopping(self) -> ShutdownDeadlines:
        deadlines = self.begin_drain()
        if self.state != ProcessState.STOPPED:
            self.enter_phase("workers")
            self.state = ProcessState.STOPPING
            self._report_state()
        return deadlines

    def mark_stopped(self) -> None:
        self.begin_stopping()
        self.state = ProcessState.STOPPED
        self._finish_phase()
        self._report_state()

    def _report_state(self) -> None:
        for state in ProcessState:
            process_state.labels(state.value).set(int(state == self.state))

    def remaining(self, deadline: float) -> float:
        return max(0.0, deadline - self._clock())
