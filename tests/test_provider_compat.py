from __future__ import annotations

import httpx
import pytest

from src.models.requests import ChatCompletionRequest
from src.providers.anthropic import AnthropicAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter


async def _line_stream(lines: list[str]):
    for line in lines:
        yield line


@pytest.mark.asyncio
async def test_openai_adapter_omits_tool_choice_without_tools() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            tool_choice="auto",
        )
        payload = await adapter.translate_request(req, {"model": "openai/gpt-4o-mini"})
        assert "tool_choice" not in payload
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_keeps_tool_choice_with_tools() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}],
            tool_choice="auto",
        )
        payload = await adapter.translate_request(req, {"model": "openai/gpt-4o-mini"})
        assert payload.get("tool_choice") == "auto"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_preserves_slash_prefixed_model_for_groq() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": "hi"}],
        )
        payload = await adapter.translate_request(
            req,
            {"provider": "groq", "model": "openai/gpt-oss-120b"},
        )
        assert payload["model"] == "openai/gpt-oss-120b"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_maps_max_tokens_to_max_completion_tokens_for_gpt5() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=64,
        )
        payload = await adapter.translate_request(
            req,
            {"provider": "openai", "model": "openai/gpt-5-mini"},
        )
        assert "max_tokens" not in payload
        assert payload["max_completion_tokens"] == 64
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_keeps_max_tokens_for_non_gpt5_models() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=64,
        )
        payload = await adapter.translate_request(
            req,
            {"provider": "openai", "model": "openai/gpt-4o-mini"},
        )
        assert payload["max_tokens"] == 64
        assert "max_completion_tokens" not in payload
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_allows_tool_call_messages_without_content() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "id": "chatcmpl-tool-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "openai/gpt-oss-120b",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_docs_search",
                                    "type": "function",
                                    "function": {
                                        "name": "docs.search",
                                        "arguments": "{\"query\":\"DeltaLLM\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
            model_name="openai/gpt-oss-120b",
        )
        payload = canonical.model_dump(mode="json")
        assert payload["choices"][0]["message"]["content"] == ""
        assert payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "docs.search"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_surfaces_provider_error_message() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            400,
            json={"error": {"message": "tool_choice is not supported for this model"}},
            request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
        )
        exc = httpx.HTTPStatusError("bad request", request=response.request, response=response)
        mapped = adapter.map_error(exc)
        assert str(mapped) == "tool_choice is not supported for this model"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_azure_openai_adapter_maps_max_tokens_to_max_completion_tokens_for_gpt5() -> None:
    adapter = AzureOpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=64,
        )
        payload = await adapter.translate_request(
            req,
            {"provider": "azure_openai", "model": "azure_openai/gpt-5-mini"},
        )
        assert "max_tokens" not in payload
        assert payload["max_completion_tokens"] == 64
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_translate_request_and_response() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="claude-3-5-sonnet-latest",
            messages=[
                {"role": "system", "content": "be concise"},
                {"role": "user", "content": "Say hi"},
            ],
            max_tokens=32,
        )
        upstream = await adapter.translate_request(req, {"model": "anthropic/claude-3-5-sonnet-latest"})
        assert upstream["model"] == "claude-3-5-sonnet-latest"
        assert upstream["system"] == "be concise"
        assert upstream["max_tokens"] == 32
        assert upstream["messages"][0]["role"] == "user"

        canonical = await adapter.translate_response(
            {
                "id": "msg_123",
                "model": "claude-3-5-sonnet-latest",
                "content": [{"type": "text", "text": "Hello"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
            model_name="anthropic/claude-3-5-sonnet-latest",
        )
        payload = canonical.model_dump(mode="json")
        assert payload["choices"][0]["message"]["content"] == "Hello"
        assert payload["usage"]["total_tokens"] == 6
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_translate_stream_to_openai_chunks() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        lines = [
            'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-3-5-sonnet-latest"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'data: {"type":"message_stop"}',
        ]
        out = [line async for line in adapter.translate_stream(_line_stream(lines))]
        assert any('"role":"assistant"' in line for line in out)
        assert any('"content":"Hello"' in line for line in out)
        assert any('"content":" world"' in line for line in out)
        assert out[-1] == "data: [DONE]"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_gemini_adapter_translate_request_and_response() -> None:
    adapter = GeminiAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gemini-2.5-flash",
            messages=[
                {"role": "system", "content": "be concise"},
                {"role": "user", "content": "Say hi"},
            ],
            max_tokens=32,
        )
        upstream = await adapter.translate_request(req, {"model": "gemini/gemini-2.5-flash"})
        assert upstream["systemInstruction"]["parts"][0]["text"] == "be concise"
        assert upstream["contents"][0]["role"] == "user"
        assert upstream["generationConfig"]["maxOutputTokens"] == 32

        canonical = await adapter.translate_response(
            {
                "responseId": "resp_123",
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Hello"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2, "totalTokenCount": 6},
            },
            model_name="gemini/gemini-2.5-flash",
        )
        payload = canonical.model_dump(mode="json")
        assert payload["choices"][0]["message"]["content"] == "Hello"
        assert payload["usage"]["total_tokens"] == 6
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_request_and_response() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[
                {"role": "system", "content": "be concise"},
                {"role": "user", "content": "Say hi"},
            ],
            max_tokens=32,
        )
        upstream = await adapter.translate_request(req, {"model": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"})
        assert upstream["system"][0]["text"] == "be concise"
        assert upstream["messages"][0]["role"] == "user"
        assert upstream["inferenceConfig"]["maxTokens"] == 32

        canonical = await adapter.translate_response(
            {
                "requestId": "req_123",
                "output": {
                    "message": {
                        "content": [{"text": "Hello"}],
                    }
                },
                "stopReason": "end_turn",
                "usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6},
            },
            model_name="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        )
        payload = canonical.model_dump(mode="json")
        assert payload["choices"][0]["message"]["content"] == "Hello"
        assert payload["usage"]["total_tokens"] == 6
    finally:
        await adapter.http_client.aclose()
