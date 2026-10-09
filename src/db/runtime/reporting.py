"""Canonical reporting SQL allocation and deadline, shared by admin reports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from time import monotonic
from typing import Protocol, TypeVar

from src.services.spend_reporting_cache import (
    ReportingQueryTimedOut,
    ReportingRefreshBusy,
    SpendReportingCache,
)

_REPORTING_CANCELLATION_GRACE_MAX_SECONDS = 1.0
_REPORTING_ADVISORY_LOCK_NAMESPACE = 1_144_204_621
_ReportingResult = TypeVar("_ReportingResult")


class ReportingTransaction(Protocol):
    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]: ...


class ReportingDatabase(Protocol):
    def tx(
        self, *, max_wait: timedelta, timeout: timedelta
    ) -> AbstractAsyncContextManager[ReportingTransaction]: ...


def _reporting_cancellation_grace_seconds(execution_timeout: float) -> float:
    return min(
        _REPORTING_CANCELLATION_GRACE_MAX_SECONDS,
        max(0.005, execution_timeout * 0.1),
    )


def _reporting_statement_timeout_ms(deadline: float, cancellation_grace: float) -> int:
    usable_seconds = deadline - monotonic() - cancellation_grace
    if usable_seconds <= 0:
        raise ReportingQueryTimedOut(
            "Reporting query exhausted its execution deadline before the next database statement"
        )
    return max(1, int(usable_seconds * 1000))


def _is_reporting_database_timeout(exc: Exception) -> bool:
    metadata = getattr(exc, "meta", None)
    error_text = f"{exc} {metadata or ''}".lower()
    error_code = str(getattr(exc, "code", "") or "").lower()
    return (
        "statement timeout" in error_text
        or ("57014" in error_text and "canceling statement" in error_text)
        or "p2028" in error_code
        or "p2028" in error_text
        or "unable to start a transaction in the given time" in error_text
        or "transaction already closed" in error_text
        or ("transaction" in error_text and "timed out" in error_text)
    )


async def _run_reporting_statement(
    tx: ReportingTransaction,
    *,
    deadline: float,
    cancellation_grace: float,
    query: str,
    params: tuple[object, ...],
) -> list[dict[str, object]]:
    statement_timeout_ms = _reporting_statement_timeout_ms(deadline, cancellation_grace)
    try:
        await tx.query_raw(
            "SELECT set_config('statement_timeout', $1, true)",
            f"{statement_timeout_ms}ms",
        )
        return await tx.query_raw(query, *params)
    except ReportingQueryTimedOut:
        raise
    except Exception as exc:
        if _is_reporting_database_timeout(exc):
            raise ReportingQueryTimedOut(
                f"PostgreSQL cancelled a reporting query after {statement_timeout_ms}ms"
            ) from exc
        raise


async def _run_reporting_transaction(
    db: ReportingDatabase,
    cache: SpendReportingCache,
    operation: Callable[[ReportingTransaction, float, float], Awaitable[_ReportingResult]],
) -> _ReportingResult:
    """Run a reporting transaction within one connection-and-query deadline."""

    load_budget = cache.active_load_budget
    execution_timeout = load_budget.execution_timeout_seconds
    cancellation_grace = _reporting_cancellation_grace_seconds(execution_timeout)
    deadline = monotonic() + execution_timeout
    max_wait_seconds = max(0.001, execution_timeout - cancellation_grace)
    try:
        async with db.tx(
            max_wait=timedelta(seconds=max_wait_seconds),
            timeout=timedelta(seconds=execution_timeout),
        ) as tx:
            admission_rows = await tx.query_raw(
                """
                SELECT slot
                FROM generate_series(0, $2::integer - 1) AS slot
                WHERE pg_try_advisory_xact_lock($1::integer, slot)
                LIMIT 1
                """,
                _REPORTING_ADVISORY_LOCK_NAMESPACE,
                load_budget.global_max_concurrent_loads,
            )
            if not admission_rows:
                raise ReportingRefreshBusy("Global reporting query capacity is currently full")
            return await operation(tx, deadline, cancellation_grace)
    except ReportingQueryTimedOut:
        raise
    except Exception as exc:
        if _is_reporting_database_timeout(exc):
            raise ReportingQueryTimedOut(
                "The reporting transaction exceeded its database execution deadline"
            ) from exc
        raise


async def _run_reporting_query(
    db: ReportingDatabase,
    cache: SpendReportingCache,
    query: str,
    *params: object,
) -> list[dict[str, object]]:
    """Run a report with a transaction-local PostgreSQL statement deadline."""

    async def run_query(
        tx: ReportingTransaction, deadline: float, cancellation_grace: float
    ) -> list[dict[str, object]]:
        return await _run_reporting_statement(
            tx,
            deadline=deadline,
            cancellation_grace=cancellation_grace,
            query=query,
            params=params,
        )

    return await _run_reporting_transaction(db, cache, run_query)
