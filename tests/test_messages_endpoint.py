from __future__ import annotations

import json as jsonlib

import httpx
import pytest

from src.models.requests import AnthropicMessagesRequest
from src.routers.anthropic_adapters import (
    AnthropicStreamTranslator,
    anthropic_messages_to_chat_request,
    chat_response_to_anthropic_response,
)


def test_anthropic_messages_to_chat_request_maps_system_and_text() -> None:
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "system": "You are helpful.",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    canonical = anthropic_messages_to_chat_request(payload)

    assert canonical.model == "claude-sonnet-4-6"
    assert canonical.max_tokens == 512
    assert canonical.messages[0].role == "system"
    assert canonical.messages[0].content == "You are helpful."
    assert canonical.messages[1].role == "user"
    assert canonical.messages[1].content == "hello"


def test_anthropic_messages_to_chat_request_round_trips_tool_use_and_result() -> None:
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 512,
            "messages": [
                {"role": "user", "content": "search docs for delta"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {"type": "tool_use", "id": "toolu_1", "name": "docs.search", "input": {"query": "delta"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "delta docs result"},
                    ],
                },
            ],
            "tools": [
                {
                    "name": "docs.search",
                    "description": "Search docs",
                    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                }
            ],
            "tool_choice": {"type": "auto"},
        }
    )

    canonical = anthropic_messages_to_chat_request(payload)

    assistant_message = canonical.messages[1]
    assert assistant_message.role == "assistant"
    assert assistant_message.content == "Let me check."
    assert assistant_message.tool_calls == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {"name": "docs.search", "arguments": jsonlib.dumps({"query": "delta"})},
        }
    ]

    tool_message = canonical.messages[2]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "toolu_1"
    assert tool_message.content == "delta docs result"

    assert canonical.tools is not None
    assert canonical.tools[0].function["name"] == "docs.search"
    assert canonical.tool_choice == "auto"


def test_anthropic_tool_choice_any_maps_to_required() -> None:
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": {"type": "tool", "name": "docs.search"},
        }
    )

    canonical = anthropic_messages_to_chat_request(payload)

    assert canonical.tool_choice.function == {"name": "docs.search"}


@pytest.mark.parametrize(
    ("tool_choice", "expected"),
    [
        ({"type": "auto"}, "auto"),
        ({"type": "any"}, "required"),
        ({"type": "none"}, "none"),
    ],
)
def test_anthropic_tool_choice_modes_map_to_chat(tool_choice: dict[str, str], expected: str) -> None:
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": tool_choice,
        }
    )

    canonical = anthropic_messages_to_chat_request(payload)

    assert canonical.tool_choice == expected


@pytest.mark.parametrize(
    "tool_choice",
    [
        {"type": "bogus"},
        {"type": "tool"},
        {"type": "tool", "name": ""},
        {"type": "tool", "name": "   "},
    ],
)
def test_anthropic_messages_rejects_invalid_tool_choice(tool_choice: dict[str, str]) -> None:
    from src.models.errors import InvalidRequestError

    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": tool_choice,
        }
    )

    with pytest.raises(InvalidRequestError) as exc_info:
        anthropic_messages_to_chat_request(payload)

    assert "tool_choice" in exc_info.value.message


def test_chat_response_to_anthropic_response_maps_text_and_tool_calls() -> None:
    chat_payload = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "claude-sonnet-4-6",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "docs.search", "arguments": jsonlib.dumps({"query": "delta"})},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }

    anthropic_response = chat_response_to_anthropic_response(chat_payload)

    assert anthropic_response["type"] == "message"
    assert anthropic_response["role"] == "assistant"
    assert anthropic_response["stop_reason"] == "tool_use"
    assert anthropic_response["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "docs.search", "input": {"query": "delta"}}
    ]
    assert anthropic_response["usage"] == {"input_tokens": 3, "output_tokens": 2}


