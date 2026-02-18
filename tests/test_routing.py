from __future__ import annotations

import pytest

from src.router import RedisStateBackend, Router, RouterConfig, RoutingStrategy, build_deployment_registry


@pytest.mark.asyncio
async def test_deployments_health_endpoint(client):
    response = await client.get("/health/deployments")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"healthy", "degraded"}
    assert payload["total_count"] >= 1
    assert isinstance(payload["deployments"], list)


@pytest.mark.asyncio
async def test_least_busy_strategy_selects_lowest_active_requests():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "litellm_params": {"model": "openai/gpt-4o-mini"}},
                {"deployment_id": "dep-b", "litellm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.LEAST_BUSY,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    await state.increment_active("dep-a")
    selected = await router.select_deployment("gpt-4o-mini", {})
    assert selected is not None
    assert selected.deployment_id == "dep-b"
