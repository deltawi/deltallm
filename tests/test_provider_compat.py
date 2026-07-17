from __future__ import annotations

import json
import struct
from binascii import crc32

import httpx
import pytest

from src.models.errors import InvalidRequestError, RateLimitError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.providers.anthropic import AnthropicAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter


async def _line_stream(lines: list[str]):
    for line in lines:
        yield line


async def _byte_stream(chunks: list[bytes]):
    for chunk in chunks:
        yield chunk


def _encode_eventstream_message(headers: dict[str, str], payload: dict) -> bytes:
    """Encodes a single AWS binary event-stream frame, matching the format
    botocore.eventstream.EventStreamBuffer expects (used by Bedrock's ConverseStream)."""
    header_bytes = b""
    for name, value in headers.items():
        name_bytes = name.encode("utf-8")
        value_bytes = value.encode("utf-8")
        header_bytes += struct.pack("!B", len(name_bytes)) + name_bytes
        header_bytes += struct.pack("!B", 7) + struct.pack("!H", len(value_bytes)) + value_bytes

    payload_bytes = json.dumps(payload).encode("utf-8")
    total_length = 12 + len(header_bytes) + len(payload_bytes) + 4
    prelude_no_crc = struct.pack("!II", total_length, len(header_bytes))
    prelude_crc = crc32(prelude_no_crc) & 0xFFFFFFFF
    prelude_crc_bytes = struct.pack("!I", prelude_crc)
    message_crc = crc32(prelude_crc_bytes + header_bytes + payload_bytes, prelude_crc) & 0xFFFFFFFF
    return prelude_no_crc + prelude_crc_bytes + header_bytes + payload_bytes + struct.pack("!I", message_crc)


def _stream_json_payloads(lines: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :]
        if payload == "[DONE]":
            continue
        payloads.append(json.loads(payload))
    return payloads


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
async def test_openai_adapter_health_check_uses_custom_auth_headers() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        captured: dict[str, object] = {}

        async def fake_get(url, headers, timeout):  # noqa: ANN001, ANN201
            captured["url"] = url
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

        adapter.http_client.get = fake_get  # type: ignore[method-assign]

        healthy = await adapter.health_check(
            {
                "provider": "vllm",
                "api_base": "https://vllm.example/v1",
                "api_key": "provider-key",
                "auth_header_name": "X-API-Key",
                "auth_header_format": "{api_key}",
            }
        )

        assert healthy is True
        assert captured["url"] == "https://vllm.example/v1/models"
        assert captured["headers"] == {"X-API-Key": "provider-key"}
        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 10.0
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_health_check_defaults_provider_when_missing() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        captured: dict[str, object] = {}

        async def fake_get(url, headers, timeout):  # noqa: ANN001, ANN201
            captured["url"] = url
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

        adapter.http_client.get = fake_get  # type: ignore[method-assign]

        healthy = await adapter.health_check({"api_key": "provider-key"})

        assert healthy is True
        assert captured["url"] == "https://api.openai.com/v1/models"
        assert captured["headers"] == {"Authorization": "Bearer provider-key"}
        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 10.0
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_health_check_returns_false_on_unexpected_error() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        async def fake_get(url, headers, timeout):  # noqa: ANN001, ANN201
            del url, headers, timeout
            raise RuntimeError("unexpected client failure")

        adapter.http_client.get = fake_get  # type: ignore[method-assign]

        assert await adapter.health_check({"api_key": "provider-key"}) is False
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
async def test_anthropic_adapter_forwards_tools_and_tool_messages() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest.model_validate(
            {
                "model": "claude-3-5-sonnet-latest",
                "max_tokens": 32,
                "messages": [
                    {"role": "user", "content": "search docs for delta"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "toolu_1",
                                "type": "function",
                                "function": {"name": "docs.search", "arguments": json.dumps({"query": "delta"})},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "toolu_1", "content": "delta docs result"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "docs.search",
                            "description": "Search docs",
                            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                        },
                    }
                ],
                "tool_choice": "required",
            }
        )
        upstream = await adapter.translate_request(req, {})

        assert upstream["tools"] == [
            {
                "name": "docs.search",
                "description": "Search docs",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ]
        assert upstream["tool_choice"] == {"type": "any"}
        assert upstream["messages"][1] == {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "docs.search", "input": {"query": "delta"}}],
        }
        assert upstream["messages"][2] == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "delta docs result"}],
        }
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_translate_response_maps_tool_use_blocks() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "id": "msg_123",
                "model": "claude-3-5-sonnet-latest",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "id": "toolu_1", "name": "docs.search", "input": {"query": "delta"}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
            model_name="anthropic/claude-3-5-sonnet-latest",
        )
        payload = canonical.model_dump(mode="json")
        message = payload["choices"][0]["message"]
        assert message["content"] == "Checking."
        assert message["tool_calls"] == [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "docs.search", "arguments": json.dumps({"query": "delta"})},
            }
        ]
        assert payload["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_translate_stream_emits_tool_call_chunks() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        lines = [
            'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-3-5-sonnet-latest"}}',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"docs.search","input":{}}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":"}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\\"delta\\"}"}}',
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
            'data: {"type":"message_stop"}',
        ]
        out = [line async for line in adapter.translate_stream(_line_stream(lines))]
        assert any('"name":"docs.search"' in line for line in out)
        assert any('"arguments":"{\\"query\\":"' in line for line in out)
        assert any('"finish_reason":"tool_calls"' in line for line in out)
        assert out[-1] == "data: [DONE]"
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


