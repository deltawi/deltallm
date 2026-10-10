"""Native admin balances are bounded, exact, and explicit on failure."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request

from src.api.admin.accounting_budget import apply_accounting_balances
from src.billing.accounting_protocol import AccountingScope
from src.billing.budget import BudgetStateUnavailable, budget_read_period
from src.db.accounting_budget_reads import AccountingBudgetReadRepository


def entity(**changes):
    return {
        "scope_id": "scope",
        "legacy_spend": "2.1",
        "budget_duration": None,
        "budget_reset_at": None,
        "metadata": None,
        "committed": None,
        "window_ends_at": None,
        "soft_budget": None,
        "max_budget": None,
        **changes,
    }


@pytest.mark.parametrize(
    "scope",
    [
        AccountingScope.API_KEY,
        AccountingScope.USER,
        AccountingScope.TEAM,
        AccountingScope.ORGANIZATION,
    ],
)
async def test_unlimited_scope_reads_one_batch_of_facts_and_retained_legacy_balance(scope):
    db = SimpleNamespace(
        query_raw=AsyncMock(side_effect=[[entity()], [{"scope_id": "scope", "spend": "0.6"}]])
    )
    values = await AccountingBudgetReadRepository(db).balances(scope, ["scope"])
    assert values["scope"].spend == Decimal("2.7")
    assert db.query_raw.await_count == 2
    assert db.query_raw.await_args_list[1].args[1:] == (["scope"], [None], [True])


async def test_projected_charges_are_excluded_only_when_legacy_period_is_current():
    now = datetime.now(UTC)
    db = SimpleNamespace(
        query_raw=AsyncMock(
            side_effect=[
                [
                    entity(
                        scope_id="current",
                        budget_duration="1h",
                        budget_reset_at=now + timedelta(minutes=30),
                    ),
                    entity(
                        scope_id="expired",
                        budget_duration="1h",
                        budget_reset_at=now - timedelta(minutes=30),
                    ),
                ],
                [{"scope_id": "current", "spend": "0.6"}, {"scope_id": "expired", "spend": "0.6"}],
            ]
        )
    )
    values = await AccountingBudgetReadRepository(db).balances(
        AccountingScope.ORGANIZATION, ["current", "expired"]
    )
    assert values["current"].spend == Decimal("2.7")
    assert values["expired"].spend == Decimal("0.6")
    query, identities, starts, current = db.query_raw.await_args_list[1].args
    assert identities == ["current", "expired"] and len(starts) == 2
    assert current == [True, False]
    assert "NOT wanted.legacy_current OR NOT EXISTS" in query and "legacy.id=f.event_id" in query
    assert db.query_raw.await_count == 2


async def test_finite_scope_reads_only_current_authority_and_reset_date():
    end = datetime(2026, 11, 1, tzinfo=UTC)
    db = SimpleNamespace(
        query_raw=AsyncMock(
            return_value=[
                entity(committed="0.6", max_budget=10, budget_duration="1mo", window_ends_at=end)
            ]
        )
    )
    value = (
        await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, ["scope"])
    )["scope"]
    assert value.spend == Decimal("0.6") and value.reset_at == end
    db.query_raw.assert_awaited_once()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [entity(scope_id="other")],
        [entity(committed="NaN")],
        [entity(max_budget=10)],
        [entity(legacy_spend="-1")],
    ],
)
async def test_missing_or_corrupt_authority_is_not_zero(rows):
    db = SimpleNamespace(query_raw=AsyncMock(return_value=rows))
    with pytest.raises(BudgetStateUnavailable):
        await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, ["scope"])


@pytest.mark.parametrize("ids", [["scope"] * 2, ["x"] * 501, [""], ["x" * 257]])
async def test_bad_page_never_reads_database(ids):
    db = SimpleNamespace(query_raw=AsyncMock())
    with pytest.raises(ValueError):
        await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, ids)
    db.query_raw.assert_not_awaited()


def test_soft_only_monthly_read_uses_existing_anchor_and_discards_old_legacy_period():
    period = budget_read_period(
        "1mo",
        datetime(2026, 2, 28, tzinfo=UTC),
        {"_budget_reset": {"monthly_anchor_day": 31}},
        now=datetime(2026, 3, 12, tzinfo=UTC),
    )
    assert period.starts_at == datetime(2026, 2, 28, tzinfo=UTC)
    assert period.ends_at == datetime(2026, 3, 31, tzinfo=UTC)
    assert period.legacy_current is False


def test_corrupt_reset_duration_fails_explicitly_instead_of_overflowing():
    with pytest.raises(BudgetStateUnavailable):
        budget_read_period(
            "999999999mo",
            datetime(2025, 1, 1, tzinfo=UTC),
            None,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )


async def test_admin_contract_keeps_numeric_spend_and_fails_explicitly_when_unavailable():
    app = FastAPI()
    db = SimpleNamespace(query_raw=AsyncMock(return_value=[entity(committed="0.6", max_budget=10)]))
    app.state.accounting_budget_reads = AccountingBudgetReadRepository(db)
    request = Request({"type": "http", "app": app})
    rows = [{"organization_id": "scope", "spend": 0.0}]
    await apply_accounting_balances(request, rows, AccountingScope.ORGANIZATION)
    assert rows == [{"organization_id": "scope", "spend": 0.6}]
    db.query_raw.return_value = []
    with pytest.raises(HTTPException) as error:
        await apply_accounting_balances(request, rows, AccountingScope.ORGANIZATION)
    assert error.value.status_code == 503
    assert rows[0]["spend"] == 0.6


async def test_legacy_admin_path_adds_no_calls():
    app = FastAPI()
    request = Request({"type": "http", "app": app})
    rows = [{"organization_id": "scope", "spend": 2.1}]
    await apply_accounting_balances(request, rows, AccountingScope.ORGANIZATION)
    assert rows[0]["spend"] == 2.1


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("database connection lost")])
async def test_database_failure_is_unavailable_not_a_zero_balance(error):
    db = SimpleNamespace(query_raw=AsyncMock(side_effect=error))
    with pytest.raises(BudgetStateUnavailable):
        await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, ["scope"])


async def test_cancelled_balance_read_propagates_cancellation():
    db = SimpleNamespace(query_raw=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, ["scope"])
