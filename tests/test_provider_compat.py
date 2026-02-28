from __future__ import annotations

import httpx
import pytest

from src.models.requests import ChatCompletionRequest
from src.providers.anthropic import AnthropicAdapter
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
