"""Supervise native recovery independently from inference and reporting."""

from __future__ import annotations

import asyncio
import math
import random
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from src.billing.accounting_health import AccountingBacklogProbe
from src.billing.selector_charge import FrozenBillingContract
from src.concurrency import BoundedCapacityGate
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.db.telemetry_acceptance import AcceptanceFailure
from src.metrics.accounting import increment_accounting_projection
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    wait_for_startup,
)


class RecoveryAction(StrEnum):
    EXPIRED_GRANTS = "expired_grants"
    EXPIRED_OPERATIONS = "expired_operations"
    SETTLE_GRANTS = "settle_grants"
    ROLL_WINDOWS = "roll_windows"


class RecoveryConfig(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    batch_size: int = Field(default=128, ge=1, le=256)
    poll_seconds: float = Field(default=1, ge=0.1, le=2, allow_inf_nan=False)
    call_budget_seconds: float = Field(default=1, ge=0.04, le=2, allow_inf_nan=False)
    backoff_max_seconds: float = Field(default=5, ge=5, le=30, allow_inf_nan=False)


class RecoveryPersistence(Protocol):
    async def recover(
        self, action: RecoveryAction, *, generation: int, limit: int, expires_at: float
    ) -> int: ...


class RecoveryObserver(Protocol):
    generation: int

    async def observe(self, *, expires_at: float) -> None: ...


class AccountingRecoveryWorker:
    """Own one task and one cached probe; stopping is not a global drain proof."""

    def __init__(
        self,
        persistence: RecoveryPersistence,
        probe: AccountingBacklogProbe,
        config: RecoveryConfig,
        *,
        observer: RecoveryObserver | None = None,
    ) -> None:
        self._config = RecoveryConfig(
            generation=config.generation,
            batch_size=config.batch_size,
            poll_seconds=config.poll_seconds,
            call_budget_seconds=config.call_budget_seconds,
            backoff_max_seconds=config.backoff_max_seconds,
        )
        self._persistence = persistence
        if observer is not None and (
            type(observer.generation) is not int or observer.generation != config.generation
        ):
            raise ValueError("accounting recovery observer uses another generation")
        self._observer = observer
        self._probe = probe
        self._gate = BoundedCapacityGate(concurrency=1, max_waiters=0)
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._wake = asyncio.Event()
        self._closing = False
        self._state = WorkerState.STARTING
        self._failures = 0
        self._stop_failed = False
        self._checked = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def probe(self) -> AccountingBacklogProbe:
        return self._probe

    @property
    def dependencies_checked(self) -> bool:
        return self._checked

    @property
    def worker_health(self) -> WorkerHealth:
        if self._state is WorkerState.DISABLED:
            return WorkerHealth(self._state)
        if self._state is WorkerState.FAILED:
            return WorkerHealth(self._state, "runtime_failed")
        if self._task is not None and self._task.done():
            if (
                self._closing
                and not self._stop_failed
                and not self._task.cancelled()
                and self._task.exception() is None
            ):
                return WorkerHealth(WorkerState.STOPPING)
            detail = "task_cancelled" if self._task.cancelled() else "task_stopped"
            if not self._task.cancelled() and self._task.exception() is not None:
                detail = "task_failed"
            return WorkerHealth(WorkerState.FAILED, detail)
        if self._state is WorkerState.READY:
            return self._probe.worker_health
        detail = "recovery_unavailable" if self._state is WorkerState.DEGRADED else None
        return WorkerHealth(self._state, detail)

    def stop_claims(self) -> None:
        if not self._closing:
            self._stop_failed = self.worker_health.state is WorkerState.FAILED
        self._closing = True
        self._state = WorkerState.STOPPING
        self._probe.close()
        self._wake.set()

    async def start(self, *, expires_at: float) -> None:
        _deadline(expires_at)
        if self._closing:
            raise RuntimeError("accounting recovery cannot restart after close")
        if expires_at <= asyncio.get_running_loop().time():
            raise TimeoutError("accounting recovery startup deadline has expired")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="accounting-native-recovery")
        try:
            await wait_for_startup(
                started=self._started,
                task=self._task,
                timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time()),
                worker_name="accounting recovery worker",
            )
            health = self.worker_health
            if health.state is not WorkerState.READY and not (
                self._checked and health.state is WorkerState.DEGRADED
            ):
                raise RuntimeError("accounting recovery worker did not start")
        except BaseException:
            self._closing = True
            self._state = WorkerState.FAILED
            self._probe.close()
            await stop_tasks_before_deadline((self._task,), deadline=expires_at, cancel_first=True)
            raise

    async def run_once(self, *, expires_at: float) -> int:
        _deadline(expires_at)
        if expires_at <= asyncio.get_running_loop().time():
            raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)
        if self._closing:
            raise RuntimeError("accounting recovery is closed")
        await self._gate.acquire(
            timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time())
        )
        try:
            self._checked = False
            async with asyncio.timeout_at(expires_at):
                count = 0
                for action in RecoveryAction:
                    # Separate statements release each lock domain before the
                    # next starts. No provider or reporting work runs here.
                    recovered = await self._persistence.recover(
                        action,
                        generation=self._config.generation,
                        limit=self._config.batch_size,
                        expires_at=expires_at,
                    )
                    if type(recovered) is not int or not 0 <= recovered <= self._config.batch_size:
                        raise invalid_result()
                    increment_accounting_projection(action.value, "success", recovered)
                    count += recovered
                ready = await self._probe.refresh(expires_at=expires_at)
                if not ready and self._probe.worker_health.detail not in {
                    "terminal_age_limit",
                    "terminal_backlog_limit",
                    "terminal_capacity",
                }:
                    raise AccountingProtocolUnavailable(AcceptanceFailure.DATABASE_UNAVAILABLE)
                self._checked = True
                if self._observer is not None:
                    await self._observer.observe(expires_at=expires_at)
                return count
        finally:
            await self._gate.release()

    async def close(self, *, expires_at: float) -> bool:
        _deadline(expires_at)
        failed = self._stop_failed or self.worker_health.state is WorkerState.FAILED
        self.stop_claims()
        stopped = await stop_tasks_before_deadline((self._task,), deadline=expires_at)
        stopped = stopped and not self._gate.active and not failed
        self._state = WorkerState.DISABLED if stopped else WorkerState.FAILED
        return stopped

    async def _run(self) -> None:
        while not self._closing:
            deadline = asyncio.get_running_loop().time() + self._config.call_budget_seconds
            try:
                await self.run_once(expires_at=deadline)
                self._state = WorkerState.STOPPING if self._closing else WorkerState.READY
                self._failures = 0
            except (AccountingProtocolUnavailable, TimeoutError):
                self._state = WorkerState.STOPPING if self._closing else WorkerState.DEGRADED
                self._failures = min(8, self._failures + 1)
                increment_accounting_projection("recovery_tick", "unavailable")
            self._started.set()
            self._wake.clear()
            if self._closing:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._next_delay())
            except TimeoutError:
                pass

    def _next_delay(self) -> float:
        base = self._config.poll_seconds
        if self._failures:
            base = min(self._config.backoff_max_seconds, base * 2**self._failures)
            return random.uniform(base / 2, base)
        return random.uniform(base * 0.8, base * 1.2)


def _deadline(expires_at: float) -> None:
    if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
        raise ValueError("accounting recovery deadline is invalid")
