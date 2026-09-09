from collections import Counter
import random
from unittest.mock import AsyncMock

import pytest

from src.router import RoutingStrategy
from src.router.candidates import invalidate_candidate_plan_cache
from src.router.selection.request_context import set_request_selector_state
from src.router.selection.service import SelectorService
from tests.router.selection.test_planning import decide, group, ids, plan, routing
from tests.router.selection.test_service import hop, select, state

STATE_READS = (
    "get_health_batch",
    "get_cooldown_batch",
    "get_active_requests_batch",
    "get_usage_batch",
    "get_latency_windows_batch",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", list(RoutingStrategy))
async def test_lanes_reuse_one_batched_strategy_snapshot(
    selector_policy, policy_identity, strategy, monkeypatch
):
    router, backend = routing([group(selector_policy, strategy=strategy.value)])
    reads = {}
    for name in STATE_READS:
        reads[name] = AsyncMock(wraps=getattr(backend, name))
        monkeypatch.setattr(backend, name, reads[name])
    implementation = router._strategies[strategy]
    query = implementation.state_query()
    order = AsyncMock(wraps=implementation.order)
    monkeypatch.setattr(implementation, "order", order)
    context = {}
    _, provider = await decide(context, selector_policy, policy_identity)
    result = await plan(router, context)

    assert Counter(ids(result.deployments)) == Counter(
        ["classifier-concrete", "economy-b", "quality-a", "quality-b"]
    )
    assert set(ids(result.deployments[:2])) == {"classifier-concrete", "economy-b"}
    assert order.await_count == 2
    assert order.call_args_list[0].args[2] is order.call_args_list[1].args[2]
    assert {name: call.await_count for name, call in reads.items()} == {
        "get_health_batch": 1,
        "get_cooldown_batch": 1,
        "get_active_requests_batch": int(query.active_requests),
        "get_usage_batch": int(query.usage),
        "get_latency_windows_batch": int(query.latency_window_ms is not None),
    }
    assert await plan(router, context) is result
    assert order.await_count == 2 and reads["get_health_batch"].await_count == 1
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", list(RoutingStrategy))
async def test_selector_free_order_and_dependency_counts_are_unchanged(strategy, monkeypatch):
    router, backend = routing([group(None, strategy=strategy.value)])
    reads = {}
    for name in STATE_READS:
        reads[name] = AsyncMock(wraps=getattr(backend, name))
        monkeypatch.setattr(backend, name, reads[name])
    implementation = router._strategies[strategy]
    original = implementation.order
    order = AsyncMock(wraps=original)
    monkeypatch.setattr(implementation, "order", order)
    context = {}
    previous_random_state = random.getstate()
    random.seed(304)
    try:
        result = await plan(router, context)
        reads_before = {name: call.await_count for name, call in reads.items()}
        assert reads_before["get_health_batch"] == reads_before["get_cooldown_batch"] == 1
        assert order.await_count == 1
        assert result.lanes == () and result.minimum_rank is None
        assert set(context) == {"_deltallm_candidate_plans"}
        random.seed(304)
        legacy_order = await original(*order.call_args.args)
        assert ids(result.deployments) == ids(legacy_order)
        assert await plan(router, context) is result
        assert reads_before == {name: call.await_count for name, call in reads.items()}
    finally:
        random.setstate(previous_random_state)


@pytest.mark.asyncio
async def test_planning_unreached_selector_fallback_never_invokes_a_provider(
    selector_policy, policy_identity, monkeypatch
):
    router, backend = routing([group(None, key="primary"), group(selector_policy)])
    health = AsyncMock(wraps=backend.get_health_batch)
    monkeypatch.setattr(backend, "get_health_batch", health)
    provider, operation, context = hop(), state(), {}
    set_request_selector_state(context, operation)
    results = await router.plan_deployments(["primary", "selected"], context)
    assert results["primary"].deployments
    assert not results["selected"].deployments
    assert results["selected"].rejection_reason == "selector_decision_required"
    assert operation.decision_for_planning() is None
    provider.invoke.assert_not_awaited()
    health.assert_awaited_once()

    await select(SelectorService(provider), operation, selector_policy, policy_identity)
    changed = await router.plan_deployments(["primary", "selected"], context)
    assert changed["primary"] is results["primary"]
    assert changed["selected"] is not results["selected"]
    assert changed["selected"].minimum_rank == 0
    assert health.await_count == 2
    invalidate_candidate_plan_cache(context)
    await router.plan_deployments(["primary", "selected"], context)
    provider.invoke.assert_awaited_once()
