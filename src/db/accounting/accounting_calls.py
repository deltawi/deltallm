"""One deadline and error owner for accounting database calls."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import StrEnum
from time import perf_counter
from typing import Protocol, TypeVar

from src.db.runtime.telemetry_acceptance import AcceptanceFailure, classify_acceptance_failure
from src.metrics.accounting import observe_accounting_database_call

logger = logging.getLogger(__name__)

# Accounting SQL functions raise fixed codes such as accounting_read_model_attribution.
# Only these fixed codes are logged. Other database text can contain customer data.
_REJECTION_CODE = re.compile(r"accounting_[a-z0-9_]{1,96}")
_SQLSTATE = re.compile(r"[0-9A-Z]{5}")
_LOG_INTERVAL_SECONDS = 30.0
_LOG_KEYS_MAX = 256
# A process that starts while the database is busy waits for it, up to its
# startup deadline, instead of exiting on one slow statement.
STARTUP_RETRY_PAUSE_SECONDS = 0.1
STARTUP_RETRY_MAX_PAUSE_SECONDS = 1.0
_STARTUP_RETRY_REASONS = frozenset(
    failure.value
    for failure in (
        AcceptanceFailure.DEADLINE,
        AcceptanceFailure.POOL_TIMEOUT,
        AcceptanceFailure.CLIENT_POOL_TIMEOUT,
        AcceptanceFailure.LOCK_TIMEOUT,
        AcceptanceFailure.STATEMENT_CANCELLED,
        AcceptanceFailure.TRANSACTION_EXPIRED,
        AcceptanceFailure.CONNECTION,
        AcceptanceFailure.DATABASE_UNAVAILABLE,
    )
)

_T = TypeVar("_T")


class AccountingQueryClient(Protocol):
    async def query_raw(
        self, query: str, *parameters: object
    ) -> Sequence[Mapping[str, object]]: ...


class AccountingResultFailure(StrEnum):
    INCOMPLETE_RESULT = "incomplete_result"
    INVALID_RESULT = "invalid_result"


class AccountingProtocolUnavailable(RuntimeError):
    """Protocol error with a fixed operator-visible reason."""

    def __init__(
        self,
        reason: AcceptanceFailure | AccountingResultFailure,
        *,
        rejection: str | None = None,
    ) -> None:
        super().__init__("accounting protocol unavailable")
        self.reason = reason.value
        # A fixed accounting SQL code, never free database text.
        self.rejection = rejection

    @property
    def detail(self) -> str:
        return self.reason if self.rejection is None else f"{self.reason}:{self.rejection}"


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
            failure = AccountingProtocolUnavailable(
                classify_acceptance_failure(exc), rejection=accounting_rejection_code(exc)
            )
            _log_failure(operation, failure, type(exc).__name__, database_sqlstate(exc))
            raise failure from None
        observe_accounting_database_call(operation, perf_counter() - started, "success")
        return rows

    def attempt_deadline(self, expires_at: float) -> float:
        """Keep one statement window for exact ambiguity recovery."""

        return expires_at - self.statement_budget_seconds


async def retry_idempotent_startup_call(
    call: Callable[[], Awaitable[_T]], *, expires_at: float
) -> _T:
    """Repeat an idempotent startup statement while the database is briefly busy.

    Only availability failures are repeated, and never past ``expires_at``.
    A rejected or invalid result is raised at once.
    """
    pause = STARTUP_RETRY_PAUSE_SECONDS
    while True:
        try:
            return await call()
        except AccountingProtocolUnavailable as failure:
            if (
                failure.rejection is not None
                or failure.reason not in _STARTUP_RETRY_REASONS
                or expires_at - asyncio.get_running_loop().time() <= pause
            ):
                raise
        await asyncio.sleep(pause)
        pause = min(pause * 2, STARTUP_RETRY_MAX_PAUSE_SECONDS)


def database_sqlstate(exc: BaseException) -> str | None:
    """Return the PostgreSQL SQLSTATE of a failed statement, if there is one."""

    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(4):
        if current is None or id(current) in seen:
            return None
        seen.add(id(current))
        sqlstate = _own_sqlstate(current)
        if sqlstate is not None:
            return sqlstate
        current = current.__cause__
    return None


def _own_sqlstate(exc: BaseException) -> str | None:
    meta = getattr(exc, "meta", None)
    sqlstate = meta.get("code") if isinstance(meta, Mapping) else None
    sqlstate = sqlstate or getattr(exc, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) and _SQLSTATE.fullmatch(sqlstate) else None


def accounting_rejection_code(exc: BaseException) -> str | None:
    """Return the fixed code an accounting SQL function raised, if there is one."""

    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(4):
        if current is None or id(current) in seen:
            return None
        seen.add(id(current))
        if _own_sqlstate(current) == "P0001":
            text = str(current).strip().removeprefix("ERROR:").strip()
            return text if _REJECTION_CODE.fullmatch(text) else None
        current = current.__cause__
    return None


_last_logged: dict[tuple[str, str, str | None, str | None], float] = {}


def _log_failure(
    operation: str,
    failure: AccountingProtocolUnavailable,
    error_type: str,
    sqlstate: str | None,
) -> None:
    # Keep one line per operation and failure kind for each interval. A stuck
    # page retries often, and the data plane must not flood the log.
    key = (operation, failure.reason, failure.rejection, sqlstate)
    now = perf_counter()
    last = _last_logged.get(key)
    if last is not None and now - last < _LOG_INTERVAL_SECONDS:
        return
    if last is None and len(_last_logged) >= _LOG_KEYS_MAX:
        _last_logged.clear()
    _last_logged[key] = now
    logger.warning(
        "Accounting database call failed: operation=%s reason=%s rejection=%s "
        "sqlstate=%s error_type=%s",
        operation,
        failure.reason,
        failure.rejection or "-",
        sqlstate or "-",
        error_type,
    )


def outcome_may_be_ambiguous(exc: AccountingProtocolUnavailable) -> bool:
    return exc.reason in {
        AcceptanceFailure.DEADLINE.value,
        AcceptanceFailure.STATEMENT_CANCELLED.value,
        AcceptanceFailure.CONNECTION.value,
        AcceptanceFailure.DATABASE_UNAVAILABLE.value,
        AcceptanceFailure.UNKNOWN.value,
    }
