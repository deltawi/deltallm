from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.billing.money import canonical_money, money_string

logger = logging.getLogger(__name__)


class SpendLedgerService:
    """Maintains cumulative spend counters for key/user/team/org entities."""

    def __init__(self, db_client: Any | None, *, strict: bool = False) -> None:
        self.db = db_client
        self.strict = strict

    async def increment_spend(
        self,
        *,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        model: str | None,
        cost: Decimal | float | str,
    ) -> None:
        exact_cost = canonical_money(cost)
        if self.db is None or exact_cost <= 0:
            return

        await self._increment_table(
            table="deltallm_verificationtoken",
            id_column="token",
            entity_id=api_key,
            amount=exact_cost,
        )
        await self._increment_table(
            table="deltallm_usertable",
            id_column="user_id",
            entity_id=user_id,
            amount=exact_cost,
        )
        await self._increment_table(
            table="deltallm_teamtable",
            id_column="team_id",
            entity_id=team_id,
            amount=exact_cost,
        )
        await self._increment_table(
            table="deltallm_organizationtable",
            id_column="organization_id",
            entity_id=organization_id,
            amount=exact_cost,
        )
        await self._increment_team_model_counter(team_id=team_id, model=model, amount=exact_cost)

    async def increment_spend_batch(
        self,
        *,
        api_keys: dict[str, Decimal],
        users: dict[str, Decimal],
        teams: dict[str, Decimal],
        organizations: dict[str, Decimal],
        team_models: dict[tuple[str, str], Decimal],
    ) -> dict[str, int]:
        """Apply one deterministic bulk delta per ledger entity type."""

        if self.db is None:
            raise RuntimeError("spend ledger database is unavailable")
        counts = {
            "api_key": await self._bulk_increment_table(
                table="deltallm_verificationtoken",
                id_column="token",
                deltas=api_keys,
            ),
            "user": await self._bulk_increment_table(
                table="deltallm_usertable",
                id_column="user_id",
                deltas=users,
            ),
            "team": await self._bulk_increment_table(
                table="deltallm_teamtable",
                id_column="team_id",
                deltas=teams,
            ),
            "organization": await self._bulk_increment_table(
                table="deltallm_organizationtable",
                id_column="organization_id",
                deltas=organizations,
            ),
            "team_model": await self._bulk_increment_team_models(team_models),
        }
        return counts

    async def _bulk_increment_table(
        self,
        *,
        table: str,
        id_column: str,
        deltas: dict[str, Decimal],
    ) -> int:
        normalized = sorted(
            (str(entity_id), canonical_money(amount))
            for entity_id, amount in deltas.items()
            if entity_id and canonical_money(amount) > 0
        )
        if not normalized:
            return 0
        entity_ids = [entity_id for entity_id, _ in normalized]
        amounts = [money_string(amount) for _, amount in normalized]
        await self.db.execute_raw(
            f"""
            WITH deltas AS MATERIALIZED (
                SELECT entity_id, amount
                FROM UNNEST($1::text[], $2::numeric[])
                    AS input(entity_id, amount)
            ),
            locked AS MATERIALIZED (
                SELECT target.{id_column} AS entity_id
                FROM {table} target
                JOIN deltas ON deltas.entity_id = target.{id_column}
                ORDER BY target.{id_column}
                FOR UPDATE
            )
            UPDATE {table} target
            SET spend_exact = COALESCE(target.spend_exact, target.spend::numeric, 0) + deltas.amount,
                spend = (COALESCE(target.spend_exact, target.spend::numeric, 0) + deltas.amount)::double precision,
                updated_at = NOW()
            FROM deltas
            JOIN locked ON locked.entity_id = deltas.entity_id
            WHERE target.{id_column} = deltas.entity_id
            """,
            entity_ids,
            amounts,
        )
        return len(normalized)

    async def _bulk_increment_team_models(
        self,
        deltas: dict[tuple[str, str], Decimal],
    ) -> int:
        normalized = sorted(
            (str(team_id), str(model), canonical_money(amount))
            for (team_id, model), amount in deltas.items()
            if team_id and model and canonical_money(amount) > 0
        )
        if not normalized:
            return 0
        team_ids = [team_id for team_id, _, _ in normalized]
        models = [model for _, model, _ in normalized]
        amounts = [money_string(amount) for _, _, amount in normalized]
        await self.db.execute_raw(
            """
            INSERT INTO deltallm_teammodelspend (team_id, model, spend, spend_exact, updated_at)
            SELECT team_id, model, amount::double precision, amount, NOW()
            FROM UNNEST($1::text[], $2::text[], $3::numeric[])
                AS input(team_id, model, amount)
            ORDER BY team_id, model
            ON CONFLICT (team_id, model)
            DO UPDATE SET
                spend_exact = COALESCE(
                    deltallm_teammodelspend.spend_exact,
                    deltallm_teammodelspend.spend::numeric,
                    0
                ) + EXCLUDED.spend_exact,
                spend = (
                    COALESCE(
                        deltallm_teammodelspend.spend_exact,
                        deltallm_teammodelspend.spend::numeric,
                        0
                    ) + EXCLUDED.spend_exact
                )::double precision,
                updated_at = NOW()
            """,
            team_ids,
            models,
            amounts,
        )
        return len(normalized)

    async def _increment_table(
        self, *, table: str, id_column: str, entity_id: str | None, amount: Decimal
    ) -> None:
        if not entity_id:
            return

        try:
            await self.db.execute_raw(
                f"""
                UPDATE {table}
                SET spend_exact = COALESCE(spend_exact, spend::numeric, 0) + $1::numeric,
                    spend = (COALESCE(spend_exact, spend::numeric, 0) + $1::numeric)::double precision,
                    updated_at = NOW()
                WHERE {id_column} = $2
                """,
                money_string(amount),
                entity_id,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            if self.strict:
                raise
            logger.warning(
                "failed to increment spend",
                extra={"table": table, "entity_id": entity_id, "error": str(exc)},
            )

    async def _increment_team_model_counter(
        self, *, team_id: str | None, model: str | None, amount: Decimal
    ) -> None:
        if not team_id or not model:
            return

        try:
            await self.db.execute_raw(
                """
                INSERT INTO deltallm_teammodelspend (team_id, model, spend, spend_exact, updated_at)
                VALUES ($1, $2, $3::numeric::double precision, $3::numeric, NOW())
                ON CONFLICT (team_id, model)
                DO UPDATE SET
                    spend_exact = COALESCE(
                        deltallm_teammodelspend.spend_exact,
                        deltallm_teammodelspend.spend::numeric,
                        0
                    ) + EXCLUDED.spend_exact,
                    spend = (
                        COALESCE(
                            deltallm_teammodelspend.spend_exact,
                            deltallm_teammodelspend.spend::numeric,
                            0
                        ) + EXCLUDED.spend_exact
                    )::double precision,
                    updated_at = NOW()
                """,
                team_id,
                model,
                money_string(amount),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            if self.strict:
                raise
            logger.warning(
                "failed to increment team-model spend",
                extra={"team_id": team_id, "model": model, "error": str(exc)},
            )
