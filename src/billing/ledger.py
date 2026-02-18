from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SpendLedgerService:
    """Maintains cumulative spend counters for key/user/team/org entities."""

    def __init__(self, db_client: Any | None) -> None:
        self.db = db_client

    async def increment_spend(
        self,
        *,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        cost: float,
    ) -> None:
        if self.db is None or cost <= 0:
            return

        await self._increment_table(
            table="litellm_verificationtoken",
            id_column="token",
            entity_id=api_key,
            amount=cost,
        )
        await self._increment_table(
            table="litellm_usertable",
            id_column="user_id",
            entity_id=user_id,
            amount=cost,
        )
        await self._increment_table(
            table="litellm_teamtable",
            id_column="team_id",
            entity_id=team_id,
            amount=cost,
        )
        await self._increment_table(
            table="litellm_organizationtable",
            id_column="organization_id",
            entity_id=organization_id,
            amount=cost,
        )

    async def _increment_table(self, *, table: str, id_column: str, entity_id: str | None, amount: float) -> None:
        if not entity_id:
            return

        try:
            await self.db.query_raw(
                f"""
                UPDATE {table}
                SET spend = COALESCE(spend, 0) + $1,
                    updated_at = NOW()
                WHERE {id_column} = $2
                """,
                amount,
                entity_id,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("failed to increment spend", extra={"table": table, "entity_id": entity_id, "error": str(exc)})
