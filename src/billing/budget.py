from __future__ import annotations

import asyncio
import calendar
import hashlib
import logging
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from src.db.budgets import BudgetRepository
from src.models.errors import RoutingFailureAction, ServiceUnavailableError

logger = logging.getLogger(__name__)

_BUDGET_RESET_METADATA_KEY = "_budget_reset"
_MONTHLY_ANCHOR_DAY_KEY = "monthly_anchor_day"
_MAX_BUDGET_DURATION_AMOUNT = 10_000


@dataclass
class BudgetExceeded(Exception):
    entity_type: str
    entity_id: str
    spend: Decimal
    max_budget: Decimal

    def __str__(self) -> str:
        return f"{self.entity_type} {self.entity_id} budget exceeded: ${self.spend:.2f} / ${self.max_budget:.2f}"


class BudgetStateUnavailable(ServiceUnavailableError):
    """An authoritative budgeted counter cannot be safely evaluated."""

    def __init__(self) -> None:
        super().__init__(
            message="Budget state is temporarily unavailable",
            code="budget_state_unavailable",
            affects_deployment_health=False,
            routing_failure_action=RoutingFailureAction.FAIL_FAST,
        )


@dataclass(frozen=True)
class BudgetAlert:
    entity_type: str
    entity_id: str
    spend: Decimal
    soft_budget: Decimal
    hard_budget: Decimal | None


class BudgetAlertSink(Protocol):
    async def send_budget_alert(
        self,
        *,
        entity_type: str,
        entity_id: str,
        current_spend: Decimal,
        soft_budget: Decimal | None,
        hard_budget: Decimal | None,
    ) -> None: ...


