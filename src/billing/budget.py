from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.billing.alerts import AlertService

logger = logging.getLogger(__name__)

_BUDGET_RESET_METADATA_KEY = "_budget_reset"
_MONTHLY_ANCHOR_DAY_KEY = "monthly_anchor_day"
_MAX_BUDGET_DURATION_AMOUNT = 10_000


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
            FROM deltallm_teamtable
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

        counter_rows = await self.db.query_raw(
            """
            SELECT spend
            FROM deltallm_teammodelspend
            WHERE team_id = $1 AND model = $2
            LIMIT 1
            """,
            team_id,
            model,
        )
        if counter_rows:
            current_spend = _to_float(counter_rows[0].get("spend"))
        else:
            spend_rows = await self.db.query_raw(
                """
                SELECT COALESCE(SUM(spend), 0) AS total
                FROM deltallm_spendlog_events
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
            "key": ("deltallm_verificationtoken", "token", "NULL AS soft_budget", "metadata"),
            "user": ("deltallm_usertable", "user_id", "NULL AS soft_budget", "metadata"),
            "team": ("deltallm_teamtable", "team_id", "NULL AS soft_budget", "metadata"),
            "org": ("deltallm_organizationtable", "organization_id", "soft_budget", "metadata"),
        }
        table_info = table_map.get(entity_type)
        if table_info is None:
            return None

        table, column, soft_budget_expr, metadata_expr = table_info
        rows = await self.db.query_raw(
            f"""
            SELECT {column} AS entity_id, max_budget, {soft_budget_expr}, spend, budget_duration, budget_reset_at, {metadata_expr} AS metadata
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

        monthly_anchor_day = _monthly_anchor_day(entity.get("metadata"))
        inferred_monthly_anchor_day = None
        if monthly_anchor_day is None and _duration_unit(duration) == "mo":
            inferred_monthly_anchor_day = reset_at.day
            monthly_anchor_day = inferred_monthly_anchor_day

        try:
            next_reset = _next_reset_after(
                duration=duration,
                previous_reset_at=reset_at,
                now=now,
                monthly_anchor_day=monthly_anchor_day,
            )
        except (OverflowError, ValueError) as exc:
            logger.warning(
                "failed to calculate budget reset",
                extra={
                    "entity_type": entity_type,
                    "entity_id": entity.get("entity_id"),
                    "budget_duration": duration,
                    "error": str(exc),
                },
            )
            return entity
        if next_reset is None:
            return entity

        table_map = {
            "key": ("deltallm_verificationtoken", "token"),
            "user": ("deltallm_usertable", "user_id"),
            "team": ("deltallm_teamtable", "team_id"),
            "org": ("deltallm_organizationtable", "organization_id"),
        }
        table_info = table_map.get(entity_type)
        if table_info is None:
            return entity

        table, column = table_info
        entity_id = entity.get("entity_id")
        if entity_id is None:
            return entity

        try:
            updated_count = await self.db.execute_raw(
                f"""
                UPDATE {table}
                SET spend = 0,
                    budget_reset_at = $1::timestamp,
                    metadata = CASE
                        WHEN $4::int IS NULL THEN metadata
                        ELSE jsonb_set(
                            COALESCE(metadata, '{{}}'::jsonb),
                            '{{_budget_reset}}',
                            CASE
                                WHEN jsonb_typeof(metadata->'_budget_reset') = 'object'
                                THEN metadata->'_budget_reset'
                                ELSE '{{}}'::jsonb
                            END
                                || jsonb_build_object('monthly_anchor_day', $4::int),
                            true
                        )
                    END,
                    updated_at = NOW()
                WHERE {column} = $2
                  AND budget_reset_at IS NOT DISTINCT FROM $3::timestamp
                """,
                _as_utc_naive(next_reset),
                entity_id,
                _as_utc_naive(reset_at),
                inferred_monthly_anchor_day,
            )
            if _affected_row_count(updated_count) <= 0:
                refreshed = await self._get_entity(entity_type, str(entity_id))
                return refreshed or entity

            entity["spend"] = 0
            entity["budget_reset_at"] = next_reset
            if inferred_monthly_anchor_day is not None:
                entity["metadata"] = _with_monthly_anchor_day(entity.get("metadata"), inferred_monthly_anchor_day)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("failed to reset budget", extra={"entity_type": entity_type, "entity_id": entity_id, "error": str(exc)})

        return entity


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _next_reset_after(
    *,
    duration: str,
    previous_reset_at: datetime,
    now: datetime,
    monthly_anchor_day: int | None = None,
) -> datetime | None:
    parsed = _parse_duration(duration)
    if parsed is None:
        return None

    amount, unit = parsed
    try:
        if unit in {"h", "d"}:
            return _next_fixed_reset_after(
                previous_reset_at,
                amount=amount,
                unit=unit,
                now=now,
            )
        if unit == "mo":
            return _next_month_reset_after(
                previous_reset_at,
                amount=amount,
                now=now,
                monthly_anchor_day=monthly_anchor_day,
            )
    except (OverflowError, ValueError):
        return None
    return None


def _parse_duration(duration: Any) -> tuple[int, str] | None:
    if not isinstance(duration, str):
        return None
    if duration.endswith("mo"):
        unit = "mo"
        value = duration[:-2]
    else:
        unit = duration[-1:]
        value = duration[:-1]

    if not value.isdigit():
        return None

    amount = int(value)
    if amount <= 0 or amount > _MAX_BUDGET_DURATION_AMOUNT:
        return None
    if unit not in {"h", "d", "mo"}:
        return None
    return amount, unit


def _duration_unit(duration: Any) -> str | None:
    parsed = _parse_duration(duration)
    return parsed[1] if parsed is not None else None


def _next_fixed_reset_after(value: datetime, *, amount: int, unit: str, now: datetime) -> datetime | None:
    if unit == "h":
        step = timedelta(hours=amount)
    elif unit == "d":
        step = timedelta(days=amount)
    else:
        return None
    elapsed_intervals = max(0, (now - value) // step)
    return value + step * (elapsed_intervals + 1)


def _next_month_reset_after(
    value: datetime,
    *,
    amount: int,
    now: datetime,
    monthly_anchor_day: int | None,
) -> datetime:
    elapsed_months = max(0, (now.year - value.year) * 12 + (now.month - value.month))
    elapsed_intervals = max(1, elapsed_months // amount)
    next_reset = _add_months(value, amount * elapsed_intervals, anchor_day=monthly_anchor_day)
    while next_reset <= now:
        elapsed_intervals += 1
        next_reset = _add_months(value, amount * elapsed_intervals, anchor_day=monthly_anchor_day)
    return next_reset


def _add_months(value: datetime, months: int, *, anchor_day: int | None = None) -> datetime:
    zero_based_month = value.month - 1 + months
    year = value.year + zero_based_month // 12
    month = zero_based_month % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    if anchor_day is not None:
        day = min(anchor_day, last_day)
    else:
        day = last_day if _is_last_day_of_month(value) else min(value.day, last_day)
    return value.replace(year=year, month=month, day=day)


def _is_last_day_of_month(value: datetime) -> bool:
    return value.day == calendar.monthrange(value.year, value.month)[1]


def _affected_row_count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _monthly_anchor_day(metadata: Any) -> int | None:
    if not isinstance(metadata, dict):
        return None
    settings = metadata.get(_BUDGET_RESET_METADATA_KEY)
    if not isinstance(settings, dict):
        return None
    try:
        day = int(settings.get(_MONTHLY_ANCHOR_DAY_KEY))
    except (TypeError, ValueError):
        return None
    if 1 <= day <= 31:
        return day
    return None


def _with_monthly_anchor_day(metadata: Any, day: int) -> dict[str, Any]:
    next_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    raw_settings = next_metadata.get(_BUDGET_RESET_METADATA_KEY)
    settings = dict(raw_settings) if isinstance(raw_settings, dict) else {}
    settings[_MONTHLY_ANCHOR_DAY_KEY] = day
    next_metadata[_BUDGET_RESET_METADATA_KEY] = settings
    return next_metadata


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


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
