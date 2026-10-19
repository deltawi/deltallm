from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.models.errors import GatewayCapacityError, InvalidRequestError, ProxyError
from src.models.requests import ChatCompletionRequest, EmbeddingRequest
from src.providers.embedding_request import translate_embedding_request
from src.providers.chat_discovery import parse_chat_models
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.model_catalog import catalog_models_for_provider
from src.providers.profiled_chat import ProfiledChatAdapter
from src.providers.resolution import provider_presets, resolve_upstream_model
from src.services.models.model_deployments import resolve_runtime_deltallm_params
from tests.providers import test_chat_discovery as discovery_contract


@pytest.fixture
def contract():
    path = Path(__file__).parents[1] / "fixtures" / "providers" / "mistral.json"
    return json.loads(path.read_text())


def test_mistral_preset_catalog_and_credential_isolation():
    preset = next(item for item in provider_presets() if item["provider"] == "mistral")
    assert preset == {
        "provider": "mistral",
        "api_base": "https://api.mistral.ai/v1",
        "compat": "openai",
        "supported_modes": ["chat", "embedding"],
    }
    settings = SimpleNamespace(openai_api_key="private-openai-key", openai_base_url="https://other")
    params = resolve_runtime_deltallm_params({"model": "mistral/mistral-small-latest"}, settings)
    assert params["api_base"] == preset["api_base"]
    assert not params.get("api_key")
    assert resolve_upstream_model(params) == "mistral-small-latest"
    assert (
        resolve_upstream_model({"provider": "mistral", "model": "vendor/custom"}) == "vendor/custom"
    )
    embeddings = catalog_models_for_provider("mistral", mode="embedding")
    assert {item["id"] for item in embeddings} == {"mistral-embed", "codestral-embed"}
    assert all(item["known_metadata"] is None for item in embeddings)


async def test_mistral_tools_json_and_native_stream_usage(contract):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES["mistral"])
        request = ChatCompletionRequest.model_validate(
            {
                "model": "alias",
                "messages": [{"role": "user", "content": "Weather?"}],
                "stream": True,
                "stream_options": {"include_usage": True},
                "tools": [{"function": {"name": "weather", "parameters": {"type": "object"}}}],
                "tool_choice": {"function": {"name": "weather"}},
                "response_format": {"type": "json_object"},
                "metadata": {"internal": "private"},
            }
        )
        payload = await adapter.translate_request(
            request, {"provider": "mistral", "model": "mistral/mistral-small-latest"}
        )
        assert payload["model"] == contract["model"]
        assert payload["tools"][0]["type"] == "function"
        assert payload["tool_choice"]["type"] == "function"
        assert payload["response_format"] == {"type": "json_object"}
        assert not {"stream_options", "metadata", "temperature", "top_p"}.intersection(payload)
        response = await adapter.translate_response(contract["tool"], "alias")
        message = response.model_dump(mode="json")["choices"][0]["message"]
        assert message["content"] is None
        assert message["tool_calls"] == contract["tool"]["choices"][0]["message"]["tool_calls"]


@pytest.mark.parametrize(
    "status,expected", [(400, 400), (401, 503), (403, 503), (429, 429), (500, 503), (503, 503)]
)
async def test_mistral_errors_are_sanitized(status, expected):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES["mistral"])
        error = adapter.map_error(
            httpx.HTTPStatusError(
                "private-url",
                request=httpx.Request("POST", "https://api.mistral.ai/v1"),
                response=httpx.Response(status, json={"message": "private-key", "type": "error"}),
            )
        )
    assert error.status_code == expected
    assert "private" not in error.message


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout])
async def test_mistral_timeouts_keep_failure_scope(error_type):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES["mistral"])
        error = adapter.map_error(error_type("private-url"))
    assert "private" not in error.message
    if error_type is httpx.PoolTimeout:
        assert isinstance(error, GatewayCapacityError)
        assert not error.affects_deployment_health
    else:
        assert error.status_code == 408
        assert error.affects_deployment_health


@pytest.mark.parametrize("frame", ["data: []", "data: invalid", "data: [DONE]"])
async def test_mistral_rejects_malformed_stream(frame):
    async def upstream():
        yield frame

    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES["mistral"])
        with pytest.raises(ProxyError):
            _ = [line async for line in adapter.translate_stream(upstream())]


def test_mistral_discovery_excludes_unsupported_and_archived_models():
    body = json.dumps(
        {
            "data": [
                {"id": "custom-chat", "capabilities": {"completion_chat": True}},
                {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
                {"id": "codestral-embed"},
                {"id": "codestral-embed-dated", "aliases": ["codestral-embed"]},
                {"id": "mistral-ocr-latest", "capabilities": {"ocr": True}},
                {"id": "old-chat", "capabilities": {"completion_chat": True}, "archived": True},
            ]
        }
    ).encode()
    result = parse_chat_models(CHAT_PROVIDER_PROFILES["mistral"], body)
    assert {item.id: item.supported_modes for item in result.models} == {
        "custom-chat": ("chat",),
        "mistral-embed": ("embedding",),
        "codestral-embed": ("embedding",),
        "codestral-embed-dated": ("embedding",),
    }


def test_embedding_translation_preserves_openai_and_maps_mistral_dimensions():
    request = EmbeddingRequest(model="alias", input=["Hello"], dimensions=256)
    params = {"provider": "mistral", "model": "mistral/codestral-embed"}
    payload = translate_embedding_request(request, params)
    assert payload == {
        "model": "codestral-embed",
        "input": ["Hello"],
        "encoding_format": "float",
        "output_dimension": 256,
    }
    openai = translate_embedding_request(
        request, {"provider": "openai", "model": "openai/text-embedding-3-small"}
    )
    assert openai == {**request.model_dump(exclude_none=True), "model": "text-embedding-3-small"}


@pytest.mark.parametrize("input_value", [[1, 2], [[1, 2]]])
def test_mistral_embedding_token_arrays_are_rejected(input_value):
    with pytest.raises(InvalidRequestError, match="text input"):
        translate_embedding_request(
            EmbeddingRequest(model="alias", input=input_value), {"provider": "mistral"}
        )


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
async def test_mistral_discovery_failure_keeps_catalog_and_redacts(contract, status):
    await discovery_contract.test_discovery_failure_preserves_catalog_and_redacts(contract, status)


def test_mistral_embedding_user_is_rejected():
    with pytest.raises(InvalidRequestError, match="user"):
        translate_embedding_request(
            EmbeddingRequest(model="alias", input="Hello", user="private-user"),
            {"provider": "mistral"},
        )
