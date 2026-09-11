import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest

from src.models.errors import TimeoutError as RequestTimeoutError
from src.router import (
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.router.candidates import invalidate_candidate_plan_cache
from src.router.context_policy import RequestTokenDemand, set_request_token_demand
from src.router.selection.contracts import SelectorInvariantError, SelectorOperationAbortedError
from src.router.selection.request_context import set_request_selector_state
from src.router.selection.service import SelectorService
from tests.router.selection.test_service import hop, select, state


def group(policy, *, key="selected", strategy="priority-based-routing"):
    return {
        "key": key,
        "mode": "chat",
        "strategy": strategy,
        "selector": policy.model_dump(mode="json") if policy is not None else None,
        "members": [
            {"deployment_id": "classifier-concrete", "lane": "economy", "priority": 50},
            {"deployment_id": "economy-b", "lane": "economy", "priority": 10},
            {"deployment_id": "quality-a", "lane": "quality", "priority": 0},
            {"deployment_id": "quality-b", "lane": "quality", "priority": 100},
        ],
    }


def routing(groups, *, context_policy=None, pre_call_checks=False):
    entries = [
        {
            "deployment_id": name,
            "deltallm_params": {"model": "openai/mock"},
            "model_info": {"mode": "chat", "max_tokens": capacity, "rpm_limit": 10},
        }
        for name, capacity in (
            ("classifier-concrete", 8000),
            ("economy-b", 32000),
            ("quality-a", 128000),
            ("quality-b", 64000),
        )
    ]
    groups = deepcopy(groups)
    if context_policy is not None:
        for item in groups:
            item["context"] = context_policy
    backend = RedisStateBackend(redis=None)
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=backend,
        config=RouterConfig(
            route_group_policies=build_route_group_policies(groups),
            enable_pre_call_checks=pre_call_checks,
        ),
        deployment_registry=build_deployment_registry({"backing": entries}, route_groups=groups),
    )
    return router, backend


async def decide(context, policy, identity, text='{"lane":"economy"}'):
    operation, provider = state(), hop(text)
    set_request_selector_state(context, operation)
    await select(SelectorService(provider), operation, policy, identity)
    return operation, provider


def ids(deployments):
    return [deployment.deployment_id for deployment in deployments]


async def plan(router, context, key="selected"):
    return (await router.plan_deployments([key], context))[key]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,rank,expected",
    [
        ('{"lane":"economy"}', 0, ["economy-b", "classifier-concrete", "quality-a", "quality-b"]),
        ('{"lane":"quality"}', 1, ["quality-a", "quality-b"]),
        ('{"lane":"injected-deployment"}', 1, ["quality-a", "quality-b"]),
    ],
)
async def test_selected_lane_then_upward_only_with_strategy_inside_each_lane(
    selector_policy, policy_identity, text, rank, expected
):
    router, _ = routing([group(selector_policy)])
    context = {}
    operation, provider = await decide(context, selector_policy, policy_identity, text)
    result = await plan(router, context)
    assert ids(result.deployments) == expected
    assert result.minimum_rank == rank
    assert [(lane.lane, lane.rank) for lane in result.lanes] == [("economy", 0), ("quality", 1)]
    assert not any(lane.deployments for lane in result.lanes if lane.rank < rank)
    assert operation.decision_for_planning().minimum_rank == rank
    provider.invoke.assert_awaited_once()
    with pytest.raises(FrozenInstanceError):
        result.lanes[0].rank = 7
    with pytest.raises(FrozenInstanceError):
        result.minimum_rank = 7


@pytest.mark.asyncio
async def test_undecided_plan_is_not_executable_and_is_replaced_after_selection(
    selector_policy, policy_identity
):
    router, _ = routing([group(selector_policy)])
    context, operation = {}, state()
    set_request_selector_state(context, operation)
    pending = await plan(router, context)
    assert pending.deployments == () and pending.minimum_rank is None
    assert pending.rejection_reason == "selector_decision_required"
    assert sum(len(lane.deployments) for lane in pending.lanes) == 4
    assert await plan(router, context) is pending
    provider = hop('{"lane":"quality"}')
    await select(SelectorService(provider), operation, selector_policy, policy_identity)
    selected = await plan(router, context)
    assert selected is not pending and selected.minimum_rank == 1
    assert ids(selected.deployments) == ["quality-a", "quality-b"]
    assert await plan(router, context) is selected
    invalidate_candidate_plan_cache(context)
    assert ids((await plan(router, context)).deployments) == ["quality-a", "quality-b"]
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_changes_replan_without_reclassifying(selector_policy, policy_identity):
    router, _ = routing(
        [group(selector_policy)],
        context_policy={"mode": "smallest-sufficient", "safety_margin_tokens": 0},
    )
    context = {}
    _, provider = await decide(context, selector_policy, policy_identity)
    set_request_token_demand(context, RequestTokenDemand(1000, 64))
    first = await plan(router, context)
    assert ids(first.deployments) == ["classifier-concrete", "economy-b", "quality-b", "quality-a"]
    set_request_token_demand(context, RequestTokenDemand(40000, 64))
    second = await plan(router, context)
    assert second is not first and ids(second.deployments) == ["quality-b", "quality-a"]
    assert second.minimum_rank == 0 and second.lanes[0].deployments == ()
    assert first.lanes[0].deployments  # Existing immutable plan was not mutated.
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint", ["health", "cooldown", "capacity", "tags", "mode"])
async def test_hard_filters_cannot_be_bypassed_by_economy_selection(
    selector_policy, policy_identity, constraint
):
    router, backend = routing([group(selector_policy)], pre_call_checks=True)
    context = {"routing_mode": "chat"}
    _, provider = await decide(context, selector_policy, policy_identity)
    economy = {"classifier-concrete", "economy-b"}
    if constraint == "health":
        backend.get_health_batch = AsyncMock(
            return_value={name: {"healthy": "false"} for name in economy}
        )
    elif constraint == "cooldown":
        backend.get_cooldown_batch = AsyncMock(return_value={name: True for name in economy})
    elif constraint == "capacity":
        backend.get_usage_batch = AsyncMock(return_value={name: {"rpm": 10} for name in economy})
    else:
        for deployment in router.deployment_registry["selected"]:
            if constraint == "tags":
                deployment.tags = ["allowed"] if deployment.deployment_id not in economy else []
                context["metadata"] = {"tags": ["allowed"]}
            elif deployment.deployment_id in economy:
                deployment.model_info["mode"] = "embedding"
    result = await plan(router, context)
    assert ids(result.deployments) == ["quality-a", "quality-b"]
    assert result.filtered_count == 2 and result.lanes[0].deployments == ()
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_lower_lane_when_quality_is_unavailable(selector_policy, policy_identity):
    router, backend = routing([group(selector_policy)])
    backend.get_cooldown_batch = AsyncMock(return_value={"quality-a": True, "quality-b": True})
    context = {}
    await decide(context, selector_policy, policy_identity, '{"lane":"quality"}')
    result = await plan(router, context)
    assert result.deployments == () and result.minimum_rank == 1
    assert result.rejection_reason == "no_eligible_selector_lane"
    assert result.filtered_count == 2


