from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.billing.budget import BudgetEnforcementService, BudgetExceeded, BudgetStateUnavailable
from tests.test_billing import CombinedBudgetDB


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [None, "", "bad", "NaN", "Infinity", "-1"])
async def test_budgeted_unknown_spend_never_becomes_zero(amount):
    db = CombinedBudgetDB(
        [
            {
                "entity_type": "team_model",
                "entity_id": "team/model",
                "max_budget": 10,
                "spend": amount,
            }
        ]
    )
    service = BudgetEnforcementService(db, query_mode="combined")
    with pytest.raises(BudgetStateUnavailable) as error:
        await service.check_budgets(
            api_key=None, user_id=None, team_id="team", organization_id=None, model="model"
        )
    assert error.value.status_code == 503
    assert error.value.affects_deployment_health is False
    assert len(db.query_calls) == 1
    assert "deltallm_spendlog_events" not in db.query_calls[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["", "bad", "NaN", "Infinity", "-1"])
async def test_corrupt_budget_limit_never_disables_enforcement(limit):
    db = CombinedBudgetDB(
        [{"entity_type": "key", "entity_id": "key", "max_budget": limit, "spend": 0}]
    )
    with pytest.raises(BudgetStateUnavailable):
        await BudgetEnforcementService(db, query_mode="combined").check_budgets(
            api_key="key",
            user_id=None,
            team_id=None,
            organization_id=None,
        )


@pytest.mark.asyncio
async def test_exact_spend_does_not_round_up_to_a_denial():
    row = {
        "entity_type": "key",
        "entity_id": "key",
        "max_budget": "1",
        "spend": "0.999999999999999999",
    }
    db = CombinedBudgetDB([row])
    service = BudgetEnforcementService(db, query_mode="combined")
    args = dict(api_key="key", user_id=None, team_id=None, organization_id=None)
    await service.check_budgets(**args)
    row["spend"] = "1.000000000000000000"
    with pytest.raises(BudgetExceeded) as exc:
        await service.check_budgets(**args)
    assert exc.value.spend == Decimal(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "shadow", "combined"])
async def test_entire_budget_operation_has_a_deadline_and_propagates_cancellation(mode):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowDB:
        async def query_raw(self, *_args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    service = BudgetEnforcementService(SlowDB(), query_mode=mode, query_timeout_seconds=0.01)
    args = dict(api_key="key", user_id=None, team_id=None, organization_id=None)
    with pytest.raises(BudgetStateUnavailable):
        await service.check_budgets(**args)
    assert cancelled.is_set()
    cancelled.clear()
    task = asyncio.create_task(service.check_budgets(**args))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("exhausted", [False, True])
async def test_shadow_timeout_cannot_change_the_completed_legacy_decision(exhausted):
    class DB:
        async def query_raw(self, query, *_args):
            if "WITH requested" in query:
                await asyncio.Event().wait()
            return [{"entity_id": "key", "max_budget": 1, "spend": 1 if exhausted else 0}]

    service = BudgetEnforcementService(
        DB(), query_mode="shadow", shadow_sample_rate=1, query_timeout_seconds=0.01
    )
    args = dict(api_key="key", user_id=None, team_id=None, organization_id=None)
    if exhausted:
        with pytest.raises(BudgetExceeded):
            await service.check_budgets(**args)
    else:
        await service.check_budgets(**args)
