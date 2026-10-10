"""Own local issue, terminal drain, and unused suffix returns under one deadline."""

from __future__ import annotations

import asyncio
import math

from src.billing.accounting.permits.accounting_local_returns import LocalReturnWorker
from src.billing.accounting.accounting_local_service import LocalAccountingService
from src.shutdown import cleanup_deadline
from src.telemetry.lifecycle import WorkerHealth, WorkerState, stop_tasks_before_deadline


class LocalAccountingRuntime:
    """Local drain is not a claim that the durable accounting backlog is empty."""

    def __init__(self, service: LocalAccountingService, returns: LocalReturnWorker) -> None:
        if not returns.owns_issuer(service.issuer):
            raise ValueError("local runtime must share one issue and return owner")
        if service.reservations.task is not None or service.finalizations.task is not None:
            raise ValueError("local runtime cannot adopt running accounting queues")
        if returns.task is not None:
            raise ValueError("local runtime cannot adopt a running return worker")
        self._service = service
        self._returns = returns
        self._queue_tasks: tuple[asyncio.Task[None], ...] = ()
        self._queue_close_task: asyncio.Task[None] | None = None
        self._return_close_task: asyncio.Task[bool] | None = None
        self._close_task: asyncio.Task[bool] | None = None
        self._probe_task: asyncio.Task[bool] | None = None
        self._interrupted = False
        self._starting = False
        self._started = False
        self._state = WorkerState.STARTING
        self._detail: str | None = None

    @property
    def service(self) -> LocalAccountingService:
        if (
            not self._started
            or self._close_task is not None
            or self.worker_health.state is not WorkerState.READY
        ):
            raise RuntimeError("local accounting runtime is not active")
        return self._service

    @property
    def worker_health(self) -> WorkerHealth:
        if self._state is WorkerState.READY:
            if not self._service.worker_health.ready:
                return WorkerHealth(WorkerState.FAILED, "queues_failed")
            if self._returns.worker_health.state is not WorkerState.READY:
                return WorkerHealth(WorkerState.DEGRADED, "returns_unready")
        return WorkerHealth(self._state, self._detail)

    async def readiness_probe(self, *, expires_at: float) -> bool:
        _deadline(expires_at)
        if self.worker_health.state is not WorkerState.READY:
            return False
        ready = await self._probe_generation(expires_at)
        return ready and self.worker_health.state is WorkerState.READY

    async def start(self, *, expires_at: float) -> None:
        _deadline(expires_at)
        if self._close_task is not None:
            raise RuntimeError("local accounting runtime cannot restart after close")
        if expires_at <= asyncio.get_running_loop().time():
            raise TimeoutError("local accounting startup deadline has expired")
        if self._starting:
            raise RuntimeError("local accounting runtime startup is already active")
        if self._started:
            if not self.worker_health.ready:
                raise RuntimeError("local accounting runtime is not ready")
            return
        self._starting = True
        try:
            await self._returns.start(expires_at=expires_at)
            if self._close_task is not None:
                raise RuntimeError("local accounting runtime closed during startup")
            self._queue_tasks = self._service.start()
            if not await self._probe_generation(expires_at):
                raise RuntimeError("local accounting generation is not active")
            if self._close_task is not None:
                raise RuntimeError("local accounting runtime closed during startup")
            if (
                self._returns.worker_health.state is not WorkerState.READY
                or not self._service.worker_health.ready
            ):
                raise RuntimeError("local accounting workers are not ready")
            self._state = WorkerState.READY
            self._started = True
        except BaseException:
            self._service.issuer.stop_admission()
            await self.close(expires_at=expires_at)
            self._state = WorkerState.FAILED
            self._detail = "startup_failed"
            raise
        finally:
            self._starting = False

    async def _probe_generation(self, expires_at: float) -> bool:
        if expires_at <= asyncio.get_running_loop().time():
            return False
        if self._probe_task is not None and not self._probe_task.done():
            return False
        task = asyncio.create_task(
            self._service.readiness_probe(), name="accounting-local-generation-probe"
        )
        self._probe_task = task
        try:
            return await _completed(task, expires_at) and task.result() is True
        finally:
            await stop_tasks_before_deadline((task,), deadline=expires_at, cancel_first=True)

    async def close(self, *, expires_at: float) -> bool:
        _deadline(expires_at)
        expires_at = min(
            expires_at,
            cleanup_deadline(max(0, expires_at - asyncio.get_running_loop().time())),
        )
        self._service.issuer.stop_admission()
        if self._interrupted:
            return False
        if self._close_task is None:
            self._state = WorkerState.STOPPING
            self._close_task = asyncio.create_task(
                self._close_owned(expires_at), name="accounting-local-runtime-close"
            )
        try:
            if await _completed(self._close_task, expires_at):
                return self._close_task.result()
            self._state = WorkerState.FAILED
            self._detail = "drain_incomplete"
            if self._interrupted:
                return False
            self._interrupted = True
            self._close_task.cancel()
            await self._stop_owned(expires_at)
            await stop_tasks_before_deadline(
                (self._close_task,), deadline=expires_at, cancel_first=True
            )
            return False
        except asyncio.CancelledError:
            if self._interrupted:
                raise
            self._interrupted = True
            self._state = WorkerState.FAILED
            self._detail = "drain_cancelled"
            self._close_task.cancel()
            await self._stop_owned(expires_at)
            await stop_tasks_before_deadline(
                (self._close_task,), deadline=expires_at, cancel_first=True
            )
            raise

    async def _close_owned(self, expires_at: float) -> bool:
        returns_failed = self._returns.worker_health.state is WorkerState.FAILED
        drained = False
        try:
            self._queue_close_task = asyncio.create_task(
                self._service.close(
                    timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time())
                ),
                name="accounting-local-queue-close",
            )
            queues = await _completed(self._queue_close_task, expires_at)
            queues = queues and all(_clean_task(task) for task in self._queue_tasks)
            if not queues:
                await stop_tasks_before_deadline(
                    (*self._queue_tasks, self._queue_close_task),
                    deadline=expires_at,
                    cancel_first=True,
                )
            self._return_close_task = asyncio.create_task(
                self._returns.close(expires_at=expires_at), name="accounting-local-return-close"
            )
            returned = await _completed(self._return_close_task, expires_at)
            returned = returned and self._return_close_task.result() is True
            drained = (
                queues
                and returned
                and not returns_failed
                and self._service.issuer.receipt_store.entries == 0
                and self._service.reservations.retained_bytes == 0
                and self._service.finalizations.retained_bytes == 0
            )
        finally:
            stopped = await self._stop_owned(expires_at)
        drained = drained and stopped
        self._state = WorkerState.DISABLED if drained else WorkerState.FAILED
        self._detail = None if drained else "drain_incomplete"
        return drained

    async def _stop_owned(self, expires_at: float) -> bool:
        return await stop_tasks_before_deadline(
            (
                *self._queue_tasks,
                self._returns.task,
                self._queue_close_task,
                self._return_close_task,
                self._probe_task,
            ),
            deadline=expires_at,
            cancel_first=True,
        )


async def _completed(task: asyncio.Task[object], expires_at: float) -> bool:
    if not task.done():
        await asyncio.wait((task,), timeout=max(0, expires_at - asyncio.get_running_loop().time()))
    return _clean_task(task)


def _clean_task(task: asyncio.Task[object]) -> bool:
    return task.done() and not task.cancelled() and task.exception() is None


def _deadline(expires_at: float) -> None:
    if type(expires_at) not in (float, int) or not math.isfinite(expires_at):
        raise ValueError("local accounting lifecycle deadline is invalid")
