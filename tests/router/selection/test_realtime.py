import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from src.billing.operation_reservation import ComponentState, ReservedOperation
from src.billing.operation_reservation import BillingOperationUnavailable
from src.router.selection.runtime import SelectorExecutionFactory
from tests.router.selection.provider_fixtures import registry, response_body
from tests.test_routing_cache_identity import _enable_cache, _publish

pytestmark = pytest.mark.app


def configure(test_app, client, *, independent=False):
    info = {
        "mode": "chat",
        "max_tokens": 32768,
        "rpm_limit": 100,
        "tpm_limit": 1_000_000,
        "input_cost_per_token": 0.000001,
        "output_cost_per_token": 0.000002,
        "chat_capabilities": {
            "tools": True,
            "json_object": True,
            "json_schema": True,
            "streaming": True,
        },
    }
    test_app.state.model_registry = {
        "backing": [
            {
                "deployment_id": key,
                "model_info": dict(info),
                "deltallm_params": {
                    "model": f"openai/{key}",
                    "api_key": "provider-private",
                    "api_base": "https://mock.test/v1",
                },
            }
            for key in ("classifier", "quality")
        ]
    }
    policy = {
        "key": "gpt-4o-mini",
        "mode": "chat",
        "strategy": "priority-based-routing",
        "context": {"mode": "eligible-only", "unknown_capacity": "exclude"},
        "selector": {
            "kind": "llm-tier",
            "classifier_deployment_id": "classifier",
            "lanes": [
                {"id": "economy", "rank": 0, "description": "Routine"},
                {"id": "quality", "rank": 1, "description": "Complex"},
            ],
        },
        "members": [
            {"deployment_id": "classifier", "lane": "economy"},
            {"deployment_id": "quality", "lane": "quality"},
        ],
    }
    if independent:
        from copy import deepcopy

        economy = deepcopy(test_app.state.model_registry["backing"][0])
        economy["deployment_id"] = "economy"
        economy["deltallm_params"]["model"] = "openai/economy"
        test_app.state.model_registry["backing"].append(economy)
        # Classifier input is bounded text; it need not support answer tools/streams.
        classifier_info = test_app.state.model_registry["backing"][0]["model_info"]
        classifier_info["chat_capabilities"] = {}
        classifier_info["max_tokens"] = 8192
        policy["members"][0]["deployment_id"] = "economy"
    billing = AsyncMock()
    billing.reserve.side_effect = lambda op, **kwargs: ReservedOperation(
        operation=op,
        selector_state=ComponentState.RESERVED,
        answer_state=ComponentState.UNATTEMPTED,
    )
    test_app.state.http_client = client
    test_app.state.provider_error_mapper_registry = registry(client)
    test_app.state.selector_execution_factory = SelectorExecutionFactory(
        client=client,
        adapters=registry(client),
        billing=billing,
        default_openai_base_url="https://mock.test/v1",
        accounting_ready=lambda: True,
    )
    _enable_cache(test_app)
    _publish(test_app, [policy])
    return billing, policy


