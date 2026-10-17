from copy import deepcopy
import json

import httpx
import pytest

from src.callbacks import CallbackManager, CustomLogger
from src.metrics.selector import decisions, terminal_lanes
from src.router.candidates import AttemptPermit, AttemptRejectionReason
from tests.router.selection.provider_fixtures import response_body
from tests.router.selection.test_realtime import configure
from tests.router.selection.test_realtime_lifecycle import BODY, HEADERS
from tests.test_routing_cache_identity import _publish

pytestmark = pytest.mark.app


def _sse(*chunks):
    lines = [f"data: {json.dumps(chunk)}" for chunk in chunks] + ["data: [DONE]"]
    return httpx.Response(
        200,
        content=("\n\n".join(lines) + "\n\n").encode(),
        headers={"content-type": "text/event-stream"},
    )


def _delta_chunk(delta, finish=None):
    return {
        "id": "chatcmpl-stream",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "quality",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/responses"])
@pytest.mark.parametrize("from_hook", [False, True])
async def test_streaming_mcp_continuation_reuses_selector_and_original_deadline(
    client, test_app, endpoint, from_hook
):
    from tests.mcp.test_chat_execution import _RecordingAuditService, _RecordingGateway

    tools = [{"type": "mcp", "server": "docs"}]
    calls = []

    class AddMCPTools(CustomLogger):
        async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
            return {**data, "tools": tools}

    def handle(request):
        data = json.loads(request.content)
        calls.append(data["model"])
        if data["model"] == "classifier":
            return httpx.Response(200, json=response_body(text='{"lane":"quality"}'))
        if any(message["role"] == "tool" for message in data["messages"]):
            return _sse(_delta_chunk({"content": "final answer"}), _delta_chunk({}, "stop"))
        call = {
            "index": 0,
            "id": "call_docs_search",
            "type": "function",
            "function": {"name": "docs.search", "arguments": '{"query":"delta"}'},
        }
        return _sse(_delta_chunk({"tool_calls": [call]}), _delta_chunk({}, "tool_calls"))

    body = (
        deepcopy(BODY)
        if endpoint.endswith("completions")
        else {"model": BODY["model"], "input": "hello"}
    )
    body["stream"] = True
    if from_hook:
        manager = CallbackManager()
        manager.register_callback(AddMCPTools())
        test_app.state.callback_manager = manager
    else:
        body["tools"] = tools
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, _ = configure(test_app, upstream)
        gateway = _RecordingGateway()
        test_app.state.mcp_gateway_service = gateway
        test_app.state.audit_service = _RecordingAuditService()
        failover = (
            test_app.state.routing_runtime_generation_store.require_snapshot().failover_manager
        )
        original = failover.execute_managed_with_failover
        deadlines = []

        async def execute(*args, **kwargs):
            deadlines.append(kwargs["request_deadline"])
            return await original(*args, **kwargs)

        failover.execute_managed_with_failover = execute
        response = await client.post(endpoint, headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        assert "final answer" in response.text
        assert calls == ["classifier", "quality", "quality"]
        assert gateway.tool_calls == ["docs.search"]
        assert len(deadlines) == 2 and deadlines[0] is deadlines[1]
        billing.reserve.assert_awaited_once()
        billing.accept_selector.assert_awaited_once()


@pytest.mark.parametrize("reason", ["tags", "capacity", "context"])
async def test_classifier_policy_or_capacity_denial_defaults_without_dispatch_or_charge(
    client, test_app, reason
):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json=response_body(text="answer"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, policy = configure(test_app, upstream)
        body = deepcopy(BODY)
        if reason == "tags":
            test_app.state.model_registry["backing"][1]["model_info"]["tags"] = ["eu"]
            body["metadata"] = {"tags": ["eu"]}
            _publish(test_app, [policy])
        elif reason == "context":
            test_app.state.model_registry["backing"][0]["model_info"]["max_tokens"] = 512
            _publish(test_app, [policy])
        else:
            runtime = test_app.state.routing_runtime_generation_store.require_snapshot()
            original = runtime.router.state.acquire_attempt

            async def acquire(ref, capacity, **kwargs):
                if ref.deployment_id == "classifier":
                    return AttemptPermit(
                        ref.deployment_id,
                        ref,
                        False,
                        "redis",
                        rejection_reason=AttemptRejectionReason.CAPACITY,
                    )
                return await original(ref, capacity, **kwargs)

            runtime.router.state.acquire_attempt = acquire
        response = await client.post("/v1/chat/completions", headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        assert calls == ["quality"]
        billing.dispatch.assert_not_awaited()
        billing.accept_selector.assert_not_awaited()
        billing.unattempted.assert_awaited_once()


async def test_terminal_selector_metrics_are_emitted_once_and_cache_hit_does_not_recount(
    client, test_app
):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(
            200, json=response_body(text='{"lane":"quality"}' if len(calls) == 1 else "answer")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configure(test_app, upstream)
        before = terminal_lanes.labels(rank="1")._value.get()
        decision_before = decisions.labels(cause="classified", rank="1")._value.get()
        for _ in range(2):
            response = await client.post("/v1/chat/completions", headers=HEADERS, json=BODY)
            assert response.status_code == 200, response.text
        assert terminal_lanes.labels(rank="1")._value.get() == before + 1
        assert decisions.labels(cause="classified", rank="1")._value.get() == decision_before + 1
        assert calls == ["classifier", "quality"]


@pytest.mark.parametrize("invalid", [{"tools": "true"}, {"unknown": True}, [], True])
async def test_admin_model_capability_contract_rejects_invalid_flags(test_app, invalid):
    from fastapi import HTTPException
    from src.api.admin.endpoints.models import _normalize_model_info_or_400

    with pytest.raises(HTTPException) as error:
        _normalize_model_info_or_400({"chat_capabilities": invalid})
    assert error.value.status_code == 400
