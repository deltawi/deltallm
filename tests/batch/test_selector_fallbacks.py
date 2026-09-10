from copy import deepcopy
import json

import httpx
import pytest
from src.callbacks import CallbackManager

from tests.batch import selector_fixtures
from tests.batch.selector_fixtures import selection_calls
from tests.router.selection.test_realtime_fallbacks import configure_fallback
from tests.test_routing_cache_identity import _publish

pytestmark = pytest.mark.app
selected_batch = selector_fixtures.selected_batch


@pytest.mark.parametrize(
    "field,code",
    [
        ("fallbacks", None),
        ("context_window_fallbacks", "context_length_exceeded"),
        ("content_policy_fallbacks", "content_filter"),
    ],
)
async def test_batch_first_selector_is_lazy_on_each_fallback_path(selected_batch, field, code):
    h = selected_batch
    billing = await configure_fallback(h.app, h.app.state.http_client, fallback_field=field)
    provider = h.provider

    async def fail_plain(request):
        data = json.loads(request.content)
        if data["model"] == "plain":
            h.calls.append(data)
            return httpx.Response(
                400 if code else 503, json={"error": {"message": code or "failed", "code": code}}
            )
        return await provider(request)

    h.provider = fail_plain
    item = h.item(content="complex")
    await h.worker._process_item(h.job, item)
    assert [call["model"] for call in h.calls] == ["plain", "classifier", "quality"]
    assert item.selector_checkpoint["model_group"] == "selected"
    assert len(h.repository.completed_calls) == 1
    billing.reserve.assert_awaited_once()


async def test_unreached_selector_fallback_does_no_classification_or_checkpoint(selected_batch):
    h = selected_batch
    billing = await configure_fallback(h.app, h.app.state.http_client)
    await h.worker._process_items(h.job, [h.item(1), h.item(2)])
    assert [call["model"] for call in h.calls] == ["plain", "plain"]
    assert not h.checkpoints.writes
    billing.reserve.assert_not_awaited()


async def test_fallback_authorization_cannot_be_bypassed_by_classifier(selected_batch):
    h = selected_batch
    billing = await configure_fallback(h.app, h.app.state.http_client, authorize=False)
    provider = h.provider

    async def fail_plain(request):
        if json.loads(request.content)["model"] == "plain":
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return await provider(request)

    h.provider = fail_plain
    await h.worker._process_item(h.job, h.item())
    assert not selection_calls(h) and not h.checkpoints.writes
    assert not h.repository.completed_calls
    billing.reserve.assert_not_awaited()


async def test_later_batch_selector_group_reuses_rank_without_reclassification(selected_batch):
    h = selected_batch
    h.selector_reply = '{"lane":"quality"}'
    billing = await configure_fallback(h.app, h.app.state.http_client, second=True)
    provider = h.provider

    async def fail_first_groups(request):
        data = json.loads(request.content)
        if data["model"] in {"plain", "quality"}:
            h.calls.append(data)
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return await provider(request)

    h.provider = fail_first_groups
    await h.worker._process_item(h.job, h.item())
    assert [call["model"] for call in h.calls] == ["plain", "classifier", "quality", "quality-2"]
    assert len(h.checkpoints.writes) == 2
    billing.reserve.assert_awaited_once()


async def test_local_context_fallback_selects_without_dispatching_rejected_primary(selected_batch):
    h = selected_batch
    await configure_fallback(
        h.app, h.app.state.http_client, fallback_field="context_window_fallbacks"
    )
    runtime = h.app.state.routing_runtime_generation_store.require_snapshot()
    groups = deepcopy(list(runtime.route_groups))
    groups[0]["context"] = {"mode": "eligible-only", "unknown_capacity": "exclude"}
    h.app.state.model_registry["backing"][-1]["model_info"]["max_tokens"] = 64
    _publish(h.app, groups, failover_config=runtime.failover_config)
    await h.worker._process_item(h.job, h.item(content="complex"))
    assert [call["model"] for call in h.calls] == ["classifier", "quality"]


async def test_classifier_uses_final_hook_mutation_without_private_metadata(selected_batch):
    h = selected_batch

    async def transform(**kwargs):
        payload = deepcopy(kwargs["data"])
        payload["messages"] = [{"role": "user", "content": "complex after hook"}]
        payload["metadata"] = {"private": "never-send-this-value"}
        return payload

    h.app.state.callback_manager = CallbackManager()
    h.app.state.callback_manager.execute_pre_call_hooks = transform
    await h.worker._process_item(h.job, h.item())
    (selector,) = selection_calls(h)
    assert "complex after hook" in json.dumps(selector)
    assert "never-send-this-value" not in json.dumps(selector)
    assert h.calls[-1]["model"] == "quality"


async def test_hard_capability_rejection_does_not_start_a_paid_selector(selected_batch):
    h = selected_batch
    for member in h.app.state.model_registry["backing"]:
        member["model_info"]["chat_capabilities"]["tools"] = False
    _publish(h.app, [h.policy])
    await h.worker._process_item(
        h.job,
        h.item(
            tools=[
                {
                    "type": "function",
                    "function": {"name": "extract", "parameters": {"type": "object"}},
                }
            ]
        ),
    )
    assert not h.calls and not h.checkpoints.writes
    h.billing.reserve.assert_not_awaited()
