from __future__ import annotations

from typing import Any

from src.config import DatabaseConnectionSettings


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

    async def connect(self, database_settings: DatabaseConnectionSettings | None = None) -> None:
        try:
            from prisma import Prisma  # type: ignore
        except Exception:
            self.client = None
            return

        if database_settings is None:
            self.client = Prisma()
        else:
            self.client = Prisma(datasource={"url": database_settings.url})
        await self.client.connect()

    async def disconnect(self) -> None:
        if self.client is not None:
            await self.client.disconnect()


prisma_manager = PrismaClientManager()
telemetry_prisma_manager = PrismaClientManager()
