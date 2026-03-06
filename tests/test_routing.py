from __future__ import annotations

import pytest

from src.router import (
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)


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
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
                {"deployment_id": "dep-b", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
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


def test_build_deployment_registry_supports_explicit_route_groups():
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"weight": 1},
                },
                {
                    "deployment_id": "dep-b",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"weight": 1},
                },
            ]
        },
        route_groups=[
            {
                "key": "support-fast",
                "enabled": True,
                "members": [
                    {"deployment_id": "dep-b", "weight": 8},
                    {"deployment_id": "dep-a"},
                ],
            }
        ],
    )

    assert "gpt-4o-mini" in registry
    assert "support-fast" in registry
    assert [item.deployment_id for item in registry["support-fast"]] == ["dep-b", "dep-a"]
    assert registry["support-fast"][0].weight == 8


@pytest.mark.asyncio
async def test_group_policy_overrides_global_strategy():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "members": [
                {"deployment_id": "dep-a"},
                {"deployment_id": "dep-b"},
            ],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"input_cost_per_token": 0.1},
                },
                {
                    "deployment_id": "dep-b",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"input_cost_per_token": 0.9},
                },
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.COST_BASED,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )

    await state.increment_active("dep-a")
    await state.increment_active("dep-a")

    selected_in_group = await router.select_deployment("support-route", {})
    selected_legacy = await router.select_deployment("gpt-4o-mini", {})

    assert selected_in_group is not None
    assert selected_in_group.deployment_id == "dep-b"
    assert selected_legacy is not None
    assert selected_legacy.deployment_id == "dep-a"


@pytest.mark.asyncio
async def test_router_records_route_decision_envelope():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "policy_version": 7,
            "members": [{"deployment_id": "dep-a"}],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )
    request_context: dict[str, object] = {}

    selected = await router.select_deployment("support-route", request_context)

    assert selected is not None
    decision = request_context.get("route_decision")
    assert isinstance(decision, dict)
    assert decision["model_group"] == "support-route"
    assert decision["strategy"] == "least-busy"
    assert decision["policy_version"] == 7
    assert decision["selected_deployment_id"] == "dep-a"


@pytest.mark.asyncio
async def test_router_exposes_failover_overrides_from_policy():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "policy_version": 3,
            "timeouts": {"global_ms": 750},
            "retry": {"max_attempts": 2, "retryable_error_classes": ["timeout", "rate_limit"]},
            "members": [{"deployment_id": "dep-a"}],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )
    request_context: dict[str, object] = {}

    selected = await router.select_deployment("support-route", request_context)

    assert selected is not None
    policy = request_context.get("route_policy")
    assert isinstance(policy, dict)
    assert policy["timeout_seconds"] == 0.75
    assert policy["retry_max_attempts"] == 2
    assert policy["retryable_error_classes"] == ["rate_limit", "timeout"]
