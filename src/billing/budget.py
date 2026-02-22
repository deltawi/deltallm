from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.billing.alerts import AlertService

logger = logging.getLogger(__name__)


@dataclass
class BudgetExceeded(Exception):
    entity_type: str
    entity_id: str
    spend: float
    max_budget: float

    def __str__(self) -> str:
        return f"{self.entity_type} {self.entity_id} budget exceeded: ${self.spend:.2f} / ${self.max_budget:.2f}"


class BudgetEnforcementService:
    """Checks hard and soft budgets for key/user/team/org entities."""

    def __init__(self, db_client: Any | None, alert_service: AlertService | None = None) -> None:
        self.db = db_client
        self.alerts = alert_service

    async def check_budgets(
        self,
        *,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        model: str | None = None,
    ) -> None:
        if self.db is None:
            return

        await self._check_entity_budget("key", api_key)
        await self._check_entity_budget("user", user_id)
        await self._check_entity_budget("team", team_id)
        await self._check_entity_budget("org", organization_id)

        if team_id and model:
            await self._check_team_model_budget(team_id=team_id, model=model)

    async def _check_entity_budget(self, entity_type: str, entity_id: str | None) -> None:
        if not entity_id:
            return

        entity = await self._get_entity(entity_type, entity_id)
        if entity is None:
            return

        entity = await self._check_budget_reset(entity_type, entity)

        max_budget = _to_float_or_none(entity.get("max_budget"))
        soft_budget = _to_float_or_none(entity.get("soft_budget"))
        spend = _to_float(entity.get("spend"))

        if max_budget is not None and spend >= max_budget:
            raise BudgetExceeded(entity_type=entity_type, entity_id=entity_id, spend=spend, max_budget=max_budget)

        if soft_budget is not None and spend >= soft_budget and self.alerts is not None:
            await self.alerts.send_budget_alert(
                entity_type=entity_type,
                entity_id=entity_id,
                current_spend=spend,
                soft_budget=soft_budget,
                hard_budget=max_budget,
            )

    async def _check_team_model_budget(self, team_id: str, model: str) -> None:
        rows = await self.db.query_raw(
            """
            SELECT model_max_budget
            FROM litellm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            team_id,
        )
        if not rows:
            return

        budgets = rows[0].get("model_max_budget")
        if not isinstance(budgets, dict):
            return

        max_budget = _to_float_or_none(budgets.get(model))
        if max_budget is None:
            return

        spend_rows = await self.db.query_raw(
            """
            SELECT COALESCE(SUM(spend), 0) AS total
            FROM litellm_spendlogs
            WHERE team_id = $1 AND model = $2
            """,
            team_id,
            model,
        )
        current_spend = _to_float((spend_rows[0] if spend_rows else {}).get("total"))
        if current_spend >= max_budget:
            raise BudgetExceeded(
                entity_type="team_model",
                entity_id=f"{team_id}/{model}",
                spend=current_spend,
                max_budget=max_budget,
            )

    async def _get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        table_map = {
            "key": ("litellm_verificationtoken", "token"),
            "user": ("litellm_usertable", "user_id"),
            "team": ("litellm_teamtable", "team_id"),
            "org": ("litellm_organizationtable", "organization_id"),
        }
        table_info = table_map.get(entity_type)
        if table_info is None:
            return None

        table, column = table_info
        rows = await self.db.query_raw(
            f"""
            SELECT {column} AS entity_id, max_budget, soft_budget, spend, budget_duration, budget_reset_at
            FROM {table}
            WHERE {column} = $1
            LIMIT 1
            """,
            entity_id,
        )
        if not rows:
            return None
        return dict(rows[0])

    async def _check_budget_reset(self, entity_type: str, entity: dict[str, Any]) -> dict[str, Any]:
        duration = entity.get("budget_duration")
        reset_at_raw = entity.get("budget_reset_at")
        if not duration or not reset_at_raw:
            return entity

        reset_at = _as_datetime(reset_at_raw)
        if reset_at is None:
            return entity

        now = datetime.now(tz=UTC)
        if reset_at > now:
            return entity

        next_reset = _next_reset(duration=duration, now=now)
        if next_reset is None:
            return entity

        table_map = {
            "key": ("litellm_verificationtoken", "token"),
            "user": ("litellm_usertable", "user_id"),
            "team": ("litellm_teamtable", "team_id"),
            "org": ("litellm_organizationtable", "organization_id"),
        }
        table_info = table_map.get(entity_type)
        if table_info is None:
            return entity

        table, column = table_info
        entity_id = entity.get("entity_id")
        if entity_id is None:
            return entity

        try:
            await self.db.query_raw(
                f"""
                UPDATE {table}
                SET spend = 0,
                    budget_reset_at = $1,
                    updated_at = NOW()
                WHERE {column} = $2
                """,
                next_reset,
                entity_id,
            )
            entity["spend"] = 0
            entity["budget_reset_at"] = next_reset
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("failed to reset budget", extra={"entity_type": entity_type, "entity_id": entity_id, "error": str(exc)})

        return entity


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    return None


def _next_reset(*, duration: str, now: datetime) -> datetime | None:
    unit = duration[-1:]
    value = duration[:-1]
    if not value.isdigit():
        return None

    amount = int(value)
    if amount <= 0:
        return None

    if unit == "h":
        return now + timedelta(hours=amount)
    if unit == "d":
        return now + timedelta(days=amount)
    return None


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None
