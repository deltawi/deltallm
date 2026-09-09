import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from tests.router.selection.provider_fixtures import response_body
from tests.router.selection.test_realtime import configure
from tests.test_routing_cache_identity import _publish

pytestmark = pytest.mark.app
HEADERS = {"Authorization": "Bearer sk-test"}
BODY = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}


async def test_equivalent_capability_reload_keeps_cache_identity_and_changed_capability_invalidates(
    test_app,
):
    from src.chat_capabilities import ChatRoutingCapabilities

    async with httpx.AsyncClient() as upstream:
        _, policy = configure(test_app, upstream)
        before = test_app.state.routing_runtime_generation_store.require_snapshot()
        for entry in test_app.state.model_registry["backing"]:
            entry["model_info"]["chat_capabilities"] = ChatRoutingCapabilities.model_validate(
                entry["model_info"]["chat_capabilities"]
            ).model_dump()
        same = _publish(test_app, [deepcopy(policy)])
        assert same.generation_id != before.generation_id
        assert same.routing_fingerprints == before.routing_fingerprints
        test_app.state.model_registry["backing"][1]["model_info"]["chat_capabilities"]["image"] = (
            True
        )
        changed = _publish(test_app, [deepcopy(policy)])
        assert (
            changed.routing_fingerprints["gpt-4o-mini"]
            != before.routing_fingerprints["gpt-4o-mini"]
        )


@pytest.mark.parametrize("feature", ["image", "audio", "file", "multiple_choices"])
async def test_unsupported_hard_capability_never_pays_for_classification(client, test_app, feature):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=response_body(text='{"lane":"economy"}'))

    body = deepcopy(BODY)
    if feature == "multiple_choices":
        body["n"] = 2
    else:
        block = {
            "image": {"type": "image_url", "image_url": {"url": "https://mock.test/image"}},
            "audio": {"type": "input_audio", "input_audio": {"data": "private", "format": "wav"}},
            "file": {"type": "file", "file": {"file_id": "private"}},
        }[feature]
        body["messages"][0]["content"] = [block]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, _ = configure(test_app, upstream)
        response = await client.post("/v1/chat/completions", headers=HEADERS, json=body)
        assert response.status_code in (400, 503), response.text
        assert not calls
        billing.reserve.assert_not_awaited()


async def test_model_reload_during_classification_does_not_change_pinned_answer(client, test_app):
    calls = []
    policy = None

    def handle(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if len(calls) == 1:
            test_app.state.model_registry = deepcopy(test_app.state.model_registry)
            test_app.state.model_registry["backing"][1]["deltallm_params"]["model"] = (
                "openai/quality-new"
            )
            _publish(test_app, [deepcopy(policy)])
        return httpx.Response(
            200,
            json=response_body(text='{"lane":"quality"}' if model == "classifier" else "answer"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, policy = configure(test_app, upstream)
        response = await client.post("/v1/chat/completions", headers=HEADERS, json=BODY)
        assert response.status_code == 200, response.text
        assert calls == ["classifier", "quality"]
        response = await client.post("/v1/chat/completions", headers=HEADERS, json=BODY)
        assert response.status_code == 200, response.text
        assert calls == ["classifier", "quality", "classifier", "quality-new"]
        assert billing.reserve.await_count == 2


async def test_mcp_continuation_reuses_selector_and_original_deadline(client, test_app):
    from tests.mcp.test_chat_execution import (
        _RecordingAuditService,
        _RecordingGateway,
        _request_payload,
        _tool_call_response,
    )

    calls = []

    def handle(request):
        data = json.loads(request.content)
        calls.append(data)
        if data["model"] == "classifier":
            return httpx.Response(200, json=response_body(text='{"lane":"quality"}'))
        if any(message["role"] == "tool" for message in data["messages"]):
            return httpx.Response(200, json=response_body(text="final answer"))
        return httpx.Response(200, json=_tool_call_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, _ = configure(test_app, upstream)
        gateway = _RecordingGateway()
        test_app.state.mcp_gateway_service = gateway
        test_app.state.audit_service = _RecordingAuditService()
        manager = (
            test_app.state.routing_runtime_generation_store.require_snapshot().failover_manager
        )
        original = manager.execute_with_failover
        deadlines = []

        async def execute(*args, **kwargs):
            deadlines.append(kwargs["request_deadline"])
            return await original(*args, **kwargs)

        manager.execute_with_failover = execute
        response = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json=_request_payload().model_dump(exclude_none=True),
        )
        assert response.status_code == 200, response.text
        assert [call["model"] for call in calls] == ["classifier", "quality", "quality"]
        assert gateway.tool_calls == ["docs.search"]
        assert len(deadlines) == 2 and deadlines[0] is deadlines[1]
        billing.reserve.assert_awaited_once()
        billing.accept_selector.assert_awaited_once()


async def test_selector_timeout_defaults_but_retains_unknown_dispatched_usage(client, test_app):
    calls, cancelled = [], asyncio.Event()

    async def handle(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "classifier":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return httpx.Response(200, json=response_body(text="answer"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, policy = configure(test_app, upstream)
        policy["selector"]["timeout_ms"] = 100
        _publish(test_app, [policy])
        response = await client.post("/v1/chat/completions", headers=HEADERS, json=BODY)
        assert response.status_code == 200, response.text
        assert calls == ["classifier", "quality"] and cancelled.is_set()
        billing.dispatch.assert_awaited_once()
        billing.accept_selector.assert_not_awaited()
        billing.unattempted.assert_not_awaited()
