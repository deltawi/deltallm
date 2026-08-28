from __future__ import annotations

import json
from typing import Any


class BatchErrorRemediationRepository:
    """Bounded data access for the operator-run historical error scrub."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_terminal_error_rows(
        self, *, after_item_id: str, limit: int
    ) -> list[dict[str, Any]]:
        rows = await self.db.query_raw(
            """
            SELECT item_id,
                   status,
                   last_error IS NOT NULL AS has_last_error,
                   error_body IS NOT NULL AS has_error_body,
                   LEFT(error_body ->> 'retry_category', 64) AS retry_category
            FROM deltallm_batch_item
            WHERE item_id > $1
              AND status IN ('completed', 'failed', 'cancelled')
              AND (last_error IS NOT NULL OR error_body IS NOT NULL)
            ORDER BY item_id
            LIMIT $2
            """,
            after_item_id,
            limit,
        )
        return [dict(row) for row in rows]

    async def update_terminal_error_rows(self, updates: list[dict[str, Any]]) -> int:
        if not updates:
            return 0
        return int(
            await self.db.execute_raw(
                """
                UPDATE deltallm_batch_item AS item
                SET last_error = patch.last_error,
                    error_body = patch.error_body
                FROM jsonb_to_recordset($1::jsonb)
                     AS patch(item_id text, last_error text, error_body jsonb)
                WHERE item.item_id = patch.item_id
                  AND item.status IN ('completed', 'failed', 'cancelled')
                """,
                json.dumps(updates, separators=(",", ":")),
            )
        )
