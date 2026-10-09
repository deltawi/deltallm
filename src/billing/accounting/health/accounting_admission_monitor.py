"""Cache one real dependency check; warm dispatch does not call a dependency."""

from __future__ import annotations

import asyncio
import math
import random
from typing import Protocol

from src.concurrency import CapacityGateFull
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.permits.accounting_permit_results import invalid_result
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    wait_for_startup,
)


class AdmissionObservation(Protocol):
    async def observe_ready(self, *, expires_at: float) -> bool: ...


class AccountingAdmissionMonitor:
    """One task and fixed scalars bound health age, calls, and shutdown."""

    def __init__(
        self,
        observation: AdmissionObservation,
        *,
        poll_seconds: float = 1.0,
        stale_seconds: float = 5.0,
        call_seconds: float = 0.5,
    ) -> None:
        for value in (poll_seconds, stale_seconds, call_seconds):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("accounting admission monitor bounds are invalid")
        if not 0.1 <= poll_seconds <= 2 or not 0.04 <= call_seconds <= 2:
            raise ValueError("accounting admission monitor call bounds are invalid")
        if not 2 * poll_seconds + call_seconds <= stale_seconds <= 10:
            raise ValueError("accounting admission monitor freshness is invalid")
        self._observation = observation
        self._poll = poll_seconds
        self._stale = stale_seconds
        self._call = call_seconds
        self._observed_at = 0.0
        self._state = WorkerState.STARTING
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._wake = asyncio.Event()
        self._busy = False
        self._closed = False
        self._stop_failed = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def worker_health(self) -> WorkerHealth:
        if self._closed:
            if self._stop_failed:
                return WorkerHealth(WorkerState.FAILED, "admission_monitor_stopped")
            return WorkerHealth(WorkerState.STOPPING, "admission_monitor_closed")
        if self._task is None:
            return WorkerHealth(WorkerState.STARTING, "admission_monitor_not_started")
        if self._task is not None and self._task.done():
            if not self._task.cancelled():
                self._task.exception()
            return WorkerHealth(WorkerState.FAILED, "admission_monitor_stopped")
        if self._state is WorkerState.READY:
            age = asyncio.get_running_loop().time() - self._observed_at
            if not math.isfinite(age) or not 0 <= age < self._stale:
                return WorkerHealth(WorkerState.DEGRADED, "admission_monitor_stale")
        return WorkerHealth(
            self._state, None if self._state is WorkerState.READY else "dependency_unready"
        )

    async def start(self, *, expires_at: float) -> None:
        _deadline(expires_at)
        if expires_at <= asyncio.get_running_loop().time():
            raise TimeoutError("accounting admission startup deadline has expired")
        if self._closed:
            raise RuntimeError("accounting admission monitor cannot restart after close")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="accounting-admission-monitor")
        try:
            await wait_for_startup(
                started=self._started,
                task=self._task,
                timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time()),
                worker_name="accounting admission monitor",
            )
            if self.worker_health.state is not WorkerState.READY:
                raise RuntimeError("accounting admission dependencies are not ready")
        except BaseException:
            await self.close(expires_at=expires_at)
            raise

    async def refresh(self, *, expires_at: float) -> bool:
        _deadline(expires_at)
        if self._busy:
            raise CapacityGateFull("accounting admission observation already has an owner")
        if self._closed:
            return False
        if expires_at <= asyncio.get_running_loop().time():
            self._state = WorkerState.DEGRADED
            return False
        self._busy = True
        # A refresh does not invalidate a still-fresh completed observation.
        observed_at = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout_at(expires_at):
                ready = await self._observation.observe_ready(expires_at=expires_at)
            if type(ready) is not bool:
                raise invalid_result()
            if self._closed or asyncio.get_running_loop().time() >= expires_at:
                self._state = WorkerState.DEGRADED
                return False
            self._observed_at = observed_at
            self._state = WorkerState.READY if ready else WorkerState.DEGRADED
            return ready
        except asyncio.CancelledError:
            self._state = WorkerState.DEGRADED
            raise
        except (AccountingProtocolUnavailable, TimeoutError):
            self._state = WorkerState.DEGRADED
            return False
        except Exception:
            self._state = WorkerState.DEGRADED
            raise
        finally:
            self._busy = False

    async def close(self, *, expires_at: float) -> bool:
        _deadline(expires_at)
        if not self._closed:
            self._stop_failed = self.worker_health.state is WorkerState.FAILED
        self._closed = True
        self._wake.set()
        stopped = await stop_tasks_before_deadline((self._task,), deadline=expires_at)
        return stopped and not self._busy and not self._stop_failed

    async def _run(self) -> None:
        while not self._closed:
            await self.refresh(expires_at=asyncio.get_running_loop().time() + self._call)
            self._started.set()
            self._wake.clear()
            if self._closed:
                return
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=random.uniform(self._poll * 0.8, self._poll * 1.2)
                )
            except TimeoutError:
                pass


def _deadline(expires_at: float) -> None:
    if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
        raise ValueError("accounting admission deadline is invalid")
