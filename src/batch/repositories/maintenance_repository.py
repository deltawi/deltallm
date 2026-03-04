from __future__ import annotations

from datetime import datetime
from typing import Any


class BatchMaintenanceRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_expired_terminal_job_ids(self, *, now: datetime, limit: int = 100) -> list[str]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT batch_id
            FROM deltallm_batch_job
            WHERE expires_at IS NOT NULL
              AND expires_at < $1::timestamp
              AND status IN ('completed', 'failed', 'cancelled', 'expired')
            ORDER BY expires_at ASC
            LIMIT $2
            """,
            now,
            max(1, min(limit, 1000)),
        )
        return [str(row.get("batch_id") or "") for row in rows if row.get("batch_id")]

    async def delete_job_metadata(self, batch_id: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_batch_item
            WHERE batch_id = $1
            """,
            batch_id,
        )
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_batch_job
            WHERE batch_id = $1
            """,
            batch_id,
        )
