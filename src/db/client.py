from __future__ import annotations

from typing import Any

from src.config import DatabaseConnectionSettings


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
