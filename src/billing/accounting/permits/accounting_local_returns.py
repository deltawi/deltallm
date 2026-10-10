"""Return bounded unused suffixes while issued proofs keep their own owner."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import math
from typing import Protocol

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.permits.accounting_local_leases import LocalPermitReturn
from src.concurrency import CapacityGateTimedOut
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    task_failure_detail,
    wait_for_startup,
)


class LocalReturnPersistence(Protocol):
    async def return_batch(
        self, returns: Sequence[LocalPermitReturn], *, expires_at: float
    ) -> Sequence[int]: ...


class LocalReturnWorker:
    """One task shares the issue owner; it never queues behind a funding call."""

    def __init__(
        self,
        persistence: LocalReturnPersistence,
        cursors: LocalCursorStore,
        issuer: LocalPermitIssuer,
        *,
        poll_seconds: float = 0.05,
        call_budget_seconds: float = 0.5,
        minimum_validity_seconds: float = 0.1,
    ) -> None:
        if not issuer.owns_cursors(cursors):
            raise ValueError("local return worker must share its issuer's cursor store")
        for value, lower, upper in (
            (poll_seconds, 0.01, 5),
            (call_budget_seconds, 0.01, 5),
            (minimum_validity_seconds, 0, 5),
        ):
            if not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("local return timing is invalid")
        self._persistence = persistence
        self._cursors = cursors
        self._issuer = issuer
        self._poll = poll_seconds
        self._budget = call_budget_seconds
        self._minimum_validity = minimum_validity_seconds
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._wake = asyncio.Event()
        self._state = WorkerState.STARTING
        self._closing = False
        self._close_deadline: float | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    def owns_issuer(self, issuer: LocalPermitIssuer) -> bool:
        return self._issuer is issuer

    @property
    def worker_health(self) -> WorkerHealth:
        if self._state is WorkerState.DISABLED:
            return WorkerHealth(self._state)
        failure = task_failure_detail(self._task)
        if failure is not None:
            return WorkerHealth(WorkerState.FAILED, failure)
        return WorkerHealth(self._state)

    async def start(self, *, expires_at: float) -> None:
        if not math.isfinite(expires_at):
            raise ValueError("local return startup deadline is invalid")
        if self._closing:
            raise RuntimeError("local return worker cannot restart after close")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="accounting-local-returns")
        try:
            await wait_for_startup(
                started=self._started,
                task=self._task,
                timeout_seconds=max(0, expires_at - asyncio.get_running_loop().time()),
                worker_name="local return worker",
            )
            if not self.worker_health.ready:
                raise RuntimeError("local return worker did not start")
        except BaseException:
            self._issuer.stop_admission()
            self._closing = True
            self._state = WorkerState.FAILED
            await stop_tasks_before_deadline((self._task,), deadline=expires_at, cancel_first=True)
            raise

    async def close(self, *, expires_at: float) -> bool:
        if not math.isfinite(expires_at):
            raise ValueError("local return close deadline is invalid")
        self._issuer.stop_admission()
        self._closing = True
        self._close_deadline = (
            expires_at if self._close_deadline is None else min(expires_at, self._close_deadline)
        )
        self._state = WorkerState.STOPPING
        self._wake.set()
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="accounting-local-returns")
        stopped = await stop_tasks_before_deadline((self._task,), deadline=self._close_deadline)
        drained = (
            stopped
            and self._cursors.entries == 0
            and not self._issuer.admission.active
            and not self._issuer.admission.waiters
        )
        self._state = WorkerState.DISABLED if drained else WorkerState.FAILED
        return drained

    async def run_once(self, *, expires_at: float) -> int:
        gate = self._issuer.admission
        if gate.active or gate.waiters:
            return 0
        await gate.acquire(expires_at=expires_at)
        try:
            if self._closing:
                self._cursors.retire_slice()
            else:
                self._cursors.prune(
                    now=asyncio.get_running_loop().time(),
                    minimum_validity_seconds=self._minimum_validity,
                )
            values = self._cursors.return_candidates()
            if not values:
                return 0
            async with asyncio.timeout_at(expires_at):
                counts = await self._persistence.return_batch(values, expires_at=expires_at)
            self._cursors.acknowledge_returns(values, counts)
            return len(values)
        finally:
            gate.release()

    async def _run(self) -> None:
        self._state = WorkerState.STOPPING if self._closing else WorkerState.READY
        self._started.set()
        while True:
            if (
                self._closing
                and self._cursors.entries == 0
                and not self._issuer.admission.active
                and not self._issuer.admission.waiters
            ):
                return
            deadline = asyncio.get_running_loop().time() + self._budget
            if self._close_deadline is not None:
                deadline = min(deadline, self._close_deadline)
            try:
                count = await self.run_once(expires_at=deadline)
                self._state = WorkerState.STOPPING if self._closing else WorkerState.READY
            except (AccountingProtocolUnavailable, CapacityGateTimedOut, TimeoutError):
                self._state = WorkerState.STOPPING if self._closing else WorkerState.DEGRADED
                count = 0
            if count:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll)
            except TimeoutError:
                pass