def test_anthropic_stream_translator_emits_message_lifecycle_events() -> None:
    translator = AnthropicStreamTranslator(model="claude-sonnet-4-6")

    role_chunk = jsonlib.dumps(
        {
            "id": "chatcmpl-1",
            "model": "claude-sonnet-4-6",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    )
    text_chunk = jsonlib.dumps(
        {
            "id": "chatcmpl-1",
            "model": "claude-sonnet-4-6",
            "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        }
    )
    final_chunk = jsonlib.dumps(
        {
            "id": "chatcmpl-1",
            "model": "claude-sonnet-4-6",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )

    first = translator.translate_line(f"data: {role_chunk}")
    second = translator.translate_line(f"data: {text_chunk}")
    third = translator.translate_line(f"data: {final_chunk}")
    fourth = translator.translate_line("data: [DONE]")

    assert first is not None and "event: message_start" in first
    assert second is not None and "event: content_block_start" in second and "event: content_block_delta" in second
    assert third is None
    assert fourth is not None
    assert "event: content_block_stop" in fourth
    assert "event: message_delta" in fourth
    assert '"stop_reason":"end_turn"' in fourth
    assert "event: message_stop" in fourth


@pytest.mark.asyncio
async def test_messages_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hello"}],
    }

    response = await client.post("/v1/messages", headers=headers, json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "message"
    assert payload["role"] == "assistant"
    assert payload["content"] == [{"type": "text", "text": "ok"}]
    assert payload["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_messages_stream_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/messages", headers=headers, json=body)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: message_start" in response.text
    assert "event: message_delta" in response.text
    assert "event: message_stop" in response.text


@pytest.mark.asyncio
async def test_messages_tool_use_round_trip(client, test_app):
    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, timeout
        assert url.endswith("/chat/completions")
        assert any(message.get("role") == "tool" for message in json["messages"])
        payload = {
            "id": "chatcmpl-tool-1",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_docs_search",
                                "type": "function",
                                "function": {"name": "docs.search", "arguments": jsonlib.dumps({"query": "delta"})},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "max_tokens": 128,
        "messages": [
            {"role": "user", "content": "search docs for delta"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "docs.search", "input": {"query": "delta"}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "delta docs result"}],
            },
        ],
    }

    response = await client.post("/v1/messages", headers=headers, json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["stop_reason"] == "tool_use"
    assert payload["content"] == [
        {"type": "tool_use", "id": "call_docs_search", "name": "docs.search", "input": {"query": "delta"}}
    ]


@pytest.mark.asyncio
async def test_messages_missing_max_tokens_returns_anthropic_error_envelope(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}

    response = await client.post("/v1/messages", headers=headers, json=body)

    assert response.status_code == 400
    payload = response.json()
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "invalid_request_error"
    assert "max_tokens" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_messages_malformed_json_returns_anthropic_error_envelope(client, test_app):
    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "Content-Type": "application/json",
    }

    response = await client.post("/v1/messages", headers=headers, content="{bad")

    assert response.status_code == 400
    payload = response.json()
    assert payload == {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Invalid JSON body"},
    }


def test_anthropic_messages_rejects_unsupported_content_blocks() -> None:
    from src.models.errors import InvalidRequestError

    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is in this image?"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGk="}},
                    ],
                }
            ],
        }
    )

    with pytest.raises(InvalidRequestError) as exc_info:
        anthropic_messages_to_chat_request(payload)

    assert "messages[0].content[1]" in exc_info.value.message
    assert "'image'" in exc_info.value.message


def test_anthropic_messages_rejects_thinking() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        AnthropicMessagesRequest.model_validate(
            {
                "model": "claude-sonnet-4-6",
                "max_tokens": 128,
                "thinking": {"type": "enabled", "budget_tokens": 1024},
                "messages": [{"role": "user", "content": "hi"}],
            }
        )

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


def test_anthropic_messages_rejects_cache_control_on_content_block() -> None:
    from src.models.errors import InvalidRequestError

    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
                }
            ],
        }
    )

    with pytest.raises(InvalidRequestError) as exc_info:
        anthropic_messages_to_chat_request(payload)

    assert "cache_control" in exc_info.value.message


def test_anthropic_messages_rejects_citations_on_content_block() -> None:
    from src.models.errors import InvalidRequestError

    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "citations": []}]}],
        }
    )

    with pytest.raises(InvalidRequestError) as exc_info:
        anthropic_messages_to_chat_request(payload)

    assert "citations" in exc_info.value.message


def test_anthropic_messages_forwards_tool_result_is_error() -> None:
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "docs.search", "input": {}}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "boom", "is_error": True}
                    ],
                },
            ],
        }
    )

    canonical = anthropic_messages_to_chat_request(payload)

    tool_message = next(message for message in canonical.messages if message.role == "tool")
    assert tool_message.content == "Error: boom"


def test_anthropic_messages_rejects_top_k() -> None:
    from src.models.errors import InvalidRequestError

    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "top_k": 40,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )

    with pytest.raises(InvalidRequestError) as exc_info:
        anthropic_messages_to_chat_request(payload)

    assert "top_k" in exc_info.value.message


@pytest.mark.asyncio
async def test_messages_unsupported_block_returns_anthropic_error_envelope(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "max_tokens": 128,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGk="}}],
            }
        ],
    }

    response = await client.post("/v1/messages", headers=headers, json=body)

    assert response.status_code == 400
    payload = response.json()
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "invalid_request_error"
    assert "unsupported content block type 'image'" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_messages_top_k_returns_anthropic_error_envelope(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "max_tokens": 128,
        "top_k": 40,
        "messages": [{"role": "user", "content": "hello"}],
    }

    response = await client.post("/v1/messages", headers=headers, json=body)

    assert response.status_code == 400
    payload = response.json()
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "invalid_request_error"
    assert "top_k" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_messages_budget_exceeded_returns_429(client, test_app):
    from src.billing.budget import BudgetExceeded

    class _AlwaysBudgetExceeded:
        async def check_budgets(self, **kwargs):
            del kwargs
            raise BudgetExceeded(entity_type="key", entity_id="k1", spend=10.0, max_budget=5.0)

    test_app.state.budget_service = _AlwaysBudgetExceeded()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "max_tokens": 128, "messages": [{"role": "user", "content": "hello"}]}

    response = await client.post("/v1/messages", headers=headers, json=body)

    assert response.status_code == 429