async def test_independent_selector_is_internal_dependency_not_a_direct_access_grant(
    client, test_app
):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        text = '{"lane":"economy"}' if len(calls) == 1 else "answer"
        return httpx.Response(200, json=response_body(text=text))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, policy = configure(test_app, upstream, independent=True)
        test_app.state.model_registry["private-tiny"] = [
            test_app.state.model_registry["backing"].pop(0)
        ]
        _publish(test_app, [policy])
        headers = {"Authorization": "Bearer sk-test"}
        body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}
        result = await client.post("/v1/chat/completions", json=body, headers=headers)
        assert result.status_code == 200, result.text
        assert [call["model"] for call in calls] == ["classifier", "economy"]
        billing.accept_selector.assert_awaited_once()
        denied = await client.post(
            "/v1/chat/completions", json={**body, "model": "private-tiny"}, headers=headers
        )
        assert denied.status_code == 403, denied.text
        assert len(calls) == 2


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/responses"])
@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize(
    "lane,selected", [("economy", "classifier"), ("quality", "quality"), ("unknown", "quality")]
)
async def test_realtime_selector_is_billed_once_and_cached_public_usage_is_answer_only(
    client, test_app, endpoint, lane, selected, independent
):
    calls = []

    def handle(request):
        data = json.loads(request.content)
        calls.append(data)
        text = f'{{"lane":"{lane}"}}' if len(calls) == 1 else "answer"
        return httpx.Response(200, json=response_body(text=text))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, _ = configure(test_app, upstream, independent=independent)
        if independent and selected == "classifier":
            selected = "economy"
        body = {
            "model": "gpt-4o-mini",
            **(
                {"input": "hello"}
                if endpoint.endswith("responses")
                else {"messages": [{"role": "user", "content": "hello"}]}
            ),
        }
        headers = {"Authorization": "Bearer sk-test"}
        result = await client.post(endpoint, json=body, headers=headers)
        assert result.status_code == 200, result.text
        assert len(calls) == 2
        assert calls[0]["model"] == "classifier" and calls[0]["max_tokens"] == 64
        assert calls[1]["model"] == selected
        assert result.json()["usage"]["total_tokens"] == 18
        billing.reserve.assert_awaited_once()
        billing.dispatch.assert_awaited_once()
        billing.accept_selector.assert_awaited_once()
        charge = billing.accept_selector.call_args.args[1]
        assert charge.customer_charge == charge.provider_cost
        assert charge.attribution.api_key != "sk-test"
        assert all(
            "selector" not in key.lower() and "lane" not in key.lower() for key in result.headers
        )
        cached = await client.post(endpoint, json=body, headers=headers)
        assert cached.status_code == 200 and cached.headers["x-deltallm-cache-hit"] == "true", (
            cached.text
        )
        assert len(calls) == 2
        billing.reserve.assert_awaited_once()


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/responses"])
@pytest.mark.parametrize("independent", [False, True])
async def test_stream_is_opened_only_after_durable_selector_receipt(
    client, test_app, endpoint, independent
):
    calls = []
    billing = None

    def handle(request):
        data = json.loads(request.content)
        calls.append(data)
        if not data.get("stream"):
            assert len(calls) == 1
            return httpx.Response(200, json=response_body(text='{"lane":"quality"}'))
        billing.accept_selector.assert_awaited_once()
        assert data["model"] == "quality"
        frames = [
            {
                "id": "stream-1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "quality",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "answer"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "stream-1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "quality",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
            },
        ]
        return httpx.Response(
            200,
            text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, _ = configure(test_app, upstream, independent=independent)
        body = {
            "model": "gpt-4o-mini",
            "stream": True,
            **(
                {"input": "hello"}
                if endpoint.endswith("responses")
                else {"messages": [{"role": "user", "content": "hello"}]}
            ),
        }
        response = await client.post(
            endpoint, json=body, headers={"Authorization": "Bearer sk-test"}
        )
        assert response.status_code == 200, response.text
        assert "answer" in response.text, response.text
        assert len(calls) == 2
        assert "model_router_selector" not in response.text and "minimum_rank" not in response.text


@pytest.mark.parametrize(
    "phase,provider_calls", [("reserve", 0), ("dispatch", 0), ("accept_selector", 1)]
)
async def test_billing_failure_never_defaults_or_dispatches_an_answer(
    client, test_app, phase, provider_calls
):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, _ = configure(test_app, upstream)
        getattr(billing, phase).side_effect = BillingOperationUnavailable()
        result = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
            headers={"Authorization": "Bearer sk-test"},
        )
        assert result.status_code == 503, result.text
        assert len(calls) == provider_calls
        generation = test_app.state.routing_runtime_generation_store.require_snapshot()
        cooldowns = await generation.router.state.get_cooldown_batch(
            [deployment.health_ref for deployment in generation.deployment_registry["gpt-4o-mini"]]
        )
        assert not any(cooldowns.values())


@pytest.mark.parametrize("stream", [False, True])
async def test_answer_failure_keeps_selector_charge_and_is_not_a_successful_stream(
    client, test_app, stream
):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json=response_body(text='{"lane":"quality"}'))
        return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, _ = configure(test_app, upstream)
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "stream": stream,
                "messages": [{"role": "user", "content": "hello"}],
            },
            headers={"Authorization": "Bearer sk-test"},
        )
        assert response.status_code == 503, response.text
        assert len(calls) == 2
        billing.accept_selector.assert_awaited_once()
        assert billing.accept_selector.call_args.args[1].customer_charge > 0


async def test_parent_deadline_cancels_selector_and_leaves_no_answer_or_reported_receipt(
    client, test_app
):
    calls, cancelled = [], asyncio.Event()

    async def handle(request):
        calls.append(json.loads(request.content))
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing, policy = configure(test_app, upstream)
        policy["timeouts"] = {"global_ms": 100}
        _publish(test_app, [policy])
        result = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
            headers={"Authorization": "Bearer sk-test"},
        )
        assert result.status_code == 408, result.text
        assert len(calls) == 1 and cancelled.is_set()
        billing.dispatch.assert_awaited_once()
        billing.accept_selector.assert_not_awaited()
