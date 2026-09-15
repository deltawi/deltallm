"""Explicit maintenance repair; never called by inference or spend delivery."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from src.billing.money import money_string

if TYPE_CHECKING:
    from prisma import Prisma


class BudgetCounterChanged(RuntimeError):
    pass


class BudgetReconciliationRepository:
    def __init__(self, db: Prisma) -> None:
        self.db = db

    async def repair(
        self,
        *,
        team_id: str,
        model: str,
        verified_total: Decimal,
        expected_spend: Decimal | None,
        expected_updated_at: datetime | None,
    ) -> None:
        """CAS a reviewed complete total while writers are paused and drained.

        None for both expected fields means the row must be absent. The guard
        also prevents an accidental repair from overwriting a concurrent delta.
        No retained-history SUM can establish archive completeness here.
        """
        if not verified_total.is_finite() or verified_total < 0:
            raise ValueError("verified total must be finite and nonnegative")
        if (expected_spend is None) != (expected_updated_at is None):
            raise ValueError("both expected fields are required for an existing counter")
        if expected_spend is None:
            rows = await self.db.query_raw(
                """
                INSERT INTO deltallm_teammodelspend
                    (team_id, model, spend, spend_exact, updated_at, reconciled_at)
                VALUES ($1, $2, $3::numeric::double precision, $3::numeric, NOW(), NOW())
                ON CONFLICT (team_id, model) DO NOTHING
                RETURNING team_id
                """,
                team_id,
                model,
                money_string(verified_total),
            )
        else:
            rows = await self.db.query_raw(
                """
                UPDATE deltallm_teammodelspend
                SET spend = $3::numeric::double precision, spend_exact = $3::numeric,
                    updated_at = NOW(), reconciled_at = NOW()
                WHERE team_id = $1 AND model = $2
                  AND COALESCE(spend_exact, spend::numeric) = $4::numeric
                  AND updated_at = $5::timestamp
                  AND reserved_spend_exact = 0
                RETURNING team_id
                """,
                team_id,
                model,
                money_string(verified_total),
                str(expected_spend),
                expected_updated_at,
            )
        if not rows:
            raise BudgetCounterChanged("counter changed or has an outstanding reservation")