class BudgetEnforcementService:
    """Checks hard and soft budgets for key/user/team/org entities."""

    def __init__(
        self,
        db_client: Any | None,
        alert_service: BudgetAlertSink | None = None,
        *,
        query_mode: str = "legacy",
        shadow_sample_rate: float = 0.01,
        query_timeout_seconds: float = 2.0,
    ) -> None:
        self.db = db_client
        self.alerts = alert_service
        self.repository = BudgetRepository(db_client)
        self.query_mode = str(query_mode or "legacy").strip().lower()
        if self.query_mode not in {"legacy", "shadow", "combined"}:
            raise ValueError("budget query mode must be legacy, shadow, or combined")
        self.shadow_sample_rate = min(1.0, max(0.0, float(shadow_sample_rate)))
        self.query_timeout_seconds = max(0.01, float(query_timeout_seconds))

    async def check_budgets(
        self,
        *,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        model: str | None = None,
    ) -> None:
        legacy_error = None
        alert = None
        try:
            async with asyncio.timeout(self.query_timeout_seconds):
                alert = await self._check_budgets(
                    api_key=api_key,
                    user_id=user_id,
                    team_id=team_id,
                    organization_id=organization_id,
                    model=model,
                )
        except BudgetExceeded as exc:
            legacy_error = exc
        except TimeoutError as exc:
            raise BudgetStateUnavailable() from exc

        if (
            self.db is not None
            and self.query_mode == "shadow"
            and self._should_shadow(
                api_key,
                user_id,
                team_id,
                organization_id,
                model,
            )
        ):
            await self._compare_shadow(
                api_key=api_key,
                user_id=user_id,
                team_id=team_id,
                organization_id=organization_id,
                model=model,
                legacy_error=legacy_error,
            )
        if legacy_error is not None:
            raise legacy_error
        if alert is not None and self.alerts is not None:
            await self.alerts.send_budget_alert(
                entity_type=alert.entity_type,
                entity_id=alert.entity_id,
                current_spend=alert.spend,
                soft_budget=alert.soft_budget,
                hard_budget=alert.hard_budget,
            )

    async def _check_budgets(
        self,
        *,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        model: str | None = None,
    ) -> BudgetAlert | None:
        if self.db is None:
            return

        if self.query_mode == "combined":
            return await self._check_combined_budgets(
                api_key=api_key,
                user_id=user_id,
                team_id=team_id,
                organization_id=organization_id,
                model=model,
            )

        await self._check_entity_budget("key", api_key)
        await self._check_entity_budget("user", user_id)
        await self._check_entity_budget("team", team_id)
        alert = await self._check_entity_budget("org", organization_id)
        if team_id and model:
            await self._check_team_model_budget(team_id=team_id, model=model)
        return alert

    async def _compare_shadow(
        self,
        *,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        model: str | None,
        legacy_error: BudgetExceeded | None,
    ) -> None:
        # Diagnostic work has its own small budget after legacy enforcement.
        # Its timeout or corrupt snapshot cannot change the completed decision.
        try:
            async with asyncio.timeout(min(0.1, self.query_timeout_seconds)):
                snapshot = await self.repository.get_snapshot(
                    api_key=api_key,
                    user_id=user_id,
                    team_id=team_id,
                    organization_id=organization_id,
                    model=model,
                )
                combined_error = self._first_hard_budget_error(snapshot)
            if _budget_error_signature(legacy_error) != _budget_error_signature(combined_error):
                logger.warning(
                    "budget query shadow mismatch",
                    extra={
                        "legacy": _budget_error_signature(legacy_error),
                        "combined": _budget_error_signature(combined_error),
                    },
                )
        except Exception:
            logger.warning("budget combined-query shadow read unavailable")

    async def _check_combined_budgets(
        self,
        *,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        model: str | None,
    ) -> BudgetAlert | None:
        async with asyncio.timeout(self.query_timeout_seconds):
            snapshot = await self.repository.get_snapshot(
                api_key=api_key,
                user_id=user_id,
                team_id=team_id,
                organization_id=organization_id,
                model=model,
            )
        alert = None
        for raw_entity in snapshot:
            entity = dict(raw_entity)
            entity_type = str(entity.get("entity_type") or "")
            entity_id = str(entity.get("entity_id") or "")
            if entity_type != "team_model":
                entity = await self._check_budget_reset(entity_type, entity)
            candidate = self._evaluate_entity_budget(entity_type, entity_id, entity)
            if candidate is not None:
                alert = candidate
        return alert

    def _evaluate_entity_budget(
        self,
        entity_type: str,
        entity_id: str,
        entity: dict[str, Any],
    ) -> BudgetAlert | None:
        max_budget = _budget_amount(entity.get("max_budget"), optional=True)
        soft_budget = _budget_amount(entity.get("soft_budget"), optional=True)
        if max_budget is None and soft_budget is None:
            return
        spend = _budget_amount(entity.get("spend"))
        if max_budget is not None and spend >= max_budget:
            raise BudgetExceeded(
                entity_type=entity_type,
                entity_id=entity_id,
                spend=spend,
                max_budget=max_budget,
            )
        if soft_budget is not None and spend >= soft_budget:
            return BudgetAlert(entity_type, entity_id, spend, soft_budget, max_budget)
        return None

    def _first_hard_budget_error(
        self,
        snapshot: list[dict[str, Any]],
    ) -> BudgetExceeded | None:
        for entity in snapshot:
            max_budget = _budget_amount(entity.get("max_budget"), optional=True)
            if max_budget is None:
                continue
            spend = _budget_amount(entity.get("spend"))
            if max_budget is not None and spend >= max_budget:
                return BudgetExceeded(
                    entity_type=str(entity.get("entity_type") or ""),
                    entity_id=str(entity.get("entity_id") or ""),
                    spend=spend,
                    max_budget=max_budget,
                )
        return None

    def _should_shadow(self, *values: str | None) -> bool:
        if self.shadow_sample_rate <= 0:
            return False
        if self.shadow_sample_rate >= 1:
            return True
        token = "\0".join(str(value or "") for value in values)
        bucket = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")
        return bucket / float(2**64) < self.shadow_sample_rate

    async def _check_entity_budget(
        self, entity_type: str, entity_id: str | None
    ) -> BudgetAlert | None:
        if not entity_id:
            return

        entity = await self._get_entity(entity_type, entity_id)
        if entity is None:
            return

        entity = await self._check_budget_reset(entity_type, entity)

        return self._evaluate_entity_budget(entity_type, entity_id, entity)

    async def _check_team_model_budget(self, team_id: str, model: str) -> None:
        rows = await self.repository.get_team_model_limits(team_id)
        if not rows:
            return

        budgets = rows[0].get("model_max_budget")
        if not isinstance(budgets, dict):
            return

        max_budget = _budget_amount(budgets.get(model), optional=True)
        if max_budget is None:
            return

        counter_rows = await self.repository.get_team_model_counter(team_id, model)
        if not counter_rows:
            raise BudgetStateUnavailable()
        current_spend = _budget_amount(counter_rows[0].get("spend"))
        if current_spend >= max_budget:
            raise BudgetExceeded(
                entity_type="team_model",
                entity_id=f"{team_id}/{model}",
                spend=current_spend,
                max_budget=max_budget,
            )

    async def _get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        return await self.repository.get_entity(entity_type, entity_id)

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
        except (OverflowError, ValueError):
            logger.warning(
                "failed to calculate budget reset",
                extra={
                    "entity_type": entity_type,
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
                    spend_exact = 0,
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
                entity["metadata"] = _with_monthly_anchor_day(
                    entity.get("metadata"), inferred_monthly_anchor_day
                )
        except Exception:  # pragma: no cover - defensive logging
            logger.warning(
                "failed to reset budget",
                extra={"entity_type": entity_type},
            )

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


def _next_fixed_reset_after(
    value: datetime, *, amount: int, unit: str, now: datetime
) -> datetime | None:
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


def _budget_error_signature(error: BudgetExceeded | None) -> str | None:
    if error is None:
        return None
    return error.entity_type


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


def _budget_amount(value: Any, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid economic state")
        return amount
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BudgetStateUnavailable() from exc
