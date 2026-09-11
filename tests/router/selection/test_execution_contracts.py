import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat_capabilities import ChatRoutingCapabilities
from src.batch.selector_checkpoint import BatchSelectorUnavailable
from src.models.requests import ChatCompletionRequest
from src.router.context_policy import ContextRoutingPolicy
from src.router.failover import FallbackConfig
from src.router.selection.eligibility import (
    capability_allows,
    chat_requirements,
    set_selector_capability_eligibility,
)
from src.router.selection.planning import selector_context_policy
from src.router.selection.qualification import selector_price_snapshot
from src.router.selection.reachability import (
    selector_reachable_groups,
)
from src.routers.selector_edge import _while_connected, SelectorClientDisconnectedError


@pytest.mark.parametrize(
    "field", ["fallbacks", "context_window_fallbacks", "content_policy_fallbacks"]
)
def test_batch_isolates_direct_and_transitive_selector_targets_even_through_cycles(field):
    reachable = selector_reachable_groups(
        ["selected"],
        FallbackConfig(**{field: {"root": ["middle"], "middle": ["root", "selected"]}}),
    )
    assert reachable == {"root", "middle", "selected"}
    assert "ordinary" not in reachable


@pytest.mark.parametrize(
    "block,feature",
    [
        ({"type": "image_url", "image_url": {"url": "https://private.test/image"}}, "image"),
        ({"type": "input_audio", "input_audio": {"data": "private", "format": "wav"}}, "audio"),
        ({"type": "file", "file": {"file_id": "private"}}, "file"),
        ({"type": "unrecognized"}, "unknown"),
    ],
)
def test_hard_capabilities_inspect_content_outside_classifier_prompt_bounds(block, feature):
    payload = ChatCompletionRequest.model_validate(
        {
            "model": "group",
            "messages": [{"role": "user", "content": [block]}]
            + [{"role": "user", "content": "hello"}] * 100,
        }
    )
    assert chat_requirements(payload) == {feature}
    context = {}
    capabilities = {
        "basic": ChatRoutingCapabilities(),
        "capable": ChatRoutingCapabilities(**({feature: True} if feature != "unknown" else {})),
    }
    set_selector_capability_eligibility(context, payload, capabilities)
    assert not capability_allows(context, "basic")
    assert capability_allows(context, "capable") == (feature != "unknown")


def test_selector_fallback_intersects_inherited_and_own_context_limits():
    inherited = ContextRoutingPolicy(
        mode="smallest-sufficient", default_output_tokens=2048, safety_margin_tokens=0
    )
    own = ContextRoutingPolicy(
        unknown_capacity="exclude", default_output_tokens=1024, safety_margin_tokens=256
    )
    result = selector_context_policy(inherited, own)
    assert result == ContextRoutingPolicy(
        mode="smallest-sufficient",
        unknown_capacity="exclude",
        default_output_tokens=2048,
        safety_margin_tokens=256,
    )
    assert selector_context_policy(None, own) is own


def test_realtime_selector_price_ignores_batch_rates_but_not_unmodelled_dimensions():
    info = {
        "input_cost_per_token": "0.001",
        "output_cost_per_token": "0.002",
        "batch_input_cost_per_token": "0.0005",
        "batch_output_cost_per_token": "0.001",
    }
    assert selector_price_snapshot(info).input_cost_per_token == Decimal("0.001")
    with pytest.raises(ValueError, match="unsupported provider billing"):
        selector_price_snapshot({**info, "input_cost_per_audio_token": "0.01"})


@pytest.mark.parametrize("disconnect", [True, False])
async def test_connection_owner_cancels_and_joins_work(disconnect):
    started, closed = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def receive():
        await started.wait()
        if not disconnect:
            await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    request = SimpleNamespace(receive=receive)
    task = asyncio.create_task(_while_connected(request, work()))
    await asyncio.wait_for(started.wait(), timeout=1)
    if not disconnect:
        task.cancel()
    with pytest.raises(SelectorClientDisconnectedError if disconnect else asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert closed.is_set()


async def test_batch_worker_requires_selector_dependencies_before_planning_or_provider(monkeypatch):
    from tests.test_batch_worker import (
        _AllowAllCallableTargetGrantService,
        _build_chat_batch_worker,
        _build_chat_batch_item,
        _build_chat_batch_job,
    )
    from src.batch.worker_types import capture_batch_routing_runtime

    worker, _ = _build_chat_batch_worker(deployment_params={"provider": "vllm", "model": "gpt-oss"})
    worker.app.state.callable_target_grant_service = _AllowAllCallableTargetGrantService()
    runtime = capture_batch_routing_runtime(worker.app.state)
    from dataclasses import replace

    runtime = replace(runtime, selector_reachable_groups=frozenset({"gpt-oss"}))
    monkeypatch.setattr(
        "src.batch.chat_item_execution.capture_batch_routing_runtime", lambda _: runtime
    )
    planner = AsyncMock(side_effect=AssertionError("must not plan"))
    monkeypatch.setattr("src.batch.chat_item_execution.require_initial_deployment", planner)
    with pytest.raises(BatchSelectorUnavailable) as error:
        await worker._prepare_item_for_execution(
            _build_chat_batch_job(), _build_chat_batch_item("selector-1", "hello")
        )
    assert error.value.code == "batch_selector_checkpoint_unavailable"
    planner.assert_not_awaited()
