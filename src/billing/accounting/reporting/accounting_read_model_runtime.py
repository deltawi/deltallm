"""Supervise native reporting with a fenced key page and no payload queue."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from time import monotonic
from typing import Protocol

from src.billing.accounting.reporting.accounting_read_model_claims import (
    READ_MODEL_HANDLE_BYTES,
    ReadModelClaim,
    ReadModelWorkerConfig,
)
from src.concurrency import BoundedCapacityGate
from src.billing.accounting.health.accounting_read_model_health import (
    ReadModelHealth,
    ReadModelProgress,
)
from src.db.accounting.accounting_calls import (
    AccountingProtocolUnavailable,
    retry_idempotent_startup_call,
)
from src.db.accounting.permits.accounting_permit_results import invalid_result
from src.metrics.accounting import increment_accounting_projection
from src.telemetry.worker_idle import IdleWorkerPoll
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    wait_for_startup,
)

logger = logging.getLogger(__name__)


class ReadModelPersistence(Protocol):
    async def initialize(self, *, generation: int, expires_at: float) -> None: ...

    async def claim(
        self,
        *,
        generation: int,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        expires_at: float,
    ) -> ReadModelClaim | None: ...

    async def materialize(self, claim: ReadModelClaim, *, expires_at: float) -> int: ...

    async def progress(self, *, generation: int, expires_at: float) -> ReadModelProgress: ...


class ReadModelProcessingWorker:
    def __init__(
        self,
        persistence: ReadModelPersistence,
        config: ReadModelWorkerConfig,
        *,
        progress_health: ReadModelHealth | None = None,
        observe_progress: bool = True,
    ) -> None:
        self._persistence = persistence
        self._config = ReadModelWorkerConfig.model_validate(config.model_dump())
        self._gate = BoundedCapacityGate(concurrency=1, max_waiters=0)
        self._task: asyncio.Task[None] | None = None
        self._claim: ReadModelClaim | None = None
        self._started = asyncio.Event()
        self._startup_deadline: float | None = None
        self._wake = asyncio.Event()
        self._idle = IdleWorkerPoll(self._wake)
        self._state = WorkerState.STARTING
        self._closing = False
        self._stop_failed = False
        self._failures = 0
        self._last_failure: str | None = None
        self._progress_health = progress_health or ReadModelHealth(self._config.generation)
        if self._progress_health.generation != self._config.generation:
            raise ValueError("Reporting lanes must share one protocol generation")
        self._observe_progress = observe_progress
        self._next_progress_at = 0.0

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        return () if self._task is None else (self._task,)

    @property
    def retained_claim(self) -> ReadModelClaim | None:
        return self._claim

    @property
    def retained_claim_bytes(self) -> int:
        return READ_MODEL_HANDLE_BYTES if self._claim is not None else 0

    @property
    def progress_health(self) -> ReadModelHealth:
        return self._progress_health

    @property
    def worker_health(self) -> WorkerHealth:
        if self._state is WorkerState.DISABLED:
            return WorkerHealth(self._state)
        if self._state is WorkerState.FAILED or self._stop_failed:
            return WorkerHealth(WorkerState.FAILED, "read_model_failed")
        task = self._task
        if task is not None and task.done():
            if not task.cancelled():
                error = task.exception()
                if self._closing and error is None:
                    return WorkerHealth(WorkerState.STOPPING)
            return WorkerHealth(WorkerState.FAILED, "read_model_task_stopped")
        if self._state is WorkerState.READY:
            return self._progress_health.worker_health
        detail = "read_model_unavailable" if self._state is WorkerState.DEGRADED else None
        return WorkerHealth(self._state, detail)

    def stop_claims(self) -> None:
        if not self._closing:
            self._stop_failed = self.worker_health.state is WorkerState.FAILED
        self._closing = True
        self._state = WorkerState.STOPPING
        self._wake.set()

    async def start(self, *, expires_at: float) -> None:
        _require_time(expires_at)
        if self._closing:
            raise RuntimeError("read-model processing cannot restart after close")
        try:
            async with asyncio.timeout_at(expires_at):
                await retry_idempotent_startup_call(
                    lambda: self._persistence.initialize(
                        generation=self._config.generation, expires_at=expires_at
                    ),
                    expires_at=expires_at,
                )
            if self._task is None:
                self._startup_deadline = expires_at
                self._task = asyncio.create_task(self._run(), name="accounting-read-model")
            try:
                await wait_for_startup(
                    started=self._started,
                    task=self._task,
                    timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time()),
                    worker_name="read-model processing",
                )
            except TimeoutError as exc:
                if self._last_failure is None:
                    raise
                raise TimeoutError(f"{exc} (last failure: {self._last_failure})") from None
            health = self.worker_health
            if health.state is not WorkerState.READY and health.detail != "read_model_age_limit":
                raise RuntimeError("read-model processing did not become ready")
        except BaseException:
            self._state = WorkerState.FAILED
            self.stop_claims()
            await stop_tasks_before_deadline((self._task,), deadline=expires_at, cancel_first=True)
            raise

    async def close(self, *, expires_at: float) -> bool:
        _deadline(expires_at)
        self.stop_claims()
        stopped = await stop_tasks_before_deadline((self._task,), deadline=expires_at)
        stopped = stopped and not self._gate.active and not self._stop_failed
        self._state = WorkerState.DISABLED if stopped else WorkerState.FAILED
        # Stopped is local task ownership, not proof that every source event is projected.
        return stopped

    async def run_once(self, *, expires_at: float) -> int:
        _require_time(expires_at)
        if self._closing:
            raise RuntimeError("read-model processing is closed")
        await self._gate.acquire(
            timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time())
        )
        try:
            async with asyncio.timeout_at(expires_at):
                if self._claim is None:
                    self._claim = await self._next_claim(expires_at=expires_at)
                claim = self._claim
                if claim is None:
                    return 0
                count = await self._persistence.materialize(claim, expires_at=expires_at)
                if type(count) is not int or count not in (0, len(claim.sequences)):
                    raise invalid_result()
                if asyncio.get_running_loop().time() >= expires_at:
                    raise TimeoutError("read-model commit exceeded its caller deadline")
                self._claim = None
                increment_accounting_projection(
                    "read_model_commit", "success" if count else "stale", max(1, count)
                )
                return count
        finally:
            await self._gate.release()

    async def _next_claim(self, *, expires_at: float) -> ReadModelClaim | None:
        claim = await self._persistence.claim(
            generation=self._config.generation,
            worker_id=self._config.worker_id,
            limit=self._config.batch_size,
            lease_seconds=self._config.lease_seconds,
            expires_at=expires_at,
        )
        if claim is None:
            return None
        if type(claim) is not ReadModelClaim:
            raise invalid_result()
        try:
            frozen = ReadModelClaim.model_validate(claim.model_dump())
        except ValueError:
            raise invalid_result() from None
        if (
            frozen.generation != self._config.generation
            or frozen.worker_id != self._config.worker_id
            or len(frozen.sequences) > self._config.batch_size
        ):
            raise invalid_result()
        return frozen

    async def _run(self) -> None:
        while not self._closing:
            self._idle.begin_claim()
            end = asyncio.get_running_loop().time() + self._config.call_budget_seconds
            if self._startup_deadline is not None:
                end = min(end, self._startup_deadline)
            try:
                count = await self.run_once(expires_at=end)
                await self._refresh_progress(expires_at=end)
                self._state = WorkerState.STOPPING if self._closing else WorkerState.READY
                if self._last_failure is not None:
                    logger.info(
                        "Accounting read-model processing recovered: worker=%s",
                        self._config.worker_id,
                    )
                    self._last_failure = None
                self._failures = 0
            except (AccountingProtocolUnavailable, TimeoutError) as exc:
                self._record_failure(exc)
                self._idle.reset()
                increment_accounting_projection("read_model_run", "unavailable")
                if self._observe_progress:
                    self._progress_health.unavailable()
                    self._next_progress_at = 0.0
                self._state = WorkerState.STOPPING if self._closing else WorkerState.DEGRADED
                self._failures = min(8, self._failures + 1)
                count = 0
            health = self.worker_health
            if health.state is WorkerState.READY or health.detail == "read_model_age_limit":
                self._started.set()
                self._startup_deadline = None
            if count:
                self._idle.reset()
                await asyncio.sleep(0)
                continue
            if self._closing:
                return
            if not self._failures:
                await self._idle.wait(self._config.poll_seconds)
                continue
            wait = min(
                self._config.backoff_max_seconds, self._config.poll_seconds * 2**self._failures
            )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=random.uniform(wait * 0.8, wait))
            except TimeoutError:
                pass

    def _record_failure(self, exc: AccountingProtocolUnavailable | TimeoutError) -> None:
        detail = exc.detail if isinstance(exc, AccountingProtocolUnavailable) else "timeout"
        claim = self._claim
        if claim is not None:
            detail = (
                f"{detail} partition={claim.accounting_partition} "
                f"sequences={claim.sequences[0]}-{claim.sequences[-1]}"
            )
        if detail != self._last_failure:
            # Log when a failure streak starts or changes. A stuck page retries
            # with backoff and must not write one line per attempt.
            logger.warning(
                "Accounting read-model processing failed: worker=%s %s",
                self._config.worker_id,
                detail,
            )
        self._last_failure = detail

    async def _refresh_progress(self, *, expires_at: float) -> None:
        if not self._observe_progress:
            return
        observed = monotonic()
        if observed < self._next_progress_at:
            return
        async with asyncio.timeout_at(expires_at):
            value = await self._persistence.progress(
                generation=self._config.generation,
                expires_at=expires_at,
            )
        _require_time(expires_at)
        self._progress_health.observe(value, observed_at=observed)
        self._next_progress_at = observed + 1


def _deadline(expires_at: float) -> None:
    if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
        raise ValueError("read-model deadline is invalid")


def _require_time(expires_at: float) -> None:
    _deadline(expires_at)
    if expires_at <= asyncio.get_running_loop().time():
        raise TimeoutError("read-model deadline has expired")
