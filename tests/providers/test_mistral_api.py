from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from src.providers.model_discovery import discover_provider_models
from src.services.models.model_deployments import resolve_runtime_deltallm_params
from tests.providers.test_chat_discovery import discovery_runtime
from tests.providers import test_chat_provider_api as chat_contract


@pytest.fixture
def contract():
    path = Path(__file__).parents[1] / "fixtures" / "providers" / "mistral.json"
    return json.loads(path.read_text())


@pytest.fixture
def configured_app(test_app, contract):
    store = test_app.state.router.deployment_registry
    original = store["gpt-4o-mini"][0]
    store.replace(
        {
            **store.snapshot(),
            "gpt-4o-mini": [
                replace(
                    original,
                    deltallm_params={
                        "provider": "mistral",
                        "model": contract["model"],
                        "api_key": "provider-key",
                    },
                    model_info={"mode": "chat"},
                )
            ],
        }
    )
    return test_app


@pytest.mark.parametrize("stream", [False, True])
async def test_mistral_chat_single_call_and_usage(client, configured_app, contract, stream):
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://api.mistral.ai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer provider-key"
        body = json.loads(request.content)
        assert body["model"] == contract["model"]
        assert "stream_options" not in body
        if stream:
            return httpx.Response(200, text="\n\n".join(contract["stream"]) + "\n\n")
        return httpx.Response(200, json=contract["success"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured_app.state._test_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": stream,
                "stream_options": {"include_usage": True},
            },
        )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    if stream:
        assert "[DONE]" in response.text
        frames = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        assert any(frame.get("usage", {}).get("completion_tokens") == 4 for frame in frames)
    else:
        assert response.json()["choices"][0]["message"]["content"] == "Sunny."
        assert response.json()["usage"]["total_tokens"] == 14


async def test_mistral_model_length_is_returned_without_retry(client, configured_app, contract):
    calls = []
    truncated = deepcopy(contract["success"])
    truncated["choices"][0]["finish_reason"] = "model_length"

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=truncated)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured_app.state._test_key}"},
            json=chat_contract.request_body(),
        )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["finish_reason"] == "length"
    assert len(calls) == 1


@pytest.mark.parametrize("after_output", [False, True])
async def test_mistral_native_stream_error_respects_retry_boundary(
    client, configured_app, contract, after_output
):
    store = configured_app.state.router.deployment_registry
    primary = store["gpt-4o-mini"][0]
    fallback = replace(primary, deployment_id="mistral-fallback")
    store.replace({**store.snapshot(), "gpt-4o-mini": [primary, fallback]})

    async def choose_primary(model_group, request_context):
        return primary

    configured_app.state.router.select_deployment = choose_primary
    terminal = json.loads(contract["stream"][-2][6:])
    terminal["choices"][0]["finish_reason"] = "error"
    calls = []
    responses = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            frames = contract["stream"][: 2 if after_output else 1]
            frames = [*frames, "data: " + json.dumps(terminal), "data: [DONE]"]
        else:
            frames = contract["stream"]
        response = httpx.Response(200, text="\n\n".join(frames) + "\n\n")
        responses.append(response)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured_app.state._test_key}"},
            json=chat_contract.request_body(stream=True),
        )
    assert response.status_code == 200, response.text
    assert len(calls) == (1 if after_output else 2)
    assert all(response.is_closed for response in responses)
    if after_output:
        assert "[DONE]" not in response.text
        assert response.headers["x-deltallm-route-fallback-used"] == "false"
    else:
        assert "[DONE]" in response.text
        assert response.headers["x-deltallm-route-deployment"] == "mistral-fallback"
    for deployment in (primary, fallback):
        assert (
            await configured_app.state.router_state_backend.get_active_requests(
                deployment.deployment_id
            )
            == 0
        )


