"""Finite Prisma admission whose ownership survives caller cancellation.

The adapter is intentionally tied to the frozen Prisma client. Model actions and
raw queries share _execute; interactive transaction copies retain this owner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
from src.db.allocation_config import DatabasePolicy
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
        # Required acceptance and receipts need a finite burst buffer. Other
        # business allocations keep immediate shedding; health may borrow one waiter.
        self._business_waiters = (
            policy.connections if policy.allocation in {"telemetry", "telemetry_settlement"} else 0
        )
        self.gate = BoundedCapacityGate(
            concurrency=policy.connections, max_waiters=self._business_waiters
        )
        self.tasks: set[asyncio.Task] = set()
        self.closed = False
        self._probe_active = False

    async def acquire(self, *, timeout_seconds: float | None = None):
        if self.closed:
            raise DatabaseUnavailableError()
        try:
            await self.gate.acquire(
                timeout_seconds=min(
                    self.policy.acquisition_seconds,
                    timeout_seconds
                    if timeout_seconds is not None
                    else self.policy.acquisition_seconds,
                )
            )
        except CapacityGateFull:
            _events.labels(self.policy.allocation, "full").inc()
            raise DatabaseUnavailableError() from None
        except CapacityGateTimedOut:
            _events.labels(self.policy.allocation, "queue_timeout").inc()
            raise DatabaseUnavailableError() from None
        if self.closed:
            await self.gate.release()
            raise DatabaseUnavailableError()
        _occupied.labels(self.policy.allocation).inc()

    async def release(self):
        await self.gate.release()
        _occupied.labels(self.policy.allocation).dec()

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
        deadline = monotonic() + self.policy.acquisition_seconds + self.policy.statement_seconds
        await self.acquire()
        task = self.spawn(self._query(operation))
        return await self._wait_query(task, deadline)

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
            await self.acquire()
        except BaseException:
            self._probe_active = False
            raise
        try:
            await self.gate.reconfigure(
                concurrency=self.policy.connections, max_waiters=max(1, self._business_waiters)
            )
        except BaseException:
            self._probe_active = False
            await self.release()
            raise
        task = self.spawn(self._query(operation, readiness=True))
        return await self._wait_query(task, deadline)

    async def _wait_query(self, task: asyncio.Task[T], deadline: float) -> T:
        try:
            async with asyncio.timeout(max(0, deadline - monotonic())):
                return await asyncio.shield(task)
        except TimeoutError:
            _events.labels(self.policy.allocation, "caller_deadline").inc()
            raise DatabaseUnavailableError() from None
        except asyncio.CancelledError:
            _events.labels(self.policy.allocation, "caller_cancelled").inc()
            raise

    async def _query(self, operation: Callable[[], Awaitable[T]], *, readiness: bool = False) -> T:
        started = monotonic()
        outcome = "error"
        try:
            result = await _native_call(operation, self.policy.native_query_seconds)
            outcome = "completed"
            return result
        finally:
            _events.labels(self.policy.allocation, outcome).inc()
            _duration.labels(self.policy.allocation, "readiness" if readiness else "query").observe(
                monotonic() - started
            )
            if readiness:
                self._probe_active = False
                await self.gate.reconfigure(
                    concurrency=self.policy.connections, max_waiters=self._business_waiters
                )
            await self.release()

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
        await self.owner.acquire(timeout_seconds=self.acquisition_seconds)
        self._owns_slot = True
        self.expires_at = (
            monotonic() + self.owner.policy.native_query_seconds + self.transaction_seconds
        )
        task = self.owner.spawn(self._start())
        try:
            async with asyncio.timeout(max(0, acquisition_deadline - monotonic())):
                return await asyncio.shield(task)
        except (asyncio.CancelledError, TimeoutError) as exc:
            self._finishing = True
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

    async def _release(self):
        if self._owns_slot:
            self._owns_slot = False
            if (
                self._expiration_task is not None
                and self._expiration_task is not asyncio.current_task()
            ):
                self._expiration_task.cancel()
            await self.owner.release()

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
