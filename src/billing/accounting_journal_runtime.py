"""Supervise bounded canonical processing without moving durable proof to memory."""

from __future__ import annotations

import asyncio
import math
import random
from typing import Protocol

from pydantic import Field

from src.billing.accounting_journal_claims import JournalClaim, JournalFailure
from src.billing.selector_charge import FrozenBillingContract, Identifier
from src.concurrency import BoundedCapacityGate
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.db.telemetry_acceptance import AcceptanceFailure
from src.metrics.accounting_journal import JournalAction, JournalActionOutcome, journal_action
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    wait_for_startup,
)


MAX_RETAINED_CLAIM_BYTES = 16 * 1024


class JournalWorkerConfig(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    worker_id: Identifier
    batch_size: int = Field(default=128, ge=1, le=256)
    lease_seconds: int = Field(default=30, ge=5, le=300)
    poll_seconds: float = Field(default=0.05, ge=0.01, le=5, allow_inf_nan=False)
    call_budget_seconds: float = Field(default=1.0, ge=0.02, le=2, allow_inf_nan=False)
    backoff_max_seconds: float = Field(default=5.0, ge=5, le=30, allow_inf_nan=False)


class JournalProcessingPersistence(Protocol):
    async def claim(
        self,
        *,
        generation: int,
        worker_id: str,
        expires_at: float,
        limit: int,
        lease_seconds: int,
    ) -> JournalClaim: ...

    async def materialize(self, claim: JournalClaim, *, expires_at: float) -> int: ...

    async def fail(
        self, claim: JournalClaim, failure: JournalFailure, *, expires_at: float
    ) -> int: ...


class JournalProcessingWorker:
    """Own one task and one fenced handle; PostgreSQL retains all financial facts."""

    def __init__(
        self, persistence: JournalProcessingPersistence, config: JournalWorkerConfig
    ) -> None:
        self._config = JournalWorkerConfig(
            generation=config.generation,
            worker_id=config.worker_id,
            batch_size=config.batch_size,
            lease_seconds=config.lease_seconds,
            poll_seconds=config.poll_seconds,
            call_budget_seconds=config.call_budget_seconds,
            backoff_max_seconds=config.backoff_max_seconds,
        )
        self._persistence = persistence
        self._gate = BoundedCapacityGate(concurrency=1, max_waiters=0)
        self._claim: JournalClaim | None = None
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._wake = asyncio.Event()
        self._closing = False
        self._state = WorkerState.STARTING
        self._failures = 0
        self._stop_failed = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        return () if self._task is None else (self._task,)

    @property
    def retained_claim(self) -> JournalClaim | None:
        return self._claim

    @property
    def retained_claim_bytes(self) -> int:
        return 0 if self._claim is None else MAX_RETAINED_CLAIM_BYTES

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
            if self._task.cancelled():
                return WorkerHealth(WorkerState.FAILED, "task_cancelled")
            if self._task.exception() is not None:
                return WorkerHealth(WorkerState.FAILED, "task_failed")
            return WorkerHealth(WorkerState.FAILED, "task_stopped")
        detail = "persistence_unavailable" if self._state is WorkerState.DEGRADED else None
        return WorkerHealth(self._state, detail)

    def stop_claims(self) -> None:
        if not self._closing:
            self._stop_failed = self.worker_health.state is WorkerState.FAILED
        self._closing = True
        self._state = WorkerState.STOPPING
        self._wake.set()

    async def start(self, *, expires_at: float) -> None:
        _deadline(expires_at)
        if self._closing:
            raise RuntimeError("journal processing cannot restart after close")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="accounting-journal-processing")
        try:
            await wait_for_startup(
                started=self._started,
                task=self._task,
                timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time()),
                worker_name="journal processing worker",
            )
            if not self.worker_health.ready:
                raise RuntimeError("journal processing worker did not start")
        except BaseException:
            self._closing = True
            self._state = WorkerState.FAILED
            await stop_tasks_before_deadline((self._task,), deadline=expires_at, cancel_first=True)
            raise

    async def close(self, *, expires_at: float) -> bool:
        _deadline(expires_at)
        failed = self._stop_failed or self.worker_health.state is WorkerState.FAILED
        self.stop_claims()
        stopped = await stop_tasks_before_deadline((self._task,), deadline=expires_at)
        stopped = stopped and not self._gate.active and not failed
        self._state = WorkerState.DISABLED if stopped else WorkerState.FAILED
        # A stopped task does not mean the durable backlog has drained. Its
        # accepted documents and charges remain owned by the database.
        return stopped

    async def run_once(self, *, expires_at: float) -> int:
        _deadline(expires_at)
        _require_time(expires_at)
        if self._closing:
            raise RuntimeError("journal processing is closed")
        await self._gate.acquire(
            timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time())
        )
        try:
            async with asyncio.timeout_at(expires_at):
                if self._claim is None:
                    self._claim = await self._claim_next(expires_at=expires_at)
                if not self._claim.sequences:
                    self._claim = None
                    return 0
                _require_time(expires_at)
                return await self._materialize(expires_at=expires_at)
        finally:
            await self._gate.release()

    async def _claim_next(self, *, expires_at: float) -> JournalClaim:
        with journal_action(JournalAction.CLAIM) as observed:
            claimed = await self._persistence.claim(
                generation=self._config.generation,
                worker_id=self._config.worker_id,
                limit=self._config.batch_size,
                lease_seconds=self._config.lease_seconds,
                expires_at=expires_at,
            )
            claimed = _validated_claim(claimed, self._config)
            if not claimed.sequences:
                observed.outcome = JournalActionOutcome.EMPTY
            return claimed

    async def _materialize(self, *, expires_at: float) -> int:
        claim = self._claim
        if claim is None:
            raise RuntimeError("journal processing has no owned claim")
        try:
            with journal_action(JournalAction.MATERIALIZE) as observed:
                count = await self._persistence.materialize(claim, expires_at=expires_at)
                _validated_count(count, limit=len(claim.sequences), complete=True)
                if not count:
                    observed.outcome = JournalActionOutcome.STALE
        except (AccountingProtocolUnavailable, TimeoutError):
            with journal_action(JournalAction.FAILURE) as observed:
                failed = await self._persistence.fail(
                    claim, JournalFailure.PERSISTENCE, expires_at=expires_at
                )
                _validated_count(failed, limit=len(claim.sequences), complete=False)
                if not failed:
                    observed.outcome = JournalActionOutcome.STALE
            self._claim = None
            raise
        _require_time(expires_at)
        self._claim = None
        return count

    async def _run(self) -> None:
        while not self._closing:
            deadline = asyncio.get_running_loop().time() + self._config.call_budget_seconds
            try:
                count = await self.run_once(expires_at=deadline)
                self._state = WorkerState.STOPPING if self._closing else WorkerState.READY
                self._failures = 0
            except (AccountingProtocolUnavailable, TimeoutError):
                self._state = WorkerState.STOPPING if self._closing else WorkerState.DEGRADED
                self._failures = min(8, self._failures + 1)
                count = 0
            self._started.set()
            if count:
                await asyncio.sleep(0)
                continue
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


def _validated_claim(value: JournalClaim, config: JournalWorkerConfig) -> JournalClaim:
    if type(value) is not JournalClaim:
        raise invalid_result()
    try:
        frozen = JournalClaim(
            protocol_generation=value.protocol_generation,
            worker_id=value.worker_id,
            lease_token=value.lease_token,
            sequences=value.sequences,
        )
    except ValueError:
        raise invalid_result() from None
    if (
        frozen.protocol_generation != config.generation
        or frozen.worker_id != config.worker_id
        or len(frozen.sequences) > config.batch_size
    ):
        raise invalid_result()
    return frozen


def _validated_count(value: int, *, limit: int, complete: bool) -> None:
    if type(value) is not int or not 0 <= value <= limit or (complete and value not in (0, limit)):
        raise invalid_result()


def _deadline(expires_at: float) -> None:
    if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
        raise ValueError("journal processing deadline is invalid")


def _require_time(expires_at: float) -> None:
    if expires_at <= asyncio.get_running_loop().time():
        raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)