@pytest.mark.asyncio
async def test_fallback_plans_reuse_rank_not_lane_name_and_leave_plain_groups_unchanged(
    selector_policy, policy_identity
):
    primary = group(selector_policy)
    fallback = group(selector_policy, key="fallback")
    fallback["selector"]["lanes"][0]["id"] = "basic"
    fallback["selector"]["lanes"][1]["id"] = "advanced"
    fallback["selector"]["default_lane"] = "advanced"
    for member in fallback["members"]:
        member["lane"] = "basic" if member["lane"] == "economy" else "advanced"
    router, _ = routing([primary, fallback, group(None, key="plain")])
    context = {}
    operation, provider = await decide(
        context, selector_policy, policy_identity, '{"lane":"quality"}'
    )
    results = await router.plan_deployments(["selected", "fallback", "plain"], context)
    assert ids(results["fallback"].deployments) == ["quality-a", "quality-b"]
    assert results["fallback"].lanes[1].lane == "advanced"
    assert results["fallback"].minimum_rank == results["selected"].minimum_rank == 1
    assert operation.decision_for_planning().lane == "quality"
    assert ids(results["plain"].deployments) == [
        "quality-a",
        "economy-b",
        "classifier-concrete",
        "quality-b",
    ]
    assert results["plain"].lanes == () and results["plain"].minimum_rank is None
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["cancelled", "failed", "expired"])
async def test_terminal_operation_never_returns_cached_candidates(
    selector_policy, policy_identity, terminal
):
    router, _ = routing([group(selector_policy)])
    context, operation = {}, state()
    set_request_selector_state(context, operation)
    await plan(router, context)
    if terminal == "expired":
        # Deterministically expire the owned deadline, without sleeping.
        await select(SelectorService(hop()), operation, selector_policy, policy_identity)
        await plan(router, context)
        object.__setattr__(operation.deadline, "expires_at", 0.0)
        expected = RequestTimeoutError
    else:
        expected = (
            asyncio.CancelledError if terminal == "cancelled" else SelectorOperationAbortedError
        )

        async def fail():
            if terminal == "cancelled":
                raise asyncio.CancelledError()
            raise RuntimeError("injected execution failure")

        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await operation.select_once(fail)
    with pytest.raises(expected):
        await plan(router, context)


@pytest.mark.asyncio
async def test_replacing_operation_owner_is_rejected(selector_policy):
    router, _ = routing([group(selector_policy)])
    context, operation = {}, state()
    set_request_selector_state(context, operation)
    pending = await plan(router, context)
    set_request_selector_state(context, operation)
    assert await plan(router, context) is pending
    with pytest.raises(SelectorInvariantError):
        set_request_selector_state(context, state())


def test_projection_is_immutable_and_detached_from_authored_document(selector_policy):
    authored = group(selector_policy)
    policy = build_route_group_policies([authored])["selected"]
    authored["members"][0]["lane"] = "quality"
    authored["selector"]["lanes"][0]["description"] = "mutated"
    assert policy.selector.members[0].lane == "economy"
    assert policy.selector.policy.lanes[0].description == "Routine work"
    with pytest.raises(FrozenInstanceError):
        policy.selector = None


@pytest.mark.parametrize("kind", ["unassigned", "duplicate", "empty_lane", "wrong_mode"])
def test_current_invalid_lane_projection_fails_closed(selector_policy, kind):
    authored = group(selector_policy)
    if kind == "unassigned":
        authored["members"][0].pop("lane")
    elif kind == "duplicate":
        authored["members"].append(authored["members"][0])
    elif kind == "empty_lane":
        for member in authored["members"]:
            member["lane"] = "economy"
    else:
        authored["mode"] = "embedding"
    with pytest.raises(ValueError):
        build_route_group_policies([authored])
    authored["policy_semantics_version"] = 1
    assert build_route_group_policies([authored])["selected"].selector is None
