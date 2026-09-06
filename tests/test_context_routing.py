from __future__ import annotations

import pytest

from src.models.errors import InvalidRequestError, ServiceUnavailableError
from src.router import (
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.router.context_policy import (
    RequestTokenDemand,
    build_combined_request_context,
    estimate_embedding_context_input_tokens,
    get_request_token_demand,
    parse_context_routing_policy,
    set_request_token_demand,
)


def _context_router(
    *,
    context: dict[str, object],
    small_model_info: dict[str, object] | None = None,
    large_model_info: dict[str, object] | None = None,
) -> tuple[Router, RedisStateBackend]:
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "context-route",
            "mode": "chat",
            "enabled": True,
            "strategy": "priority-based-routing",
            "context": context,
            "members": [
                {"deployment_id": "dep-small", "priority": 0},
                {"deployment_id": "dep-large", "priority": 1},
            ],
        }
    ]
    registry = build_deployment_registry(
        {
            "backing-model": [
                {
                    "deployment_id": "dep-small",
                    "deltallm_params": {"model": "openai/backing-model"},
                    "model_info": small_model_info or {},
                },
                {
                    "deployment_id": "dep-large",
                    "deltallm_params": {"model": "openai/backing-model"},
                    "model_info": large_model_info or {},
                },
            ]
        },
        route_groups=route_groups,
    )
    return (
        Router(
            strategy=RoutingStrategy.SIMPLE_SHUFFLE,
            state_backend=state,
            config=RouterConfig(
                route_group_policies=build_route_group_policies(route_groups),
            ),
            deployment_registry=registry,
        ),
        state,
    )


@pytest.mark.asyncio
async def test_context_policy_routes_large_request_to_capable_instance(monkeypatch):
    router, state = _context_router(
        context={"mode": "eligible-only", "safety_margin_tokens": 0},
        small_model_info={"max_tokens": 8_000},
        large_model_info={"max_tokens": 32_000},
    )
    calls = {"health": 0, "cooldown": 0}
    original_health = state.get_health_batch
    original_cooldown = state.get_cooldown_batch

    async def counted_health(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls["health"] += 1
        return await original_health(*args, **kwargs)

    async def counted_cooldown(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls["cooldown"] += 1
        return await original_cooldown(*args, **kwargs)

    monkeypatch.setattr(state, "get_health_batch", counted_health)
    monkeypatch.setattr(state, "get_cooldown_batch", counted_cooldown)
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=10_000, requested_output_tokens=1_000),
    )

    selected = await router.select_deployment("context-route", context)

    assert selected is not None
    assert selected.deployment_id == "dep-large"
    assert calls == {"health": 1, "cooldown": 1}


@pytest.mark.asyncio
async def test_smallest_sufficient_preserves_larger_instance_for_failover():
    router, _ = _context_router(
        context={"mode": "smallest-sufficient", "safety_margin_tokens": 0},
        small_model_info={"max_tokens": 32_000},
        large_model_info={"max_tokens": 128_000},
    )
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=4_000, requested_output_tokens=1_000),
    )

    plan = (await router.plan_deployments(["context-route"], context))["context-route"]

    assert [item.deployment_id for item in plan.deployments] == ["dep-small", "dep-large"]


@pytest.mark.asyncio
async def test_unknown_capacity_is_eligible_by_default_for_compatibility():
    router, _ = _context_router(
        context={"mode": "eligible-only", "safety_margin_tokens": 0},
        small_model_info={},
        large_model_info={"max_tokens": 128_000},
    )
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=4_000, requested_output_tokens=1_000),
    )

    selected = await router.select_deployment("context-route", context)

    assert selected is not None
    assert selected.deployment_id == "dep-small"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_capacity", [0, -1])
async def test_legacy_non_positive_capacity_is_treated_as_unknown(
    legacy_capacity: int,
):
    router, _ = _context_router(
        context={"mode": "eligible-only", "safety_margin_tokens": 0},
        small_model_info={"max_tokens": legacy_capacity},
        large_model_info={"max_tokens": 128_000},
    )
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=4_000, requested_output_tokens=1_000),
    )

    selected = await router.select_deployment("context-route", context)

    assert selected is not None
    assert selected.deployment_id == "dep-small"


@pytest.mark.asyncio
async def test_unknown_capacity_can_be_excluded_strictly():
    router, _ = _context_router(
        context={
            "mode": "eligible-only",
            "unknown_capacity": "exclude",
            "safety_margin_tokens": 0,
        },
        small_model_info={},
        large_model_info={},
    )
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=4_000, requested_output_tokens=1_000),
    )

    selected = await router.select_deployment("context-route", context)

    assert selected is None
    assert context["route_decision"]["reason"] == "context_capacity_unknown"


@pytest.mark.asyncio
async def test_context_capacity_exceeded_returns_stable_client_error():
    router, _ = _context_router(
        context={"mode": "eligible-only", "safety_margin_tokens": 100},
        small_model_info={"max_tokens": 8_000},
        large_model_info={"max_tokens": 16_000},
    )
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=15_000, requested_output_tokens=1_000),
    )
    selected = await router.select_deployment("context-route", context)

    assert selected is None
    assert context["route_decision"]["reason"] == "context_capacity_exceeded"
    with pytest.raises(InvalidRequestError) as exc_info:
        router.require_deployment(
            "context-route",
            selected,
            request_context=context,
        )
    assert exc_info.value.code == "context_length_exceeded"
    assert exc_info.value.affects_deployment_health is False


