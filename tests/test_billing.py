from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.billing.budget import BudgetEnforcementService, BudgetExceeded
from src.billing.spend import SpendTrackingService


class RecordingDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def query_raw(self, query: str, *args):
        self.calls.append((query, args))
        return []


class BudgetDB:
    async def query_raw(self, query: str, *args):
        normalized = " ".join(query.lower().split())
        if "from litellm_verificationtoken" in normalized:
            return [
                {
                    "entity_id": args[0],
                    "max_budget": 10.0,
                    "soft_budget": 8.0,
                    "spend": 12.0,
                    "budget_duration": None,
                    "budget_reset_at": None,
                }
            ]
        return []


class SpendQueryDB:
    async def query_raw(self, query: str, *args):
        normalized = " ".join(query.lower().split())
        if "total_requests" in normalized and "from litellm_spendlogs" in normalized:
            return [
                {
                    "total_spend": 1.25,
                    "total_tokens": 200,
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "total_requests": 5,
                }
            ]
        if "count(*) as total" in normalized:
            return [{"total": 1}]
        if "from litellm_spendlogs" in normalized and "order by start_time desc" in normalized:
            return [
                {
                    "id": "log_1",
                    "request_id": "req_1",
                    "call_type": "completion",
                    "model": "gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "hashed-key",
                    "spend": 0.01,
                    "total_tokens": 20,
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "start_time": "2026-02-13T00:00:00+00:00",
                    "end_time": "2026-02-13T00:00:01+00:00",
                    "user": "u1",
                    "team_id": "t1",
                    "cache_hit": False,
                    "request_tags": ["tag-a"],
                }
            ]
        return []


@pytest.mark.asyncio
async def test_spend_tracking_writes_log_and_ledger_updates():
    db = RecordingDB()
    service = SpendTrackingService(db_client=db)

    await service.log_spend(
        request_id="req_123",
        api_key="key_hash",
        user_id="user_1",
        team_id="team_1",
        organization_id="org_1",
        end_user_id="end_1",
        model="gpt-4o-mini",
        call_type="completion",
        usage={"total_tokens": 10, "prompt_tokens": 6, "completion_tokens": 4},
        cost=0.02,
        metadata={"api_base": "https://api.openai.com/v1", "tags": ["prod"]},
    )

    assert any("insert into litellm_spendlogs" in q.lower() for q, _ in db.calls)
    assert any("update litellm_verificationtoken" in q.lower() for q, _ in db.calls)
    assert any("update litellm_usertable" in q.lower() for q, _ in db.calls)
    assert any("update litellm_teamtable" in q.lower() for q, _ in db.calls)
    assert any("update litellm_organizationtable" in q.lower() for q, _ in db.calls)


@pytest.mark.asyncio
async def test_budget_enforcement_raises_when_hard_budget_exceeded():
    service = BudgetEnforcementService(db_client=BudgetDB())

    with pytest.raises(BudgetExceeded):
        await service.check_budgets(
            api_key="key_hash",
            user_id=None,
            team_id=None,
            organization_id=None,
            model="gpt-4o-mini",
        )


@pytest.mark.asyncio
async def test_spend_logs_endpoint_returns_paginated_data(client, test_app):
    test_app.state.prisma_manager = SimpleNamespace(client=SpendQueryDB())
    test_app.state.settings.master_key = "mk-test"

    response = await client.get(
        "/spend/logs",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["logs"][0]["request_id"] == "req_1"


@pytest.mark.asyncio
async def test_global_spend_summary_endpoint(client, test_app):
    test_app.state.prisma_manager = SimpleNamespace(client=SpendQueryDB())
    test_app.state.settings.master_key = "mk-test"

    response = await client.get(
        "/global/spend",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_spend"] == 1.25
    assert payload["total_requests"] == 5
