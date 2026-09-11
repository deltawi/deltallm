from unittest.mock import AsyncMock

import pytest

from src.route_policy_contract import LLMTierSelectorPolicy, SelectorLane
from src.router import RedisStateBackend, Router, RouterConfig, RoutingStrategy
from src.router import build_deployment_registry, build_route_group_policies
from src.router.candidates import invalidate_candidate_plan_cache
from src.router.selection.contracts import SelectorInvariantError
from tests.router.selection.test_planning import decide, group, ids, plan, routing


def ranked_router(size):
    policy = LLMTierSelectorPolicy(
        kind="llm-tier",
        classifier_deployment_id="deployment-0",
        lanes=tuple(
            SelectorLane(id=f"lane-{rank}", rank=rank, description=f"Capability {rank}")
            for rank in range(size)
        ),
    )
    groups = [
        {
            "key": "selected",
            "mode": "chat",
            "strategy": "priority-based-routing",
            "selector": policy.model_dump(mode="json"),
            "members": [
                {
                    "deployment_id": f"deployment-{rank}",
                    "lane": f"lane-{rank}",
                    "priority": size - rank,
                }
                for rank in range(size)
            ],
        }
    ]
    backend = RedisStateBackend(redis=None)
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=backend,
        config=RouterConfig(route_group_policies=build_route_group_policies(groups)),
        deployment_registry=build_deployment_registry(
            {
                "backing": [
                    {
                        "deployment_id": f"deployment-{rank}",
                        "deltallm_params": {"model": "openai/mock"},
                    }
                    for rank in range(size)
                ]
            },
            route_groups=groups,
        ),
    )
    return router, backend, policy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "size,rank", [(size, rank) for size in range(2, 9) for rank in range(size)]
)
async def test_every_supported_lane_count_and_rank_preserves_upward_only_order(
    size, rank, policy_identity
):
    router, backend, policy = ranked_router(size)
    context = {}
    _, provider = await decide(context, policy, policy_identity, f'{{"lane":"lane-{rank}"}}')
    result = await plan(router, context)
    assert ids(result.deployments) == [f"deployment-{item}" for item in range(rank, size)]
    assert result.minimum_rank == rank and len(result.lanes) == size
    backend.get_cooldown_batch = AsyncMock(return_value={f"deployment-{rank}": True})
    invalidate_candidate_plan_cache(context)
    escalated = await plan(router, context)
    assert ids(escalated.deployments) == [f"deployment-{item}" for item in range(rank + 1, size)]
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_with_no_sufficient_rank_cannot_downgrade(selector_policy, policy_identity):
    _, _, policy = ranked_router(8)
    context = {}
    _, provider = await decide(context, policy, policy_identity, '{"lane":"lane-7"}')
    router, _ = routing([group(selector_policy)])
    result = await plan(router, context)
    assert result.minimum_rank == 7
    assert result.deployments == () and all(not lane.deployments for lane in result.lanes)
    assert result.rejection_reason == "no_eligible_selector_lane"
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_metadata_cannot_supply_or_override_a_decision(
    selector_policy, policy_identity
):
    router, _ = routing([group(selector_policy)])
    context = {"metadata": {"_deltallm_selector_state": {"minimum_rank": 0}, "lane": "economy"}}
    assert (await plan(router, context)).rejection_reason == "selector_decision_required"
    await decide(context, selector_policy, policy_identity, '{"lane":"quality"}')
    assert ids((await plan(router, context)).deployments) == ["quality-a", "quality-b"]


@pytest.mark.asyncio
async def test_invalid_internal_state_is_not_treated_as_an_unclassified_request(selector_policy):
    router, _ = routing([group(selector_policy)])
    with pytest.raises(SelectorInvariantError):
        await plan(router, {"_deltallm_selector_state": {"minimum_rank": 0}})