@pytest.mark.asyncio
async def test_capable_but_unhealthy_instance_preserves_service_unavailable_error():
    router, state = _context_router(
        context={"mode": "eligible-only", "safety_margin_tokens": 0},
        small_model_info={"max_tokens": 8_000},
        large_model_info={"max_tokens": 32_000},
    )
    large = router.deployment_registry["context-route"][1]
    await state.apply_manual_cooldown(large.health_ref, 60, "test")
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=10_000, requested_output_tokens=1_000),
    )
    selected = await router.select_deployment("context-route", context)

    assert selected is None
    assert context["route_decision"]["reason"] == "no_eligible_candidates"
    with pytest.raises(ServiceUnavailableError):
        router.require_deployment(
            "context-route",
            selected,
            request_context=context,
        )


@pytest.mark.asyncio
async def test_separate_input_and_output_limits_are_both_enforced():
    router, _ = _context_router(
        context={"mode": "eligible-only", "safety_margin_tokens": 100},
        small_model_info={"max_input_tokens": 5_000, "max_output_tokens": 4_000},
        large_model_info={"max_input_tokens": 16_000, "max_output_tokens": 512},
    )
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=5_000, requested_output_tokens=1_000),
    )

    selected = await router.select_deployment("context-route", context)

    assert selected is None
    assert context["route_decision"]["reason"] == "context_capacity_exceeded"


@pytest.mark.asyncio
async def test_anthropic_provider_output_default_is_reserved_before_routing():
    router, _ = _context_router(
        context={
            "mode": "eligible-only",
            "default_output_tokens": 1024,
            "safety_margin_tokens": 0,
        },
        small_model_info={"max_tokens": 5_000},
        large_model_info={"max_tokens": 8_000},
    )
    for deployment in router.deployment_registry["context-route"]:
        deployment.deltallm_params.update(
            {
                "provider": "anthropic",
                "model": "anthropic/claude-sonnet-4",
                "max_tokens": 4_000,
            }
        )
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=2_000, requested_output_tokens=None),
    )

    selected = await router.select_deployment("context-route", context)

    assert selected is not None
    assert selected.deployment_id == "dep-large"


@pytest.mark.asyncio
async def test_context_demand_change_invalidates_request_plan_cache():
    router, _ = _context_router(
        context={"mode": "eligible-only", "safety_margin_tokens": 0},
        small_model_info={"max_tokens": 8_000},
        large_model_info={"max_tokens": 32_000},
    )
    context: dict[str, object] = {}
    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=1_000, requested_output_tokens=1_000),
    )
    first = await router.select_deployment("context-route", context)

    set_request_token_demand(
        context,
        RequestTokenDemand(input_tokens=10_000, requested_output_tokens=1_000),
    )
    second = await router.select_deployment("context-route", context)

    assert first is not None and first.deployment_id == "dep-small"
    assert second is not None and second.deployment_id == "dep-large"


def test_embedding_context_demand_uses_largest_string_input():
    short_input = "short input"
    long_input = "long input " * 100

    demand = estimate_embedding_context_input_tokens([short_input, long_input])

    assert demand == estimate_embedding_context_input_tokens(long_input)
    assert demand > estimate_embedding_context_input_tokens(short_input)


@pytest.mark.parametrize("value", [1.9, -0.9, True, "1.9"])
def test_runtime_context_policy_rejects_non_integer_token_settings(value):
    assert parse_context_routing_policy({"default_output_tokens": value}) is None


@pytest.mark.parametrize("field_name", ["mode", "unknown_capacity"])
@pytest.mark.parametrize("value", [False, 0, "", None])
def test_runtime_context_policy_rejects_supplied_falsy_choices(field_name, value):
    assert parse_context_routing_policy({field_name: value}) is None


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ([1, 2, 3, 4], 4),
        ([[1], [1, 2, 3], [1, 2]], 3),
    ],
)
def test_embedding_context_demand_counts_pretokenized_logical_inputs(
    input_value: list[int] | list[list[int]], expected: int
):
    assert estimate_embedding_context_input_tokens(input_value) == expected


@pytest.mark.asyncio
async def test_combined_request_context_replans_for_largest_microbatch_item():
    router, _ = _context_router(
        context={"mode": "eligible-only", "safety_margin_tokens": 0},
        small_model_info={"max_tokens": 8_000},
        large_model_info={"max_tokens": 32_000},
    )
    short_context: dict[str, object] = {}
    set_request_token_demand(
        short_context,
        RequestTokenDemand(input_tokens=1_000, requested_output_tokens=1_000),
    )
    first = await router.select_deployment("context-route", short_context)
    assert first is not None and first.deployment_id == "dep-small"

    long_context: dict[str, object] = {}
    set_request_token_demand(
        long_context,
        RequestTokenDemand(input_tokens=10_000, requested_output_tokens=1_000),
    )
    combined_context = build_combined_request_context([short_context, long_context])

    selected = await router.select_deployment("context-route", combined_context)

    assert get_request_token_demand(combined_context) == RequestTokenDemand(
        input_tokens=10_000,
        requested_output_tokens=1_000,
    )
    assert selected is not None and selected.deployment_id == "dep-large"
