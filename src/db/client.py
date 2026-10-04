from __future__ import annotations

from typing import Any

from src.config import DatabaseConnectionSettings
from src.db.allocation_config import DatabasePolicy


def is_prisma_transaction_client(client: object | None) -> bool:
    """Return whether *client* is already bound to a Prisma transaction."""

    if client is None:
        return False
    checker = getattr(client, "is_transaction", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


class PrismaClientManager:
    def __init__(self) -> None:
        self.client: Any | None = None
        self.allocation = None

    async def connect(
        self,
        database_settings: DatabaseConnectionSettings | None = None,
        *,
        policy: DatabasePolicy | None = None,
    ) -> None:
        try:
            from prisma import Prisma  # type: ignore
        except Exception:
            if policy is not None:
                raise RuntimeError(
                    "Database allocations require the generated Prisma client"
                ) from None
            self.client = None
            return

        if policy is not None:
            from src.db.allocated_client import AllocatedPrisma, DatabaseOwner

            if database_settings is None:
                raise RuntimeError("An allocated database requires an explicit database URL")
            self.allocation = DatabaseOwner(policy)
            self.client = AllocatedPrisma(
                datasource={"url": policy.connection_url(database_settings.url)},
                allocation=self.allocation,
            )
        elif database_settings is None:
            self.client = Prisma()
        else:
            self.client = Prisma(datasource={"url": database_settings.url})
        await self.client.connect()
        if policy is not None:
            rows = await self.client.query_raw("""
                SELECT
                  EXTRACT(EPOCH FROM current_setting('statement_timeout')::interval)::double precision AS statement_seconds,
                  EXTRACT(EPOCH FROM current_setting('lock_timeout')::interval)::double precision AS lock_seconds,
                  EXTRACT(EPOCH FROM current_setting('idle_in_transaction_session_timeout')::interval)::double precision AS transaction_seconds
            """)
            if not rows or any(
                abs(float(rows[0][name]) - expected) > 0.001
                for name, expected in (
                    ("statement_seconds", policy.statement_seconds),
                    ("lock_seconds", policy.lock_seconds),
                    ("transaction_seconds", policy.transaction_seconds),
                )
            ):
                raise RuntimeError("Database server did not apply the required native deadlines")

    async def disconnect(self) -> None:
        if self.allocation is not None:
            await self.allocation.close()
        if self.client is not None:
            await self.client.disconnect()


prisma_manager = PrismaClientManager()
telemetry_prisma_manager = PrismaClientManager()
foreground_prisma_manager = PrismaClientManager()
telemetry_worker_prisma_manager = PrismaClientManager()

telemetry_settlement_prisma_manager = PrismaClientManager()
