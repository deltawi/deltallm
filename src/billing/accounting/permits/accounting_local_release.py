"""Release local issue proofs that another process finalized or the database expired.

A proof normally leaves the store when this process finalizes its operation. A
batch completion can be delivered by any batch worker, so the issuing worker may
never see that terminal. Without this worker those proofs stay until restart and
the store fills, after which every local reservation fails as "queue full".
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
import logging
import math
from typing import Protocol

from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.billing.accounting.permits.accounting_local_receipts import (
    LocalReceiptStore,
    RetainedLocalReceipt,
)
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.permits.accounting_permit_results import invalid_result
from src.metrics.accounting import (
    increment_accounting_failure,
    increment_accounting_local_receipt_releases,
    set_accounting_local_receipts,
)
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    task_failure_detail,
)

logger = logging.getLogger(__name__)


class LocalReleasePersistence(Protocol):
    async def lookup_terminals(
        self, values: Sequence[RetainedLocalReceipt], *, expires_at: float
    ) -> Sequence[JournalReceipt | None]: ...


class LocalReceiptReleaseWorker:
    """Its health never gates admission: a failed lookup only delays a release."""

    def __init__(
        self,
        persistence: LocalReleasePersistence,
        receipts: LocalReceiptStore,
        *,
        poll_seconds: float = 1.0,
        recheck_seconds: float = 10.0,
        expiry_grace_seconds: float = 60.0,
        call_budget_seconds: float = 1.0,
        batch_size: int = 256,
        clock: Callable[[], float] | None = None,
    ) -> None:
        for value, lower, upper in (
            (poll_seconds, 0.01, 60),
            (recheck_seconds, 0.01, 3600),
            (expiry_grace_seconds, 0, 3600),
            (call_budget_seconds, 0.01, 5),
        ):
            if not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("local receipt release timing is invalid")
        if type(batch_size) is not int or not 1 <= batch_size <= 256:
            raise ValueError("local receipt release batch size is invalid")
        self._persistence = persistence
        self._receipts = receipts
        self._poll = poll_seconds
        self._recheck = recheck_seconds
        self._grace = expiry_grace_seconds
        self._budget = call_budget_seconds
        self._batch = batch_size
        # Grant deadlines use the event loop's monotonic clock.
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._state = WorkerState.STARTING
        self._closing = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def worker_health(self) -> WorkerHealth:
        if self._state is WorkerState.DISABLED:
            return WorkerHealth(self._state)
        failure = task_failure_detail(self._task)
        if failure is not None:
            return WorkerHealth(WorkerState.FAILED, failure)
        detail = "lookup_unavailable" if self._state is WorkerState.DEGRADED else None
        return WorkerHealth(self._state, detail)

    def owns_receipts(self, receipts: LocalReceiptStore) -> bool:
        return self._receipts is receipts

    def start(self) -> asyncio.Task[None]:
        if self._closing:
            raise RuntimeError("local receipt release cannot restart after close")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="accounting-local-receipt-release")
        return self._task

    async def close(self, *, expires_at: float) -> bool:
        if not math.isfinite(expires_at):
            raise ValueError("local receipt release close deadline is invalid")
        self._closing = True
        self._state = WorkerState.STOPPING
        stopped = await stop_tasks_before_deadline(
            (self._task,), deadline=expires_at, cancel_first=True
        )
        self._publish()
        self._state = WorkerState.DISABLED if stopped else WorkerState.FAILED
        return stopped

    async def run_once(self, *, expires_at: float) -> int:
        """Check one slice of proofs not checked recently; return how many were checked."""
        candidates = self._receipts.release_candidates(
            minimum_age_seconds=self._recheck, limit=self._batch
        )
        if not candidates:
            self._publish()
            return 0
        try:
            now = asyncio.get_running_loop().time() if self._clock is None else self._clock()
            expired: list[RetainedLocalReceipt] = []
            live: list[RetainedLocalReceipt] = []
            for proof in candidates:
                if proof.grant.recovery_deadline + self._grace <= now:
                    expired.append(proof)
                else:
                    live.append(proof)
            if expired:
                increment_accounting_local_receipt_releases(
                    "expired",
                    self._receipts.release_expired(expired, now=now, grace_seconds=self._grace),
                )
            if live:
                async with asyncio.timeout_at(expires_at):
                    results = await self._persistence.lookup_terminals(live, expires_at=expires_at)
                if len(results) != len(live):
                    raise invalid_result()
                increment_accounting_local_receipt_releases(
                    "finalized",
                    self._receipts.release_finalized(
                        tuple(
                            (proof, result)
                            for proof, result in zip(live, results, strict=True)
                            if result is not None
                        )
                    ),
                )
        finally:
            self._publish()
        return len(candidates)

    def _publish(self) -> None:
        set_accounting_local_receipts(
            entries=self._receipts.entries,
            retained_bytes=self._receipts.retained_bytes,
            fill_ratio=self._receipts.fill_ratio,
        )

    async def _run(self) -> None:
        self._state = WorkerState.READY
        loop = asyncio.get_running_loop()
        while not self._closing:
            try:
                checked = await self.run_once(expires_at=loop.time() + self._budget)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - keep releasing after any failure.
                reason = (
                    error.reason
                    if isinstance(error, AccountingProtocolUnavailable)
                    else "deadline"
                    if isinstance(error, TimeoutError)
                    else "unknown"
                )
                increment_accounting_failure("receipt_release", "lookup", reason)
                if self._state is not WorkerState.DEGRADED:
                    logger.warning(
                        "Local receipt release lookup failed; reason=%s error=%s",
                        reason,
                        type(error).__name__,
                    )
                self._state = WorkerState.DEGRADED
                checked = 0
            else:
                if self._state is WorkerState.DEGRADED:
                    logger.info("Local receipt release lookup recovered")
                self._state = WorkerState.READY
            if checked < self._batch:
                await asyncio.sleep(self._poll)
            else:
                # More proofs are due; yield, then check the next slice.
                await asyncio.sleep(0)
