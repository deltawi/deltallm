"""Bounded durable budget alert acceptance and owned background delivery."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from decimal import Decimal

from src.billing.alerts import AlertService
from src.db.budget_notifications import BudgetNotification, BudgetNotificationRepository
from src.metrics import increment_notification_enqueue
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    task_failure_detail,
    wait_for_startup,
)

logger = logging.getLogger(__name__)


class BudgetNotificationProducer:
    def __init__(
        self,
        repository: BudgetNotificationRepository,
        *,
        enabled: Callable[[], bool],
        ttl_seconds: int,
    ) -> None:
        self.repository = repository
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds

    async def send_budget_alert(
        self,
        *,
        entity_type: str,
        entity_id: str,
        current_spend: Decimal,
        soft_budget: Decimal | None,
        hard_budget: Decimal | None,
    ) -> None:
        if not self.enabled() or soft_budget is None:
            return
        if entity_type != "org":
            raise ValueError("only organizations currently expose soft budget policy")
        try:
            # Optional work cannot queue behind required admission capacity.
            async with asyncio.timeout(0.1):
                outcome = await self.repository.enqueue(
                    organization_id=entity_id,
                    spend=current_spend,
                    soft_budget=soft_budget,
                    hard_budget=hard_budget,
                    ttl_seconds=self.ttl_seconds,
                )
        except Exception:
            outcome = "unavailable"
            logger.warning("budget notification acceptance unavailable")
        increment_notification_enqueue(kind="budget_threshold", channel="intent", status=outcome)


class BudgetNotificationWorker:
    """One control-plane task and one leased record per process, no memory queue.

    Preparation may retry. A persisted dispatch boundary prevents blind replay
    of a Slack send or email enqueue after an ambiguous crash. Accepted email
    delivery then belongs to the existing durable email outbox worker.
    """

    def __init__(self, repository: BudgetNotificationRepository, alerts: AlertService) -> None:
        self.repository = repository
        self.alerts = alerts
        self.task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._stop = asyncio.Event()
        self._state = WorkerState.STARTING

    @property
    def worker_health(self) -> WorkerHealth:
        failure = task_failure_detail(self.task)
        if failure or self.task is None:
            return WorkerHealth(WorkerState.FAILED, "budget notification worker is not running")
        return WorkerHealth(self._state)

    async def start(self) -> None:
        if self.task is not None:
            return
        async with asyncio.timeout(5):
            await self.repository.probe()
        self.task = asyncio.create_task(self.run(), name="budget-notifications")
        try:
            await wait_for_startup(
                started=self._started,
                task=self.task,
                timeout_seconds=5,
                worker_name="budget notifications",
            )
        except BaseException:
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        self._state = WorkerState.STOPPING
        self._stop.set()
        await stop_tasks_before_deadline(
            [self.task],
            deadline=asyncio.get_running_loop().time() + 11,
        )

    async def run(self) -> None:
        self._state = WorkerState.READY
        self._started.set()
        while not self._stop.is_set():
            try:
                async with asyncio.timeout(15):
                    record = await self.repository.claim()
                    if record is not None:
                        await self.process(record)
                    else:
                        await self.repository.cleanup()
                self._state = WorkerState.READY
            except Exception:
                self._state = WorkerState.DEGRADED
                logger.warning("budget notification worker cycle unavailable")
                increment_notification_enqueue(
                    kind="budget_threshold", channel="intent", status="worker_unavailable"
                )
            # Always yield: neither backlog nor failures create an unbounded busy loop.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1 + random.random())
            except TimeoutError:
                pass

    async def process(self, record: BudgetNotification) -> None:
        if record.status == "failed":
            increment_notification_enqueue(
                kind="budget_threshold",
                channel="intent",
                status="delivery_unknown"
                if record.outcome == "delivery_unknown"
                else "attempts_exhausted",
            )
            return
        if not self.alerts.budget_notifications_enabled():
            await self.repository.finish(record, delivered=True, outcome="disabled")
            return
        try:
            async with asyncio.timeout(5):
                prepared = await self.alerts.prepare_budget_alert(
                    entity_type="org",
                    entity_id=record.organization_id,
                    current_spend=float(record.spend),
                    soft_budget=float(record.soft_budget),
                    hard_budget=float(record.hard_budget)
                    if record.hard_budget is not None
                    else None,
                )
        except Exception:
            await self.repository.retry_preparation(
                record, delay=min(60, 2**record.attempt_count) + random.random()
            )
            return
        if not await self.repository.begin_dispatch(record):
            return
        # The durable boundary is deliberately outside the try: a commit-ambiguous
        # transition never permits an external send from this caller.
        try:
            async with asyncio.timeout(5):
                delivered = await self.alerts.dispatch_prepared_budget_alert(
                    prepared,
                    entity_type="org",
                    entity_id=record.organization_id,
                )
            # A false aggregate is not proof that every downstream send failed.
            outcome = "delivered" if delivered else "delivery_unknown"
        except Exception:
            delivered, outcome = False, "delivery_unknown"
        await self.repository.finish(record, delivered=delivered, outcome=outcome)
        increment_notification_enqueue(kind="budget_threshold", channel="intent", status=outcome)