@pytest.mark.asyncio
async def test_bedrock_adapter_only_sends_explicit_sampling_params() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        base = {
            "model": "anthropic.claude-3-5-sonnet-20240620-v1:0",
            "messages": [{"role": "user", "content": "Say hi"}],
        }

        omitted = await adapter.translate_request(ChatCompletionRequest.model_validate(base), {})
        assert "inferenceConfig" not in omitted

        temperature = await adapter.translate_request(
            ChatCompletionRequest.model_validate({**base, "temperature": 0.7}),
            {},
        )
        assert temperature["inferenceConfig"] == {"temperature": 0.7}

        top_p = await adapter.translate_request(
            ChatCompletionRequest.model_validate({**base, "top_p": 0.8}),
            {},
        )
        assert top_p["inferenceConfig"] == {"topP": 0.8}

        top_p_null = await adapter.translate_request(
            ChatCompletionRequest.model_validate({**base, "top_p": None}),
            {},
        )
        assert "inferenceConfig" not in top_p_null

        both_explicit = await adapter.translate_request(
            ChatCompletionRequest.model_validate({**base, "temperature": 0.5, "top_p": 0.8}),
            {},
        )
        assert both_explicit["inferenceConfig"] == {"temperature": 0.5, "topP": 0.8}
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_surfaces_provider_error_message() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            400,
            json={"message": "`temperature` and `top_p` cannot both be specified"},
            request=httpx.Request("POST", "https://bedrock-runtime.us-east-1.amazonaws.com/model/claude/converse"),
        )
        exc = httpx.HTTPStatusError("bad request", request=response.request, response=response)
        mapped = adapter.map_error(exc)
        assert str(mapped) == "`temperature` and `top_p` cannot both be specified"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_stream_to_openai_chunks() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    assert adapter.stream_uses_bytes is True
    try:
        frames = b"".join(
            [
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStart"},
                    {"role": "assistant"},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 0, "delta": {"text": "Hello"}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 0, "delta": {"text": " world"}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStop"},
                    {"stopReason": "end_turn"},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "metadata"},
                    {"usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6}},
                ),
            ]
        )
        # Split arbitrarily mid-frame to make sure the parser buffers correctly
        # across chunk boundaries, just like real HTTP reads would arrive.
        split = len(frames) // 2
        out = [
            line
            async for line in adapter.translate_stream(
                _byte_stream([frames[:split], frames[split:]]),
                model_name="bedrock-public-model",
            )
        ]
        payloads = _stream_json_payloads(out)

        assert any('"role":"assistant"' in line for line in out)
        assert any('"content":"Hello"' in line for line in out)
        assert any('"content":" world"' in line for line in out)
        assert any('"finish_reason":"stop"' in line for line in out)
        assert any('"total_tokens":6' in line for line in out)
        assert all(payload["model"] == "bedrock-public-model" for payload in payloads)
        assert out[-1] == "data: [DONE]"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_stream_uses_sequential_tool_call_indexes() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        frames = b"".join(
            [
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStart"},
                    {"role": "assistant"},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 0, "delta": {"text": "Let me check."}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockStart"},
                    {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "toolu_1", "name": "docs.search"}}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"query":"delta"}'}}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStop"},
                    {"stopReason": "tool_use"},
                ),
            ]
        )

        out = [line async for line in adapter.translate_stream(_byte_stream([frames]), model_name="bedrock-public-model")]
        payloads = _stream_json_payloads(out)
        tool_deltas = [
            tool_call
            for payload in payloads
            for choice in payload.get("choices", [])
            for tool_call in (choice.get("delta") or {}).get("tool_calls", [])
        ]

        assert tool_deltas[0]["index"] == 0
        assert tool_deltas[1]["index"] == 0
        assert all(payload["model"] == "bedrock-public-model" for payload in payloads)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_forwards_tools_and_tool_messages() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest.model_validate(
            {
                "model": "anthropic.claude-3-5-sonnet-20240620-v1:0",
                "max_tokens": 32,
                "messages": [
                    {"role": "user", "content": "search docs for delta"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "toolu_1",
                                "type": "function",
                                "function": {"name": "docs.search", "arguments": json.dumps({"query": "delta"})},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "toolu_1", "content": "delta docs result"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "docs.search",
                            "description": "Search docs",
                            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "docs.search"}},
            }
        )
        upstream = await adapter.translate_request(req, {})

        assert upstream["toolConfig"] == {
            "tools": [
                {
                    "toolSpec": {
                        "name": "docs.search",
                        "description": "Search docs",
                        "inputSchema": {"json": {"type": "object", "properties": {"query": {"type": "string"}}}},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": "docs.search"}},
        }
        assert upstream["messages"][1] == {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "toolu_1", "name": "docs.search", "input": {"query": "delta"}}}],
        }
        assert upstream["messages"][2] == {
            "role": "user",
            "content": [{"toolResult": {"toolUseId": "toolu_1", "content": [{"text": "delta docs result"}]}}],
        }
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_response_maps_tool_use_blocks() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "output": {
                    "message": {
                        "content": [
                            {"text": "Checking."},
                            {"toolUse": {"toolUseId": "toolu_1", "name": "docs.search", "input": {"query": "delta"}}},
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6},
            },
            model_name="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        )
        payload = canonical.model_dump(mode="json")
        message = payload["choices"][0]["message"]
        assert message["content"] == "Checking."
        assert message["tool_calls"] == [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "docs.search", "arguments": json.dumps({"query": "delta"})},
            }
        ]
        assert payload["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_stream_raises_on_exception_event() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        frame = _encode_eventstream_message(
            {":message-type": "exception", ":exception-type": "validationException"},
            {"message": "Malformed input request"},
        )
        with pytest.raises(InvalidRequestError, match="Malformed input request"):
            async for _ in adapter.translate_stream(_byte_stream([frame])):
                pass
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_stream_classifies_retryable_errors() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        throttle_frame = _encode_eventstream_message(
            {":message-type": "exception", ":exception-type": "throttlingException"},
            {"message": "Too many requests"},
        )
        with pytest.raises(RateLimitError, match="Too many requests") as rate_limit_info:
            async for _ in adapter.translate_stream(_byte_stream([throttle_frame])):
                pass
        assert rate_limit_info.value.affects_deployment_health is True

        server_frame = _encode_eventstream_message(
            {":message-type": "exception", ":exception-type": "internalServerException"},
            {"message": "Internal failure"},
        )
        with pytest.raises(ServiceUnavailableError, match="Internal failure") as unavailable_info:
            async for _ in adapter.translate_stream(_byte_stream([server_frame])):
                pass
        assert unavailable_info.value.affects_deployment_health is True

        unknown_frame = _encode_eventstream_message(
            {":message-type": "error", ":event-type": "somethingUnexpected"},
            {"message": "mystery failure"},
        )
        with pytest.raises(ServiceUnavailableError, match="mystery failure"):
            async for _ in adapter.translate_stream(_byte_stream([unknown_frame])):
                pass
    finally:
        await adapter.http_client.aclose()
