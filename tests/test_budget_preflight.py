from unittest.mock import AsyncMock

import pytest

from src.billing.budget import BudgetEnforcementService
from tests.test_billing import CombinedBudgetDB


@pytest.mark.asyncio
async def test_unavailable_budget_returns_safe_503_before_provider_execution(client, test_app):
    test_app.state.budget_service = BudgetEnforcementService(
        CombinedBudgetDB(
            [{"entity_type": "key", "entity_id": "private-key", "max_budget": 10, "spend": None}]
        ),
        query_mode="combined",
    )
    provider = AsyncMock(side_effect=AssertionError("provider must not execute"))
    test_app.state.http_client.post = provider
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "budget_state_unavailable"
    assert "private-key" not in response.text
    provider.assert_not_awaited()
