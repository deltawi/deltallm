"""Observe existing outbox transaction boundaries without adding dependency calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from enum import StrEnum
import hashlib
import logging
import sys
from time import perf_counter
from types import TracebackType
from typing import TypeVar

import httpx
from prisma.errors import (
    ClientNotConnectedError,
    DataError,
    HTTPClientClosedError,
    TransactionError,
    TransactionExpiredError,
)

from src.metrics import telemetry_acceptance as metrics
from src.metrics.telemetry_acceptance import AcceptancePhase, TelemetryQueue

logger = logging.getLogger(__name__)
T = TypeVar("T")


class TelemetryDatabaseUnavailable(RuntimeError):
    """The producer has no configured durable database client."""


class AcceptanceFailure(StrEnum):
    CANCELLED = "cancelled"
    DEADLINE = "deadline_exceeded"
    POOL_TIMEOUT = "pool_timeout"
    CLIENT_POOL_TIMEOUT = "client_pool_timeout"
    LOCK_TIMEOUT = "lock_timeout"
    STATEMENT_CANCELLED = "statement_cancelled"
    TRANSACTION_EXPIRED = "transaction_expired"
    TRANSACTION_ERROR = "transaction_error"
    CONNECTION = "connection"
    DATABASE_UNAVAILABLE = "database_unavailable"
    INVALID_INPUT = "invalid_input"
    UNKNOWN = "unknown"


def classify_acceptance_failure(exc: BaseException) -> AcceptanceFailure:
    """Use structured codes/types only; never parse or publish database error text."""
    if isinstance(exc, asyncio.CancelledError):
        return AcceptanceFailure.CANCELLED
    if isinstance(exc, TelemetryDatabaseUnavailable):
        return AcceptanceFailure.DATABASE_UNAVAILABLE
    if isinstance(exc, httpx.PoolTimeout):
        return AcceptanceFailure.CLIENT_POOL_TIMEOUT
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return AcceptanceFailure.DEADLINE
    if isinstance(exc, TransactionExpiredError):
        return AcceptanceFailure.TRANSACTION_EXPIRED
    if isinstance(exc, TransactionError):
        return AcceptanceFailure.TRANSACTION_ERROR
    if isinstance(exc, (ClientNotConnectedError, HTTPClientClosedError, httpx.NetworkError)):
        return AcceptanceFailure.CONNECTION
    if isinstance(exc, DataError):
        code = exc.code if isinstance(exc.code, str) else None
        if code == "P2024":
            return AcceptanceFailure.POOL_TIMEOUT
        if code == "P2028":
            return AcceptanceFailure.TRANSACTION_ERROR
        if code == "P1008":
            return AcceptanceFailure.DEADLINE
        if code in {"P1001", "P1002", "P1011", "P1017"}:
            return AcceptanceFailure.CONNECTION
        sqlstate = exc.meta.get("code") if isinstance(exc.meta, dict) else None
        if sqlstate == "55P03":
            return AcceptanceFailure.LOCK_TIMEOUT
        if sqlstate == "57014":
            # PostgreSQL uses this code for statement timeout and explicit cancellation.
            return AcceptanceFailure.STATEMENT_CANCELLED
        if isinstance(sqlstate, str) and sqlstate.startswith("08"):
            return AcceptanceFailure.CONNECTION
    if isinstance(exc, ValueError):
        return AcceptanceFailure.INVALID_INPUT
    return AcceptanceFailure.UNKNOWN


class AcceptanceObservation:
    def __init__(
        self, queue: TelemetryQueue, *, owns_transaction: bool, event_id: str | None = None
    ) -> None:
        self.queue = queue
        self.owns_transaction = owns_transaction
        self.failed_phase = AcceptancePhase.PREPARE
        self.outcome = "success"
        self._bytes = 0
        self._started = 0.0
        self._event_fingerprint = (
            hashlib.sha256(event_id.encode("utf-8", errors="replace")).hexdigest()[:16]
            if isinstance(event_id, str) and 0 < len(event_id) <= 128
            else None
        )

    def __enter__(self) -> AcceptanceObservation:
        self._started = perf_counter()
        metrics.phase_in_flight.labels(self.queue.value, AcceptancePhase.TOTAL.value).inc()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        outcome = "success"
        if exc is not None:
            reason = classify_acceptance_failure(exc)
            outcome = "cancelled" if reason == AcceptanceFailure.CANCELLED else "error"
            self.outcome = outcome
            metrics.failures.labels(self.queue.value, self.failed_phase.value, reason.value).inc()
            if reason != AcceptanceFailure.CANCELLED:
                logger.warning(
                    "telemetry acceptance failed queue=%s phase=%s reason=%s event_fingerprint=%s",
                    self.queue.value,
                    self.failed_phase.value,
                    reason.value,
                    self._event_fingerprint,
                    extra={
                        "queue": self.queue.value,
                        "phase": self.failed_phase.value,
                        "reason": reason.value,
                        "event_fingerprint": self._event_fingerprint,
                    },
                )
        metrics.operations.labels(
            self.queue.value,
            self.outcome,
            "owned" if self.owns_transaction else "external",
        ).inc()
        metrics.phase_seconds.labels(
            self.queue.value, AcceptancePhase.TOTAL.value, outcome
        ).observe(max(0.0, perf_counter() - self._started))
        metrics.phase_in_flight.labels(self.queue.value, AcceptancePhase.TOTAL.value).dec()
        metrics.serialized_bytes.labels(self.queue.value).dec(self._bytes)

    @contextmanager
    def phase(self, phase: AcceptancePhase) -> Iterator[None]:
        started = perf_counter()
        gauge = metrics.phase_in_flight.labels(self.queue.value, phase.value)
        gauge.inc()
        outcome = "success"
        try:
            yield
        except BaseException as exc:
            self.failed_phase = phase
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            raise
        finally:
            gauge.dec()
            metrics.phase_seconds.labels(self.queue.value, phase.value, outcome).observe(
                max(0.0, perf_counter() - started)
            )

    def retain_serialized(self, payload: str) -> None:
        size = len(payload.encode("utf-8"))
        metrics.serialized_bytes.labels(self.queue.value).inc(size)
        self._bytes += size

    def record_result(self, statuses: Sequence[str]) -> None:
        known = set(statuses)
        if not known <= {"accepted", "duplicate", "full"}:
            raise ValueError("unknown telemetry enqueue status")
        self.outcome = next(iter(known)) if len(known) == 1 else "mixed" if known else "empty"
        if self.owns_transaction and statuses:
            metrics.events_per_commit.labels(self.queue.value).observe(statuses.count("accepted"))

    @asynccontextmanager
    async def transaction(self, context: AbstractAsyncContextManager[T]) -> AsyncIterator[T]:
        if not self.owns_transaction:
            # The caller owns commit/rollback; do not emit fictional acquisition/commit timings.
            async with context as tx:
                yield tx
            return
        with self.phase(AcceptancePhase.ACQUIRE):
            tx = await context.__aenter__()
        try:
            yield tx
        except BaseException:
            with self.phase(AcceptancePhase.ROLLBACK):
                suppressed = await context.__aexit__(*sys.exc_info())
            if not suppressed:
                raise
        else:
            with self.phase(AcceptancePhase.COMMIT):
                await context.__aexit__(None, None, None)
