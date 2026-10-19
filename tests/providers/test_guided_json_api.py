from __future__ import annotations

from dataclasses import replace
import json

import httpx
import pytest

from tests.providers.test_guided_json import SCHEMA, request_data
from tests.test_cache import _enable_cache


@pytest.fixture
def vllm_app(test_app):
    store = test_app.state.router.deployment_registry
    deployment = replace(
        store["gpt-4o-mini"][0],
        deltallm_params={
            "provider": "vllm",
            "model": "private-model",
            "api_base": "https://vllm.test/v1",
            "api_key": "test-key",
        },
    )
    store.replace({**store.snapshot(), "gpt-4o-mini": [deployment]})
    return test_app


def completion(content='{"invoice_number":"INV-1","total":1}'):
    return {
        "id": "chatcmpl-guided",
        "object": "chat.completion",
        "created": 1,
        "model": "private-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def upstream_response(*, stream: bool):
    if not stream:
        return httpx.Response(200, json=completion())
    events = [
        {
            "id": "chatcmpl-guided",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "private-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": completion()["choices"][0]["message"]["content"],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-guided",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "private-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": completion()["usage"],
        },
    ]
    return httpx.Response(
        200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("as_string", [False, True])
async def test_vllm_wire_body_preserves_guided_schema(client, vllm_app, stream, as_string):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        assert str(request.url) == "https://vllm.test/v1/chat/completions"
        return upstream_response(stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        vllm_app.state.http_client = upstream
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {vllm_app.state._test_key}"},
            json={
                **request_data(guided_json=json.dumps(SCHEMA) if as_string else SCHEMA),
                "model": "gpt-4o-mini",
                "stream": stream,
            },
        )
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    assert "guided_json" not in captured[0]
    assert captured[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "guided_json", "schema": SCHEMA},
    }
    assert captured[0]["model"] == "private-model"
    if stream:
        assert "[DONE]" in response.text


@pytest.mark.parametrize("stream", [False, True])
async def test_unsupported_guidance_fails_before_dispatch(client, test_app, stream):
    captured = []

    def handle(request):
        captured.append(request)
        return upstream_response(stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        test_app.state.http_client = upstream
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {test_app.state._test_key}"},
            json={**request_data(guided_json=SCHEMA), "model": "gpt-4o-mini", "stream": stream},
        )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["param"] == "guided_json"
    assert captured == []


async def test_vllm_rejected_schema_returns_error_without_unconstrained_retry(client, vllm_app):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "private schema error"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        vllm_app.state.http_client = upstream
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {vllm_app.state._test_key}"},
            json={**request_data(guided_json=SCHEMA), "model": "gpt-4o-mini"},
        )
    assert response.status_code == 400, response.text
    assert len(captured) == 1
    assert captured[0]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "private schema error" not in response.text


async def test_cache_separates_plain_and_constrained_vllm_requests(client, vllm_app):
    for token, record in vllm_app.state._test_repo.records.items():
        vllm_app.state._test_repo.records[token] = replace(record, rpm_limit=10)
    _enable_cache(vllm_app)
    captured = []

    def handle(request):
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(
            200, json=completion() if "response_format" in body else completion("plain")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        vllm_app.state.http_client = upstream
        headers = {"Authorization": f"Bearer {vllm_app.state._test_key}"}
        base = {**request_data(), "model": "gpt-4o-mini"}
        plain = await client.post("/v1/chat/completions", headers=headers, json=base)
        guided = await client.post(
            "/v1/chat/completions", headers=headers, json={**base, "guided_json": SCHEMA}
        )
        cached = await client.post(
            "/v1/chat/completions", headers=headers, json={**base, "guided_json": SCHEMA}
        )
    assert plain.status_code == guided.status_code == cached.status_code == 200
    assert plain.json()["choices"][0]["message"]["content"] == "plain"
    assert guided.json()["choices"][0]["message"]["content"] != "plain"
    assert cached.headers["x-deltallm-cache-hit"] == "true"
    assert len(captured) == 2