async def test_mistral_embeddings_use_default_base_and_strip_prefix(client, configured_app):
    store = configured_app.state.router.deployment_registry
    original = store["gpt-4o-mini"][0]
    params = resolve_runtime_deltallm_params(
        {"provider": "mistral", "model": "mistral/mistral-embed", "api_key": "provider-key"},
        configured_app.state.settings,
    )
    store.replace(
        {
            **store.snapshot(),
            "gpt-4o-mini": [
                replace(
                    original,
                    deltallm_params=params,
                    model_info={"mode": "embedding"},
                )
            ],
        }
    )
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://api.mistral.ai/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer provider-key"
        body = json.loads(request.content)
        assert body["model"] == "mistral-embed"
        assert body["input"] == ["Hello"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "mistral-embed",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        response = await client.post(
            "/v1/embeddings",
            headers={"Authorization": f"Bearer {configured_app.state._test_key}"},
            json={"model": "gpt-4o-mini", "input": ["Hello"]},
        )
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["embedding"] == [0.1, 0.2]
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["chat", "embedding"])
async def test_mistral_live_discovery_filters_modes(mode):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer provider-key"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "mistral-small-latest", "capabilities": {"completion_chat": True}},
                    {"id": "custom-chat", "capabilities": {"completion_chat": True}},
                    {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
                    {"id": "mistral-ocr-latest", "capabilities": {"ocr": True}},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        result = await discover_provider_models(
            upstream,
            provider="mistral",
            mode=mode,
            api_key="provider-key",
            default_openai_base_url="https://other.example/v1",
            discovery_runtime=discovery_runtime(upstream._transport),
        )
    assert len(calls) == 1
    assert not result["warnings"]
    assert all(item["supported_modes"] == [mode] for item in result["data"])
    assert all(item["id"] != "mistral-ocr-latest" for item in result["data"])
    expected = "mistral-small-latest" if mode == "chat" else "mistral-embed"
    match = next(item for item in result["data"] if item["id"] == expected)
    assert match["source"] == "catalog+provider_api"


async def test_mistral_admin_preset_and_unsupported_mode(client, test_app):
    test_app.state.settings.master_key = "mk-test"
    headers = {"Authorization": "Bearer mk-test"}
    response = await client.get("/ui/api/provider-presets", headers=headers)
    preset = next(item for item in response.json()["data"] if item["provider"] == "mistral")
    assert preset["supported_modes"] == ["chat", "embedding"]
    response = await client.post(
        "/ui/api/models",
        headers=headers,
        json={
            "model_name": "unsupported-mistral",
            "deltallm_params": {
                "provider": "mistral",
                "model": "mistral-small-latest",
                "api_key": "provider-key",
                "api_base": "https://api.mistral.ai/v1",
            },
            "model_info": {"mode": "image_generation"},
        },
    )
    assert response.status_code == 400
    assert "does not support mode" in response.text


async def test_mistral_named_credential_setup(client, test_app, contract, monkeypatch):
    await chat_contract.test_named_credential_and_model_setup(
        client, test_app, contract, monkeypatch
    )


async def test_mistral_stream_failover_before_output(client, configured_app, contract):
    await chat_contract.test_stream_failover_is_allowed_before_output(
        client, configured_app, contract
    )


async def test_mistral_cancel_closes_stream_and_releases_lease(client, configured_app, contract):
    await chat_contract.test_cancel_after_reasoning_closes_stream_and_releases_lease(
        client, configured_app, contract
    )


@pytest.mark.parametrize("frame_index", [1, 2])
async def test_mistral_stream_failure_after_output_never_retries(
    client, configured_app, contract, frame_index
):
    await chat_contract.test_stream_error_after_output_never_retries(
        client, configured_app, contract, frame_index
    )


async def test_mistral_tool_conversation(client, configured_app, contract):
    await chat_contract.test_tool_conversation_preserves_provider_reasoning(
        client, configured_app, contract
    )


async def test_mistral_credential_setup_requires_admin(client, test_app, contract):
    await chat_contract.test_provider_setup_requires_admin(client, test_app, contract)


async def test_mistral_health_uses_control_pool(client, configured_app, contract):
    configured_app.state.settings.master_key = "mk-test"
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer provider-key"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": contract["model"],
                        "capabilities": {"completion_chat": True},
                    }
                ]
            },
        )

    def inference(request):
        raise AssertionError("Health check used the inference pool")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as control,
        httpx.AsyncClient(transport=httpx.MockTransport(inference)) as upstream,
    ):
        configured_app.state.control_http_client = control
        configured_app.state.provider_discovery_runtime = discovery_runtime(control._transport)
        configured_app.state.http_client = upstream
        deployment = configured_app.state.router.deployment_registry["gpt-4o-mini"][0]
        response = await client.post(
            f"/ui/api/models/{deployment.deployment_id}/health-check",
            headers={"Authorization": "Bearer mk-test"},
        )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
