"""Batched persistence for the API-key last-used timestamp."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

# One statement per chunk keeps each array parameter and row-lock set bounded.
KEY_LAST_USED_WRITE_CHUNK = 1000

# The value lives in its own table: an update of the key row fires the
# organization lifecycle fence. SKIP LOCKED: a key row that another transaction
# holds for a key change (regeneration or deletion) is skipped, not waited
# for. The next flush writes a newer value. The guard keeps the newest value
# when replicas flush out of order.
_WRITE_LAST_USED_SQL = """
WITH usage AS (
    SELECT token, used_at
    FROM unnest($1::text[], $2::text[]::timestamptz[]) AS u(token, used_at)
),
live AS (
    SELECT vt.token, usage.used_at
    FROM deltallm_verificationtoken vt
    JOIN usage ON usage.token = vt.token
    ORDER BY vt.token
    FOR KEY SHARE OF vt SKIP LOCKED
)
INSERT INTO deltallm_verificationtoken_last_used AS lu (token, last_used_at)
SELECT token, used_at FROM live ORDER BY token
ON CONFLICT (token) DO UPDATE SET last_used_at = EXCLUDED.last_used_at
WHERE lu.last_used_at < EXCLUDED.last_used_at
"""


class KeyLastUsedDatabase(Protocol):
    async def execute_raw(self, query: str, *params: object) -> int: ...


class KeyLastUsedRepository:
    def __init__(self, db: KeyLastUsedDatabase) -> None:
        self.db = db

    async def write(self, usages: Mapping[str, datetime]) -> int:
        """Store the newest use per token hash and return the changed row count."""
        items = sorted(usages.items())
        updated = 0
        for start in range(0, len(items), KEY_LAST_USED_WRITE_CHUNK):
            chunk = items[start : start + KEY_LAST_USED_WRITE_CHUNK]
            updated += int(
                await self.db.execute_raw(
                    _WRITE_LAST_USED_SQL,
                    [token for token, _ in chunk],
                    [used_at.isoformat() for _, used_at in chunk],
                )
                or 0
            )
        return updated
