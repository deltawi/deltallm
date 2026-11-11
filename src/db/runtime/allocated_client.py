"""Finite Prisma admission whose ownership survives caller cancellation.

The adapter is intentionally tied to the frozen Prisma client. Model actions and
raw queries share _execute; interactive transaction copies retain this owner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from typing import TypeVar

import httpx
from prisma import Prisma
from prisma.client import Batch
from prisma._raw_query import deserialize_raw_results
from prisma.errors import ClientNotConnectedError, HTTPClientClosedError, TransactionExpiredError
from prometheus_client import Counter, Gauge, Histogram

from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.db.runtime.allocation_config import DatabasePolicy
from src.db.runtime.database_caller import current_database_caller
from src.metrics.prometheus import get_prometheus_registry
from src.models.errors import RoutingFailureAction, ServiceUnavailableError

T = TypeVar("T")
_registry = get_prometheus_registry()
_occupied = Gauge(
    "deltallm_database_allocation_occupied",
    "Occupied database slots",
    ["allocation"],
    registry=_registry,
)
_events = Counter(
    "deltallm_database_allocation_events_total",
    "Database allocation outcomes",
    ["allocation", "outcome"],
    registry=_registry,
)
_duration = Histogram(
    "deltallm_database_allocation_seconds",
    "Database allocation duration",
    ["allocation", "operation"],
    registry=_registry,
)
# Which code path holds, waits for, or is refused a slot. Callers are fixed
# classes (src/db/runtime/database_caller.py), never tenant or request values.
_caller_slots = Gauge(
    "deltallm_database_caller_slots",
    "Database slots held now, by caller",
    ["allocation", "caller"],
    registry=_registry,
)
_caller_events = Counter(
    "deltallm_database_caller_events_total",
    "Database slot outcomes, by caller",
    ["allocation", "caller", "outcome"],
    registry=_registry,
)
_caller_wait = Histogram(
    "deltallm_database_caller_wait_seconds",
    "Time a caller waited for a database slot",
    ["allocation", "caller"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
    registry=_registry,
)
_caller_hold = Histogram(
    "deltallm_database_caller_hold_seconds",
    "Time a caller held a database slot",
    ["allocation", "caller"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0),
    registry=_registry,
)
_waiters = Gauge(
    "deltallm_database_allocation_waiters",
    "Callers waiting for a database slot now",
    ["allocation"],
    registry=_registry,
)
_abandoned = Gauge(
    "deltallm_database_allocation_abandoned",
    "Database slots still held for work whose caller stopped waiting",
    ["allocation"],
    registry=_registry,
)


# Waiting positions on the receipt (settlement) allocation, whatever its size.
SETTLEMENT_MIN_WAITERS = 64


class _CallerSeries:
    """Bound metric children for one allocation and caller (no label lookup per query)."""

    __slots__ = ("caller", "slots", "wait", "hold", "_events", "_allocation")

    def __init__(self, allocation: str, caller: str):
        self._allocation = allocation
        self.caller = caller
        self.slots = _caller_slots.labels(allocation, caller)
        self.wait = _caller_wait.labels(allocation, caller)
        self.hold = _caller_hold.labels(allocation, caller)
        self._events: dict[str, Counter] = {}

    def event(self, outcome: str) -> None:
        counter = self._events.get(outcome)
        if counter is None:
            counter = self._events[outcome] = _caller_events.labels(
                self._allocation, self.caller, outcome
            )
        counter.inc()


@dataclass(frozen=True, slots=True)
class DatabaseSlot:
    """One held slot: who holds it and since when."""

    caller: str
    acquired_at: float
    series: _CallerSeries


class DatabaseUnavailableError(ServiceUnavailableError):
    message = "Database capacity is temporarily unavailable"
    error_type = "database_unavailable"

    def __init__(self):
        super().__init__(
            code="database_unavailable",
            affects_deployment_health=False,
            routing_failure_action=RoutingFailureAction.FAIL_FAST,
        )


async def _native_call(operation: Callable[[], Awaitable[T]], seconds: float) -> T:
    try:
        async with asyncio.timeout(seconds):
            return await operation()
    except (
        TimeoutError,
        httpx.TransportError,
        ClientNotConnectedError,
        HTTPClientClosedError,
        TransactionExpiredError,
    ) as exc:
        raise DatabaseUnavailableError() from exc
    except Exception as exc:
        # Preserve record/constraint errors for repository conflict handling and
        # poison-record isolation. Only native availability codes become 503s.
        code = str(getattr(exc, "code", ""))
        meta = getattr(exc, "meta", None)
        sqlstate = str(meta.get("code", "")) if isinstance(meta, dict) else ""
        if (
            code in {"P1001", "P1002", "P1008", "P1017", "P2024", "P2028", "P2037"}
            or sqlstate.startswith(("08", "53"))
            or sqlstate in {"55P03", "57014", "57P01", "57P02", "57P03", "25P03"}
        ):
            raise DatabaseUnavailableError() from exc
        raise


class DatabaseOwner:
    def __init__(self, policy: DatabasePolicy):
        self.policy = policy
        # Required acceptance and receipts need a finite burst buffer, as do
        # allocations given a queue wait (batch workers). Others keep immediate
        # shedding; health may borrow one waiter.
        if policy.allocation == "telemetry_settlement":
            # Receipts follow provider work that already ran, so shedding one
            # turns a paid answer into a 503. They arrive in bursts (requests
            # released together by one admission lock wait) and each takes a
            # few milliseconds, so a small pool queues them within the same
            # acquisition deadline instead of rejecting the third at once.
            self._business_waiters = max(policy.connections, SETTLEMENT_MIN_WAITERS)
        elif policy.allocation == "telemetry" or policy.queue_seconds > 0:
            self._business_waiters = policy.connections
        else:
            self._business_waiters = 0
        self.gate = BoundedCapacityGate(
            concurrency=policy.connections, max_waiters=self._business_waiters
        )
        self.tasks: set[asyncio.Task] = set()
        self.closed = False
        self._probe_active = False
        allocation = policy.allocation
        self._occupied = _occupied.labels(allocation)
        self._waiters = _waiters.labels(allocation)
        self.abandoned_slots = _abandoned.labels(allocation)
        self._query_duration = _duration.labels(allocation, "query")
        self._readiness_duration = _duration.labels(allocation, "readiness")
        self._outcomes: dict[str, Counter] = {}
        # Bounded by the fixed caller classes in database_caller.py.
        self._callers: dict[str, _CallerSeries] = {}

    def _event(self, outcome: str) -> None:
        counter = self._outcomes.get(outcome)
        if counter is None:
            counter = self._outcomes[outcome] = _events.labels(self.policy.allocation, outcome)
        counter.inc()

    def _caller_series(self, caller: str) -> _CallerSeries:
        series = self._callers.get(caller)
        if series is None:
            series = self._callers[caller] = _CallerSeries(self.policy.allocation, caller)
        return series

    async def acquire(
        self, *, timeout_seconds: float | None = None, caller: str | None = None
    ) -> DatabaseSlot:
        if self.closed:
            raise DatabaseUnavailableError()
        series = self._caller_series(caller or current_database_caller())
        started = monotonic()
        waiting = self.gate.active >= self.gate.concurrency
        if waiting:
            self._waiters.inc()
        try:
            await self.gate.acquire(
                timeout_seconds=self.policy.queue_seconds
                + min(
                    self.policy.acquisition_seconds,
                    timeout_seconds
                    if timeout_seconds is not None
                    else self.policy.acquisition_seconds,
                )
            )
        except CapacityGateFull:
            self._event("full")
            series.event("full")
            raise DatabaseUnavailableError() from None
        except CapacityGateTimedOut:
            self._event("queue_timeout")
            series.event("queue_timeout")
            series.wait.observe(monotonic() - started)
            raise DatabaseUnavailableError() from None
        finally:
            if waiting:
                self._waiters.dec()
        if self.closed:
            await self.gate.release()
            raise DatabaseUnavailableError()
        acquired_at = monotonic()
        self._occupied.inc()
        series.slots.inc()
        series.event("acquired")
        series.wait.observe(acquired_at - started)
        return DatabaseSlot(series.caller, acquired_at, series)

    async def release(self, slot: DatabaseSlot | None = None):
        await self.gate.release()
        self._occupied.dec()
        if slot is None:
            self._caller_series(current_database_caller()).slots.dec()
            return
        slot.series.slots.dec()
        slot.series.hold.observe(monotonic() - slot.acquired_at)

    def spawn(self, operation: Awaitable[T]) -> asyncio.Task[T]:
        # Callers must own a pool slot (or a transaction's single query slot).
        task = asyncio.create_task(operation)
        self.tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task):
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Observe errors after an abandoned caller.

    async def query(self, operation: Callable[[], Awaitable[T]]) -> T:
        deadline = monotonic() + self.policy.admission_seconds + self.policy.statement_seconds
        slot = await self.acquire()
        task = self.spawn(self._query(operation, slot))
        return await self._wait_query(task, deadline, slot)

    async def readiness_query(self, operation: Callable[[], Awaitable[T]]) -> T:
        # Borrow one existing slot. A business operation may wait behind this
        # probe within its existing acquisition deadline instead of being shed
        # by health traffic. One probe uses the existing telemetry queue or
        # borrows one waiter; other business saturation still has zero waiters.
        if self._probe_active or self.gate.waiters or self.gate.active >= self.policy.connections:
            raise DatabaseUnavailableError()
        deadline = monotonic() + self.policy.acquisition_seconds + self.policy.statement_seconds
        self._probe_active = True
        try:
            slot = await self.acquire(caller="readiness")
        except BaseException:
            self._probe_active = False
            raise
        try:
            await self.gate.reconfigure(
                concurrency=self.policy.connections, max_waiters=max(1, self._business_waiters)
            )
        except BaseException:
            self._probe_active = False
            await self.release(slot)
            raise
        task = self.spawn(self._query(operation, slot, readiness=True))
        return await self._wait_query(task, deadline, slot)

    async def _wait_query(self, task: asyncio.Task[T], deadline: float, slot: DatabaseSlot) -> T:
        try:
            async with asyncio.timeout(max(0, deadline - monotonic())):
                return await asyncio.shield(task)
        except TimeoutError:
            self._event("caller_deadline")
            slot.series.event("caller_deadline")
            self.track_abandoned(task)
            raise DatabaseUnavailableError() from None
        except asyncio.CancelledError:
            self._event("caller_cancelled")
            slot.series.event("caller_cancelled")
            self.track_abandoned(task)
            raise

    def track_abandoned(self, task: asyncio.Task) -> None:
        """Count a slot that native work keeps after its caller stopped waiting."""
        if task.done():
            return
        self.abandoned_slots.inc()
        task.add_done_callback(lambda _task: self.abandoned_slots.dec())

    async def _query(
        self,
        operation: Callable[[], Awaitable[T]],
        slot: DatabaseSlot,
        *,
        readiness: bool = False,
    ) -> T:
        started = monotonic()
        outcome = "error"
        try:
            result = await _native_call(operation, self.policy.native_query_seconds)
            outcome = "completed"
            return result
        finally:
            self._event(outcome)
            slot.series.event(outcome)
            (self._readiness_duration if readiness else self._query_duration).observe(
                monotonic() - started
            )
            if readiness:
                self._probe_active = False
                await self.gate.reconfigure(
                    concurrency=self.policy.connections, max_waiters=self._business_waiters
                )
            await self.release(slot)

    async def close(self):
        self.closed = True
        deadline = (
            monotonic() + self.policy.transaction_seconds + self.policy.native_query_seconds + 1
        )
        while self.tasks and monotonic() < deadline:
            await asyncio.wait(tuple(self.tasks), timeout=max(0, deadline - monotonic()))
        # Cancelled native acknowledgements may schedule one final quarantine
        # task. Drain those too before the lifecycle disconnects the engine.
        while self.tasks:
            pending = tuple(self.tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


class AllocatedPrisma(Prisma):
    def __init__(self, *args, allocation: DatabaseOwner | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allocation = allocation
        self.transaction_owner: AllocatedTransaction | None = None

    def _copy(self):
        client = super()._copy()
        client.allocation = self.allocation
        return client

    async def _execute(self, **kwargs):
        async def operation():
            return await super(AllocatedPrisma, self)._execute(**kwargs)

        if self.transaction_owner is not None:
            return await self.transaction_owner.query(operation)
        if self.allocation is None:
            raise RuntimeError("Prisma client has no allocation owner")
        return await self.allocation.query(operation)

    def tx(self, *, max_wait=None, timeout=None):
        if self.is_transaction():
            raise RuntimeError("Nested database transactions are not supported")
        if self.allocation is None:
            raise RuntimeError("Prisma client has no allocation owner")
        policy = self.allocation.policy
        transaction_seconds = _bounded_seconds(timeout, policy.transaction_seconds)
        acquisition_seconds = _bounded_seconds(max_wait, policy.acquisition_seconds)
        manager = super().tx(
            max_wait=timedelta(seconds=acquisition_seconds),
            timeout=timedelta(seconds=transaction_seconds),
        )
        return AllocatedTransaction(
            self.allocation,
            manager,
            transaction_seconds=transaction_seconds,
            acquisition_seconds=acquisition_seconds,
        )

    def batch_(self):
        # Production currently uses raw bulk SQL. Keep the generated driver's
        # batch escape hatch under the same admission owner for future callers.
        return AllocatedBatch(self)

    async def readiness_probe(self) -> bool:
        if self.allocation is None or self.is_transaction():
            raise RuntimeError("Readiness requires the owned non-transactional Prisma client")

        async def operation() -> bool:
            response = await super(AllocatedPrisma, self)._execute(
                method="query_raw",
                arguments={"query": "SELECT 1 AS ready", "parameters": ()},
                model=None,
            )
            return deserialize_raw_results(response["data"]["result"]) == [{"ready": 1}]

        return await self.allocation.readiness_query(operation)


def _bounded_seconds(value: int | timedelta | None, maximum: float) -> float:
    if value is None:
        return maximum
    seconds = value.total_seconds() if isinstance(value, timedelta) else value / 1000
    return max(0.001, min(seconds, maximum))


class AllocatedTransaction:
    def __init__(
        self, owner: DatabaseOwner, native, *, transaction_seconds=None, acquisition_seconds=None
    ):
        self.owner = owner
        self.native = native
        self.pending: set[asyncio.Task] = set()
        self.query_gate = BoundedCapacityGate(concurrency=1, max_waiters=0)
        self.closed = False
        self.expires_at = 0.0
        self._started = False
        self._ready = False
        self._finishing = False
        self._owns_slot = False
        self._slot: DatabaseSlot | None = None
        self._abandoned = False
        self.failed = False
        self._deadline = None
        self._expiration_task = None
        self.transaction_seconds = (
            transaction_seconds
            if transaction_seconds is not None
            else owner.policy.transaction_seconds
        )
        self.acquisition_seconds = (
            acquisition_seconds
            if acquisition_seconds is not None
            else owner.policy.acquisition_seconds
        )

    async def start(self, *, _from_context=False):
        if self._started:
            raise RuntimeError("Transaction has already started")
        self._started = True
        acquisition_deadline = monotonic() + self.acquisition_seconds
        self._slot = await self.owner.acquire(timeout_seconds=self.acquisition_seconds)
        self._owns_slot = True
        if self.owner.policy.queue_seconds:
            # A caller that queued for its slot still gets the full native
            # acquisition deadline to start the transaction.
            acquisition_deadline = max(acquisition_deadline, monotonic() + self.acquisition_seconds)
        self.expires_at = (
            monotonic() + self.owner.policy.native_query_seconds + self.transaction_seconds
        )
        task = self.owner.spawn(self._start())
        try:
            async with asyncio.timeout(max(0, acquisition_deadline - monotonic())):
                return await asyncio.shield(task)
        except (asyncio.CancelledError, TimeoutError) as exc:
            self._finishing = True
            self._mark_abandoned()
            self.owner.spawn(self._abandon_start(task))
            if isinstance(exc, TimeoutError):
                raise DatabaseUnavailableError() from None
            raise

    async def _start(self):
        try:
            client = await _native_call(self.native.start, self.owner.policy.native_query_seconds)
            client.transaction_owner = self
            self._ready = True
            self._expiration_task = self.owner.spawn(self._expire_and_release())
            return client
        except BaseException:
            self._mark_abandoned()
            self.owner.spawn(self._expire_and_release())
            raise

    async def _abandon_start(self, task):
        try:
            await task
        except BaseException:
            return  # _start owns release on failure.
        await self._finish(commit=False)

    async def query(self, operation):
        if self.closed or self._finishing:
            raise DatabaseUnavailableError()
        try:
            await self.query_gate.acquire(timeout_seconds=self.owner.policy.acquisition_seconds)
        except CapacityGateFull:
            raise DatabaseUnavailableError() from None
        task = self.owner.spawn(self._query(operation))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        return await asyncio.shield(task)

    async def _query(self, operation):
        try:
            return await _native_call(operation, self.owner.policy.native_query_seconds)
        except BaseException:
            self.failed = True
            raise
        finally:
            await self.query_gate.release()

    async def _expire_and_release(self):
        # If start/commit acknowledgement was lost, Rust still owns the native
        # transaction timeout. Quarantine this slot until that bound expires.
        try:
            await asyncio.sleep(max(0, self.expires_at - monotonic()) + 0.05)
            self.closed = True
            self._finishing = True
            if self.pending:
                await asyncio.gather(*tuple(self.pending), return_exceptions=True)
        finally:
            await self._release()

    def _mark_abandoned(self) -> None:
        # The slot stays held until native work ends or its timeout expires.
        if self._owns_slot and not self._abandoned:
            self._abandoned = True
            self.owner.abandoned_slots.inc()

    async def _release(self):
        if self._owns_slot:
            self._owns_slot = False
            if self._abandoned:
                self.owner.abandoned_slots.dec()
            if (
                self._expiration_task is not None
                and self._expiration_task is not asyncio.current_task()
            ):
                self._expiration_task.cancel()
            await self.owner.release(self._slot)

    async def _finish(self, *, commit: bool):
        if self.closed:
            raise RuntimeError("Transaction has already finished")
        self.closed = True
        started = monotonic()
        rejected_commit = False
        try:
            if self.pending:
                results = await asyncio.gather(*tuple(self.pending), return_exceptions=True)
                if any(isinstance(result, BaseException) for result in results):
                    self.failed = True
            rejected_commit = commit and self.failed
            commit = commit and not self.failed
            await _native_call(
                self.native.commit if commit else self.native.rollback,
                self.owner.policy.native_query_seconds,
            )
        except BaseException:
            self._mark_abandoned()
            self.owner.spawn(self._expire_and_release())
            raise
        else:
            await self._release()
        finally:
            _duration.labels(self.owner.policy.allocation, "finish").observe(monotonic() - started)
        if rejected_commit:
            raise DatabaseUnavailableError()

    async def commit(self):
        self._begin_finish()
        await asyncio.shield(self.owner.spawn(self._finish(commit=True)))

    async def rollback(self):
        self._begin_finish()
        await asyncio.shield(self.owner.spawn(self._finish(commit=False)))

    def _begin_finish(self):
        if not self._ready or not self._owns_slot or self._finishing or self.closed:
            raise RuntimeError("Transaction is not open")
        self._finishing = True

    async def __aenter__(self):
        client = await self.start(_from_context=True)
        self._deadline = asyncio.timeout(self.transaction_seconds)
        await self._deadline.__aenter__()
        return client

    async def __aexit__(self, exc_type, exc, traceback):
        try:
            try:
                if exc_type is None and not self._deadline.expired():
                    await self.commit()
                else:
                    try:
                        await self.rollback()
                    except Exception:
                        _events.labels(self.owner.policy.allocation, "rollback_error").inc()
                    if exc_type is None:
                        raise DatabaseUnavailableError()
            except BaseException as finish_error:
                await self._deadline.__aexit__(
                    type(finish_error), finish_error, finish_error.__traceback__
                )
                raise
            else:
                await self._deadline.__aexit__(exc_type, exc, traceback)
        except TimeoutError as deadline_error:
            raise DatabaseUnavailableError() from deadline_error


class AllocatedBatch(Batch):
    def __init__(self, client: AllocatedPrisma):
        super().__init__(client)
        self.client = client

    async def commit(self):
        async def operation():
            return await super(AllocatedBatch, self).commit()

        if self.client.transaction_owner is not None:
            return await self.client.transaction_owner.query(operation)
        if self.client.allocation is None:
            raise RuntimeError("Prisma client has no allocation owner")
        return await self.client.allocation.query(operation)
