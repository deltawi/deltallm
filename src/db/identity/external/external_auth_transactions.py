from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
import logging
from time import monotonic
from typing import AsyncContextManager, Literal, Protocol

from prisma.errors import PrismaError

from src.metrics.external_auth import external_auth_saturation
from src.auth.external_errors import ExternalAuthUnavailable
from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.db.identity.platform_accounts import PlatformAccountDatabase

logger = logging.getLogger(__name__)

TransactionKind = Literal["mutation", "validation", "maintenance"]

# Session validation runs on every Console read, so a short burst from one page queues
# for a slot instead of failing; mutations stay inside the one-second operation deadline.
ACQUIRE_TIMEOUT_SECONDS: dict[TransactionKind, float] = {
    "mutation": 0.05,
    "validation": 0.5,
    "maintenance": 0.05,
}
SATURATION_LOG_INTERVAL_SECONDS = 10.0


class ExternalTransactionDatabase(PlatformAccountDatabase, Protocol):
    def tx(
        self, *, max_wait: timedelta, timeout: timedelta
    ) -> AsyncContextManager[PlatformAccountDatabase]: ...


class ExternalAuthTransactions:
    """Reserve mutation, validation, and maintenance capacity in one five-connection pool."""

    def __init__(
        self,
        db: ExternalTransactionDatabase,
        *,
        transaction_timeout: timedelta = timedelta(milliseconds=750),
    ) -> None:
        self.db = db
        self.transaction_timeout = transaction_timeout
        self.gates = {
            "mutation": BoundedCapacityGate(concurrency=2, max_waiters=8),
            "validation": BoundedCapacityGate(concurrency=2, max_waiters=32),
            "maintenance": BoundedCapacityGate(concurrency=1, max_waiters=0),
        }
        self._saturation_logged_at: dict[str, float] = {}

    @asynccontextmanager
    async def transaction(
        self, kind: TransactionKind = "mutation"
    ) -> AsyncIterator[PlatformAccountDatabase]:
        gate = self.gates[kind]
        try:
            await gate.acquire(timeout_seconds=ACQUIRE_TIMEOUT_SECONDS[kind])
        except (CapacityGateFull, CapacityGateTimedOut) as exc:
            reason = "full" if isinstance(exc, CapacityGateFull) else "timeout"
            external_auth_saturation.labels(kind, reason).inc()
            self._log_saturation(kind, reason)
            raise ExternalAuthUnavailable() from exc
        try:
            async with self.db.tx(
                max_wait=timedelta(milliseconds=50), timeout=self.transaction_timeout
            ) as tx:
                await tx.query_raw(
                    "SELECT set_config('statement_timeout', '250ms', true), set_config('lock_timeout', '100ms', true), set_config('TimeZone', 'UTC', true)"
                )
                yield tx
        except PrismaError as exc:
            raise ExternalAuthUnavailable() from exc
        finally:
            await gate.release()

    def _log_saturation(self, kind: str, reason: str) -> None:
        now = monotonic()
        last = self._saturation_logged_at.get(kind)
        if last is not None and now - last < SATURATION_LOG_INTERVAL_SECONDS:
            return
        self._saturation_logged_at[kind] = now
        gate = self.gates[kind]
        logger.warning(
            "external auth %s capacity saturated (%s): active=%d waiters=%d",
            kind,
            reason,
            gate.active,
            gate.waiters,
        )
