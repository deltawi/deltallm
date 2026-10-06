"""Typed worker owners share one process deadline and no inference services."""

from __future__ import annotations

import asyncio

from src.billing.accounting_admission_monitor import AccountingAdmissionMonitor
from src.billing.accounting_journal_runtime import JournalProcessingWorker
from src.billing.accounting_lane_group import AccountingLaneGroup
from src.billing.accounting_presence import ProjectionPresencePublisher
from src.billing.accounting_read_model_runtime import ReadModelProcessingWorker
from src.billing.accounting_recovery import AccountingRecoveryWorker
from src.billing.accounting_rpc_service import AccountingRpcService
from src.process_lifecycle import ProcessLifecycle
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerHealthSource,
    WorkerState,
    stop_tasks_before_deadline,
)


class AccountingProcessors:
    """Every mandatory processor must report READY before admission can start."""

    def __init__(self, journal: WorkerHealthSource, reports: WorkerHealthSource) -> None:
        self._owners = (journal, reports)

    @property
    def worker_health(self) -> WorkerHealth:
        for owner in self._owners:
            health = owner.worker_health
            if health.state is not WorkerState.READY:
                return health
        return WorkerHealth(WorkerState.READY)


class AccountingRequestRuntime:
    def __init__(
        self,
        service: AccountingRpcService,
        monitor: AccountingAdmissionMonitor,
        lifecycle: ProcessLifecycle,
    ) -> None:
        self.service = service
        self.monitor = monitor
        self._lifecycle = lifecycle
        self._closed = False
        self._terminal_task: asyncio.Task[None] | None = None

    @property
    def worker_health(self) -> WorkerHealth:
        return self.service.worker_health

    async def start(self, *, expires_at: float) -> None:
        if self._closed:
            raise RuntimeError("accounting request runtime cannot restart after close")
        self._lifecycle.register_claim_stop(self.service.stop_admission)
        await self.monitor.start(expires_at=expires_at)
        self._terminal_task = self.service.start()
        if self.worker_health.state is not WorkerState.READY:
            raise RuntimeError("accounting request runtime did not become ready")

    async def close(self, *, expires_at: float) -> None:
        self._closed = True
        self.service.stop_admission()
        try:
            async with asyncio.timeout_at(expires_at):
                await self.service.close(
                    timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time())
                )
        finally:
            monitored = await self.monitor.close(expires_at=expires_at)
            stopped = await stop_tasks_before_deadline(
                (self._terminal_task,),
                deadline=expires_at,
                cancel_first=True,
            )
        task = self._terminal_task
        clean = task is None or (task.done() and not task.cancelled() and task.exception() is None)
        if not stopped or not clean or not monitored:
            raise RuntimeError("accounting request monitor did not stop")


class AccountingProjectionRuntime:
    def __init__(
        self,
        processing: JournalProcessingWorker | AccountingLaneGroup,
        read_models: ReadModelProcessingWorker | AccountingLaneGroup,
        recovery: AccountingRecoveryWorker,
        presence: ProjectionPresencePublisher,
        lifecycle: ProcessLifecycle,
    ) -> None:
        self.processing = processing
        self.read_models = read_models
        self.recovery = recovery
        self.presence = presence
        self._lifecycle = lifecycle
        self._closed = False

    @property
    def worker_health(self) -> WorkerHealth:
        for owner in (self.processing, self.read_models, self.recovery, self.presence):
            health = owner.worker_health
            if health.state is not WorkerState.READY:
                return health
        return WorkerHealth(WorkerState.READY)

    async def start(self, *, expires_at: float) -> None:
        if self._closed:
            raise RuntimeError("accounting projection runtime cannot restart after close")
        await self.processing.start(expires_at=expires_at)
        tasks = self.processing.tasks
        if not tasks:
            raise RuntimeError("accounting journal task is missing")
        for task in tasks:
            self._lifecycle.register_producer(self.processing.stop_claims, task)
        await self.read_models.start(expires_at=expires_at)
        tasks = self.read_models.tasks
        if not tasks:
            raise RuntimeError("accounting read-model task is missing")
        for task in tasks:
            self._lifecycle.register_producer(self.read_models.stop_claims, task)
        await self.presence.start(expires_at=expires_at)
        await self.recovery.start(expires_at=expires_at)
        task = self.recovery.task
        if task is None:
            raise RuntimeError("accounting recovery task is missing")
        self._lifecycle.register_producer(self.recovery.stop_claims, task)
        health = self.worker_health
        if health.state is not WorkerState.READY and not (
            health.state is WorkerState.DEGRADED and self.recovery.dependencies_checked
        ):
            raise RuntimeError("accounting projection runtime did not become ready")
        # A checked repair role stays alive to reduce old debt. Readiness remains
        # false and presence cannot admit new work until every queue is healthy.

    async def close(self, *, expires_at: float) -> None:
        self._closed = True
        self.processing.stop_claims()
        self.read_models.stop_claims()
        self.recovery.stop_claims()
        try:
            await self.presence.close(expires_at=expires_at)
        finally:
            try:
                recovered = await self.recovery.close(expires_at=expires_at)
            finally:
                try:
                    reported = await self.read_models.close(expires_at=expires_at)
                finally:
                    processed = await self.processing.close(expires_at=expires_at)
        if not recovered or not reported or not processed:
            raise RuntimeError("accounting projection tasks did not stop")
