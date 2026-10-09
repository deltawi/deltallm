"""Control-plane balances use native authority without legacy counter writes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from src.billing.budgets.budget import BudgetStateUnavailable, budget_read_period
from src.billing.accounting.accounting_protocol import AccountingScope
from src.db.accounting_calls import (
    AccountingDatabaseCalls,
    AccountingProtocolUnavailable,
    AccountingQueryClient,
)

_SOURCES = {
    AccountingScope.API_KEY: ("deltallm_verificationtoken", "token", "api_key"),
    AccountingScope.USER: ("deltallm_usertable", "user_id", "user_id"),
    AccountingScope.TEAM: ("deltallm_teamtable", "team_id", "team_id"),
    AccountingScope.ORGANIZATION: (
        "deltallm_organizationtable",
        "organization_id",
        "organization_id",
    ),
}


@dataclass(frozen=True, slots=True)
class AccountingBudgetBalance:
    scope_id: str
    spend: Decimal
    reset_at: datetime | None


class AccountingBudgetReadRepository:
    """Read at most 500 authorized entities in two bounded control-pool calls.

    Finite budgets use committed window balances. Unlimited and soft-only
    scopes use projected facts plus the retained legacy period balance.
    Projection delay is reporting delay, not permission to spend.
    """

    def __init__(self, db: AccountingQueryClient) -> None:
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=2)

    async def balances(
        self, scope: AccountingScope, scope_ids: Sequence[str]
    ) -> dict[str, AccountingBudgetBalance]:
        if not scope_ids:
            return {}
        if scope not in _SOURCES or len(scope_ids) > 500 or len(set(scope_ids)) != len(scope_ids):
            raise ValueError("invalid accounting budget read page")
        if any(type(value) is not str or not 1 <= len(value) <= 256 for value in scope_ids):
            raise ValueError("invalid accounting budget scope identity")
        table, identity, fact_column = _SOURCES[scope]
        soft_column = "e.soft_budget" if scope is AccountingScope.ORGANIZATION else "NULL"
        expires_at = asyncio.get_running_loop().time() + 2
        async with asyncio.timeout(2):
            rows = await self._query(
                f"""
                SELECT e.{identity} AS scope_id,
                    COALESCE(e.spend_exact,e.spend::numeric)::text AS legacy_spend,
                    e.budget_duration,e.budget_reset_at,e.metadata,
                    w.committed_exact::text AS committed,w.window_ends_at,
                    {soft_column} AS soft_budget,e.max_budget
                FROM {table} e
                LEFT JOIN LATERAL (
                    SELECT w.committed_exact,w.window_ends_at
                    FROM deltallm_accounting_protocols p
                    JOIN deltallm_accounting_budget_windows w
                      ON w.protocol_name=p.protocol_name AND w.generation=p.generation
                    WHERE p.protocol_name='primary' AND p.state='active'
                      AND w.scope_type=$2 AND w.scope_id=e.{identity}
                      AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
                    ORDER BY w.window_id LIMIT 1
                ) w ON TRUE
                WHERE e.{identity}=ANY($1::text[])
                """,
                list(scope_ids),
                scope.value,
                expires_at=expires_at,
            )
            if {row.get("scope_id") for row in rows} != set(scope_ids) or len(rows) != len(
                scope_ids
            ):
                raise BudgetStateUnavailable()
            result: dict[str, AccountingBudgetBalance] = {}
            pending: list[str] = []
            starts: list[str | None] = []
            legacy_current: list[bool] = []
            now = datetime.now(UTC)
            for row in rows:
                identity_value = str(row["scope_id"])
                if row["committed"] is not None:
                    reset = _time(row["window_ends_at"])
                    # A lifetime window is not a recurring reset date.
                    reset = reset if row["budget_duration"] is not None else None
                    result[identity_value] = AccountingBudgetBalance(
                        identity_value, _money(row["committed"]), reset
                    )
                    continue
                if _finite_limit(row["max_budget"]):
                    # A finite policy without a current authority is unknown,
                    # not an unlimited or zero balance.
                    raise BudgetStateUnavailable()
                period = budget_read_period(
                    row["budget_duration"], _time(row["budget_reset_at"]), row["metadata"], now=now
                )
                base = _money(row["legacy_spend"]) if period.legacy_current else Decimal(0)
                result[identity_value] = AccountingBudgetBalance(
                    identity_value, base, period.ends_at
                )
                pending.append(identity_value)
                starts.append(period.starts_at.isoformat() if period.starts_at else None)
                legacy_current.append(period.legacy_current)
            if pending:
                totals = await self._query(
                    f"""
                    SELECT wanted.scope_id,COALESCE(sum(f.spend_exact),0)::text AS spend
                    FROM unnest($1::text[],$2::text[],$3::boolean[]) wanted(scope_id,period_start,legacy_current)
                    LEFT JOIN deltallm_accounting_usage_facts_v2 f
                      ON f.{fact_column}=wanted.scope_id
                     AND (wanted.period_start IS NULL OR f.start_time>=wanted.period_start::timestamptz)
                     AND (NOT wanted.legacy_current OR NOT EXISTS (
                         SELECT 1 FROM deltallm_spendlog_events legacy WHERE legacy.id=f.event_id
                     ))
                    GROUP BY wanted.scope_id
                    """,
                    pending,
                    starts,
                    legacy_current,
                    expires_at=expires_at,
                )
                if {row.get("scope_id") for row in totals} != set(pending) or len(totals) != len(
                    pending
                ):
                    raise BudgetStateUnavailable()
                for row in totals:
                    value = result[str(row["scope_id"])]
                    result[value.scope_id] = AccountingBudgetBalance(
                        value.scope_id, value.spend + _money(row["spend"]), value.reset_at
                    )
            return result

    async def _query(
        self, query: str, *parameters: object, expires_at: float
    ) -> Sequence[Mapping[str, object]]:
        try:
            return await self._calls.call("budget_read", query, *parameters, expires_at=expires_at)
        except AccountingProtocolUnavailable:
            raise BudgetStateUnavailable() from None


def _money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid budget balance")
        return amount
    except (InvalidOperation, ValueError):
        raise BudgetStateUnavailable() from None


def _finite_limit(value: object) -> bool:
    if value is None:
        return False
    try:
        limit = Decimal(str(value))
        if limit.is_nan():
            raise ValueError("invalid budget limit")
        return 0 <= limit < Decimal("1e20")
    except (InvalidOperation, ValueError):
        raise BudgetStateUnavailable() from None


def _time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise BudgetStateUnavailable() from None
    if not isinstance(value, datetime):
        raise BudgetStateUnavailable()
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
