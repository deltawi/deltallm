from __future__ import annotations

from typing import Any


ROUTING_RUNTIME_STATE_KEY = "routing_runtime"


class RoutingRuntimeRevisionRepository:
    """Own the monotonic revision for every durable routing input."""

    def __init__(self, prisma_client: Any | None) -> None:
        self.prisma = prisma_client

    async def get_revision(self) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            SELECT revision
            FROM deltallm_routeruntimestate
            WHERE state_key = $1
            """,
            ROUTING_RUNTIME_STATE_KEY,
        )
        return int(rows[0].get("revision") or 0) if rows else 0

    async def bump_revision(self, *, route_groups_initialized: bool = False) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_routeruntimestate
            SET revision = revision + 1,
                route_groups_initialized = route_groups_initialized OR $2,
                updated_at = NOW()
            WHERE state_key = $1
            RETURNING revision
            """,
            ROUTING_RUNTIME_STATE_KEY,
            route_groups_initialized,
        )
        if not rows:
            raise RuntimeError("routing runtime revision state is missing")
        return int(rows[0].get("revision") or 0)
