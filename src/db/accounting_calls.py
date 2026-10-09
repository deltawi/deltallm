"""One deadline and error owner for accounting database calls."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from enum import StrEnum
from time import perf_counter
from typing import Protocol

from src.db.telemetry_acceptance import AcceptanceFailure, classify_acceptance_failure
from src.metrics.accounting import observe_accounting_database_call


class AccountingQueryClient(Protocol):
    async def query_raw(
        self, query: str, *parameters: object
    ) -> Sequence[Mapping[str, object]]: ...


class AccountingResultFailure(StrEnum):
    INCOMPLETE_RESULT = "incomplete_result"
    INVALID_RESULT = "invalid_result"


class AccountingProtocolUnavailable(RuntimeError):
    """Protocol error with a fixed operator-visible reason."""

    def __init__(self, reason: AcceptanceFailure | AccountingResultFailure) -> None:
        super().__init__("accounting protocol unavailable")
        self.reason = reason.value


class AccountingDatabaseCalls:
    """Use the existing pool with one bounded statement and error policy."""

    def __init__(self, db: AccountingQueryClient, *, statement_budget_seconds: float) -> None:
        if not 0.01 <= statement_budget_seconds <= 2.0:
            raise ValueError("accounting statement budget must be between 10ms and 2s")
        self._db = db
        self.statement_budget_seconds = statement_budget_seconds

    async def call(
        self, operation: str, query: str, *parameters: object, expires_at: float
    ) -> Sequence[Mapping[str, object]]:
        started = perf_counter()
        remaining = expires_at - asyncio.get_running_loop().time()
        if not math.isfinite(remaining) or remaining <= 0:
            observe_accounting_database_call(operation, perf_counter() - started, "error")
            raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)
        try:
            async with asyncio.timeout(min(remaining, self.statement_budget_seconds)):
                rows = await self._db.query_raw(query, *parameters)
        except asyncio.CancelledError:
            observe_accounting_database_call(operation, perf_counter() - started, "cancelled")
            raise
        except Exception as exc:
            observe_accounting_database_call(operation, perf_counter() - started, "error")
            raise AccountingProtocolUnavailable(classify_acceptance_failure(exc)) from None
        observe_accounting_database_call(operation, perf_counter() - started, "success")
        return rows

    def attempt_deadline(self, expires_at: float) -> float:
        """Keep one statement window for exact ambiguity recovery."""

        return expires_at - self.statement_budget_seconds


def outcome_may_be_ambiguous(exc: AccountingProtocolUnavailable) -> bool:
    return exc.reason in {
        AcceptanceFailure.DEADLINE.value,
        AcceptanceFailure.STATEMENT_CANCELLED.value,
        AcceptanceFailure.CONNECTION.value,
        AcceptanceFailure.DATABASE_UNAVAILABLE.value,
        AcceptanceFailure.UNKNOWN.value,
    }
