from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncContextManager, Literal, Protocol

from prisma.errors import PrismaError

from src.metrics.external_auth import external_auth_saturation
from src.auth.external_errors import ExternalAuthUnavailable
from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.db.platform_accounts import PlatformAccountDatabase


class ExternalTransactionDatabase(PlatformAccountDatabase, Protocol):
    def tx(
        self, *, max_wait: timedelta, timeout: timedelta
    ) -> AsyncContextManager[PlatformAccountDatabase]: ...


class ExternalAuthTransactions:
    """Reserve mutation, validation, and maintenance capacity in one four-connection pool."""

    def __init__(self, db: ExternalTransactionDatabase) -> None:
        self.db = db
        self.gates = {
            "mutation": BoundedCapacityGate(concurrency=2, max_waiters=8),
            "validation": BoundedCapacityGate(concurrency=1, max_waiters=8),
            "maintenance": BoundedCapacityGate(concurrency=1, max_waiters=0),
        }

    @asynccontextmanager
    async def transaction(
        self, kind: Literal["mutation", "validation", "maintenance"] = "mutation"
    ) -> AsyncIterator[PlatformAccountDatabase]:
        gate = self.gates[kind]
        try:
            await gate.acquire(timeout_seconds=0.05)
        except (CapacityGateFull, CapacityGateTimedOut) as exc:
            external_auth_saturation.labels(
                kind, "full" if isinstance(exc, CapacityGateFull) else "timeout"
            ).inc()
            raise ExternalAuthUnavailable() from exc
        try:
            async with self.db.tx(
                max_wait=timedelta(milliseconds=50), timeout=timedelta(milliseconds=750)
            ) as tx:
                await tx.query_raw(
                    "SELECT set_config('statement_timeout', '250ms', true), set_config('lock_timeout', '100ms', true), set_config('TimeZone', 'UTC', true)"
                )
                yield tx
        except PrismaError as exc:
            raise ExternalAuthUnavailable() from exc
        finally:
            await gate.release()
