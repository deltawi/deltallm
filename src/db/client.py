from __future__ import annotations

from typing import Any


class PrismaClientManager:
    def __init__(self) -> None:
        self.client: Any | None = None

    async def connect(self) -> None:
        try:
            from prisma import Prisma  # type: ignore
        except Exception:
            self.client = None
            return

        self.client = Prisma()
        await self.client.connect()

    async def disconnect(self) -> None:
        if self.client is not None:
            await self.client.disconnect()


prisma_manager = PrismaClientManager()
