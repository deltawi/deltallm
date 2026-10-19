from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from src.cache.key_builder import CacheKeyBuilder
from src.chat.response_validation import heal_json_choices
from src.models.errors import InvalidRequestError
from src.models.request_serialization import dump_request_for_preflight
from src.models.requests import ChatCompletionRequest
from src.providers.anthropic import AnthropicAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter
from src.providers.profiled_chat import ProfiledChatAdapter


SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": "string", "default": None},
        "total": {"type": "number"},
    },
    "required": ["invoice_number", "total"],
    "additionalProperties": False,
}


def request_data(**extra: object) -> dict[str, object]:
    return {
        "model": "private-model",
        "messages": [{"role": "user", "content": "Invoice?"}],
        **extra,
    }


@pytest.mark.parametrize("schema", [SCHEMA, json.dumps(SCHEMA), {}])
async def test_guided_json_survives_preflight_and_becomes_vllm_response_format(schema):
    canonical = ChatCompletionRequest.model_validate(request_data(guided_json=schema))
    restored = ChatCompletionRequest.model_validate(dump_request_for_preflight(canonical))
    expected = json.loads(schema) if isinstance(schema, str) else schema
    async with httpx.AsyncClient() as client:
        translated = await OpenAIAdapter(client).translate_request(
            restored, {"provider": "vllm", "model": "private-model"}
        )
    assert "guided_json" not in translated
    assert translated["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "guided_json", "schema": expected},
    }


@pytest.mark.parametrize("schema", [[], True, 1, "invalid JSON", "[]", "true"])
def test_invalid_guided_json_is_rejected(schema):
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(request_data(guided_json=schema))


@pytest.mark.parametrize("format_type", ["text", "json_object", "json_schema"])
def test_guided_json_and_response_format_are_mutually_exclusive(format_type):
    with pytest.raises(ValidationError, match="only one of guided_json and response_format"):
        ChatCompletionRequest.model_validate(
            request_data(guided_json={}, response_format={"type": format_type})
        )


@pytest.mark.parametrize(
    "provider,adapter_type",
    [
        ("openai", OpenAIAdapter),
        ("unknown", OpenAIAdapter),
        ("lmstudio", OpenAIAdapter),
        ("ollama", OpenAIAdapter),
        ("azure_openai", AzureOpenAIAdapter),
        ("anthropic", AnthropicAdapter),
        ("bedrock", BedrockAdapter),
        ("gemini", GeminiAdapter),
    ],
)
async def test_unsupported_adapters_reject_guided_json(provider, adapter_type):
    async with httpx.AsyncClient() as client:
        adapter = adapter_type(client)
        with pytest.raises(InvalidRequestError) as error:
            await adapter.translate_request(
                ChatCompletionRequest.model_validate(request_data(guided_json={})),
                {"provider": provider, "model": "test-model"},
            )
    assert error.value.param == "guided_json"
    assert error.value.status_code == 400


@pytest.mark.parametrize("profile", CHAT_PROVIDER_PROFILES.values(), ids=lambda p: p.provider)
async def test_profiled_adapters_reject_guided_json(profile):
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidRequestError) as error:
            await ProfiledChatAdapter(client, profile).translate_request(
                ChatCompletionRequest.model_validate(request_data(guided_json={})),
                {"provider": profile.provider, "model": "test-model"},
            )
    assert error.value.param == "guided_json"


@pytest.mark.parametrize("provider", ["openai", "vllm"])
async def test_response_format_is_unchanged_when_guided_json_is_null(provider):
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "invoice", "schema": SCHEMA, "strict": True},
    }
    async with httpx.AsyncClient() as client:
        translated = await OpenAIAdapter(client).translate_request(
            ChatCompletionRequest.model_validate(
                request_data(guided_json=None, response_format=response_format)
            ),
            {"provider": provider, "model": "test-model"},
        )
    assert "guided_json" not in translated
    assert translated["response_format"] == response_format


@pytest.mark.parametrize("custom_key", [None, "same-custom-key"])
@pytest.mark.parametrize("fields", [None, {"model", "messages"}])
def test_guided_json_always_participates_in_cache_identity(custom_key, fields):
    builder = CacheKeyBuilder(fields=fields)
    keys = {
        builder.build_key_from_payload(request_data(**extra), custom_key=custom_key)
        for extra in ({}, {"guided_json": {}}, {"guided_json": SCHEMA})
    }
    assert len(keys) == 3


def test_guided_json_uses_existing_json_response_validation():
    response = {"choices": [{"message": {"content": '```json\n{"total": 1}\n```'}}]}
    request = ChatCompletionRequest.model_validate(request_data(guided_json=SCHEMA))
    assert heal_json_choices(response, request)
    assert response["choices"][0]["message"]["content"] == '{"total": 1}'


def test_guided_json_cache_identity_preserves_schema_numeric_precision():
    builder = CacheKeyBuilder()
    keys = {
        builder.build_key_from_payload(request_data(guided_json={"minimum": minimum}))
        for minimum in (0.0000001, 0.0000002)
    }
    assert len(keys) == 2
