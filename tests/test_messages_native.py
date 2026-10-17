from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx
import pytest

from src.models.errors import InvalidRequestError
from src.models.requests import AnthropicMessagesRequest, ChatCompletionRequest
from src.providers.anthropic_messages import canonical_from_native_message, native_usage
from src.routers.anthropic_request_view import build_messages_policy_view

CLAUDE_CODE_BODY: dict[str, Any] = {
    "model": "gpt-4o-mini",
    "max_tokens": 1024,
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "high"},
    "context_management": {"edits": [{"type": "clear_thinking_20251015"}]},
    "system": [
        {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1"},
        {"type": "text", "text": "You are Claude Code.", "cache_control": {"type": "ephemeral"}},
    ],
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<system-reminder>ctx</system-reminder>"},
                {"type": "text", "text": "fix the bug", "cache_control": {"type": "ephemeral"}},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "look at main.py", "signature": "sig-1"},
                {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"path": "main.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": [{"type": "text", "text": "print(1)"}],
                },
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "aGk="},
                },
            ],
        },
    ],
    "tools": [
        {
            "name": "Read",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            "cache_control": {"type": "ephemeral"},
        }
    ],
    "metadata": {"user_id": "user_abc", "trace_id": "deltallm-only"},
    "top_k": 5,
}


def _view(body: dict[str, Any]):
    return build_messages_policy_view(
        AnthropicMessagesRequest.model_validate(body),
        body,
        anthropic_beta="interleaved-thinking-2025-05-14,context-1m-2025-08-07",
        anthropic_version="2023-06-01",
    )


def _with_messages(view, contents: list[Any]) -> ChatCompletionRequest:
    data = view.canonical.model_dump(mode="json", exclude_unset=True)
    for message, content in zip(data["messages"], contents):
        if content is not None:
            message["content"] = content
    return ChatCompletionRequest.model_validate(data)


def test_policy_view_accepts_claude_code_request_and_keeps_text() -> None:
    view = _view(CLAUDE_CODE_BODY)

    messages = view.canonical.messages
    assert [message.role for message in messages] == ["system", "user", "assistant", "tool"]
    assert messages[0].content == (
        "x-anthropic-billing-header: cc_version=2.1\nYou are Claude Code."
    )
    assert messages[1].content == "<system-reminder>ctx</system-reminder>\nfix the bug"
    assert messages[2].tool_calls[0]["function"]["name"] == "Read"
    assert messages[3].content == "print(1)"
    assert view.canonical.tools[0].function["name"] == "Read"
    # The image has no chat form on this path, so translation must reject it.
    assert view.translation_blocker is not None
    assert "messages[2].content[1]" in view.translation_blocker


def test_native_request_forwards_body_unchanged_without_policy_changes() -> None:
    view = _view(CLAUDE_CODE_BODY)

    native = view.native_request(view.canonical)

    assert native is not None
    expected = {**CLAUDE_CODE_BODY, "metadata": {"user_id": "user_abc"}}
    assert native.body == expected
    assert native.anthropic_beta == "interleaved-thinking-2025-05-14,context-1m-2025-08-07"
    upstream = native.upstream_body(model="claude-sonnet-4-6", max_tokens=1000, stream=True)
    assert upstream["model"] == "claude-sonnet-4-6"
    assert upstream["max_tokens"] == 1000
    assert upstream["stream"] is True
    assert upstream["thinking"] == {"type": "adaptive"}
    assert upstream["system"] == CLAUDE_CODE_BODY["system"]


def test_native_request_writes_guardrail_text_back_into_blocks() -> None:
    view = _view(CLAUDE_CODE_BODY)
    redacted = _with_messages(
        view, [None, "<system-reminder>ctx</system-reminder>\nfix <PERSON>", None, "[REDACTED]"]
    )

    native = view.native_request(redacted)

    assert native is not None
    user_content = native.body["messages"][0]["content"]
    assert user_content == [
        {
            "type": "text",
            "text": "<system-reminder>ctx</system-reminder>\nfix <PERSON>",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    tool_result = native.body["messages"][2]["content"][0]
    assert tool_result["content"] == [{"type": "text", "text": "[REDACTED]"}]
    # The original body is not changed.
    assert CLAUDE_CODE_BODY["messages"][0]["content"][1]["text"] == "fix the bug"


def test_native_request_falls_back_when_policy_changes_structure() -> None:
    body = {
        "model": "gpt-4o-mini",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }
    view = _view(body)
    data = view.canonical.model_dump(mode="json", exclude_unset=True)
    data["messages"].insert(0, {"role": "system", "content": "prompt registry"})

    assert view.native_request(ChatCompletionRequest.model_validate(data)) is None


def test_native_request_rejects_structure_change_with_native_only_content() -> None:
    view = _view(CLAUDE_CODE_BODY)
    data = view.canonical.model_dump(mode="json", exclude_unset=True)
    data["messages"].insert(0, {"role": "system", "content": "prompt registry"})

    with pytest.raises(InvalidRequestError):
        view.native_request(ChatCompletionRequest.model_validate(data))


def test_native_usage_counts_cache_writes_and_reads_as_prompt_tokens() -> None:
    usage = native_usage(
        {
            "input_tokens": 10,
            "output_tokens": 7,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 1000,
        }
    )

    assert usage == {
        "prompt_tokens": 1110,
        "completion_tokens": 7,
        "total_tokens": 1117,
        "prompt_tokens_cached": 1000,
        "cache_creation_input_tokens": 100,
    }


def test_canonical_from_native_message_keeps_text_reasoning_and_tools() -> None:
    canonical = canonical_from_native_message(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [
                {"type": "thinking", "thinking": "plan", "signature": "s"},
                {"type": "text", "text": "done"},
                {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"cmd": "ls"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
        "gpt-4o-mini",
    )

    message = canonical.choices[0].message
    assert message.content == "done"
    assert message.reasoning_content == "plan"
    assert message.tool_calls[0]["function"]["arguments"] == '{"cmd": "ls"}'
    assert canonical.choices[0].finish_reason == "tool_calls"
    assert canonical.usage.prompt_tokens == 3


def _use_anthropic_deployment(test_app) -> None:  # noqa: ANN001
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "anthropic"
    deployment.deltallm_params["model"] = "anthropic/claude-sonnet-4-6"
    deployment.deltallm_params["api_key"] = "provider-key"


NATIVE_RESPONSE = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [
        {"type": "thinking", "thinking": "plan", "signature": "sig-2"},
        {"type": "text", "text": "Fixed."},
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 12,
        "output_tokens": 4,
        "cache_creation_input_tokens": 50,
        "cache_read_input_tokens": 900,
    },
}


@pytest.mark.asyncio
async def test_messages_native_hop_forwards_body_headers_and_response(client, test_app):
    _use_anthropic_deployment(test_app)
    captured: dict[str, Any] = {}

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del timeout, content
        captured.update(url=url, headers=headers, json=json)
        return httpx.Response(200, json=NATIVE_RESPONSE)

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/messages?beta=true",
        headers={
            "x-api-key": test_app.state._test_key,
            "anthropic-beta": "interleaved-thinking-2025-05-14",
            "anthropic-version": "2023-06-01",
        },
        json=CLAUDE_CODE_BODY,
    )

    assert response.status_code == 200, response.text
    assert response.json() == NATIVE_RESPONSE
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["anthropic-beta"] == "interleaved-thinking-2025-05-14"
    assert captured["headers"]["x-api-key"] == "provider-key"
    sent = captured["json"]
    assert sent["model"] == "claude-sonnet-4-6"
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["context_management"] == CLAUDE_CODE_BODY["context_management"]
    assert sent["system"] == CLAUDE_CODE_BODY["system"]
    assert sent["messages"] == CLAUDE_CODE_BODY["messages"]
    assert sent["tools"] == CLAUDE_CODE_BODY["tools"]
    assert sent["metadata"] == {"user_id": "user_abc"}
    assert sent["top_k"] == 5
    assert "stream" not in sent


@pytest.mark.asyncio
async def test_messages_native_hop_forwards_client_error_wording(client, test_app):
    _use_anthropic_deployment(test_app)
    message = "thinking.type: Input tag 'adaptive' found using 'type' does not match"

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del url, headers, json, content, timeout
        return httpx.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": message}},
        )

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/messages",
        headers={"x-api-key": test_app.state._test_key},
        json=CLAUDE_CODE_BODY,
    )

    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": message},
    }


@pytest.mark.asyncio
async def test_messages_native_hop_keeps_auth_errors_sanitized(client, test_app):
    _use_anthropic_deployment(test_app)
    secret = "invalid x-api-key sk-ant-provider-secret"

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del url, headers, json, content, timeout
        return httpx.Response(
            401,
            json={"type": "error", "error": {"type": "authentication_error", "message": secret}},
        )

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/messages",
        headers={"x-api-key": test_app.state._test_key},
        json=CLAUDE_CODE_BODY,
    )

    assert response.status_code == 503
    assert secret not in response.text


NATIVE_EVENTS = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_s",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [],
                "stop_reason": None,
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 400,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
    ),
    ("ping", {"type": "ping"}),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "hmm"},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig"},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_9", "name": "Bash", "input": {}},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"cmd":"ls"}'},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 30},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]


def _sse_lines(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    lines: list[str] = []
    for name, data in events:
        lines.extend([f"event: {name}", f"data: {jsonlib.dumps(data)}", ""])
    return lines


class _Stream:
    status_code = 200

    def __init__(self, lines: list[str], captured: dict[str, Any]) -> None:
        self._lines = lines
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_messages_native_stream_relays_every_event_in_order(client, test_app):
    _use_anthropic_deployment(test_app)
    captured: dict[str, Any] = {}

    def stream(method, url, headers, json=None, content=None, timeout=None):  # noqa: ANN001, ANN202
        captured.update(url=url, headers=headers, json=json)
        return _Stream(_sse_lines(NATIVE_EVENTS), captured)

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={**CLAUDE_CODE_BODY, "stream": True},
    )

    assert response.status_code == 200
    assert captured["json"]["stream"] is True
    assert "stream_options" not in captured["json"]
    expected = "".join(
        f"event: {name}\ndata: {jsonlib.dumps(data)}\n\n" for name, data in NATIVE_EVENTS
    )
    assert response.text == expected


@pytest.mark.asyncio
async def test_messages_native_stream_without_stop_ends_with_error_event(client, test_app):
    _use_anthropic_deployment(test_app)

    def stream(method, url, headers, json=None, content=None, timeout=None):  # noqa: ANN001, ANN202
        return _Stream(_sse_lines(NATIVE_EVENTS[:4]), {})

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={**CLAUDE_CODE_BODY, "stream": True},
    )

    assert response.status_code == 200
    assert "event: content_block_delta" in response.text
    assert response.text.rstrip().split("\n\n")[-1].startswith("event: error")
    assert "event: message_stop" not in response.text


@pytest.mark.asyncio
async def test_messages_image_on_translated_deployment_is_rejected(client, test_app):
    response = await client.post(
        "/v1/messages",
        headers={"x-api-key": test_app.state._test_key},
        json=CLAUDE_CODE_BODY,
    )

    assert response.status_code == 400
    assert "Anthropic deployment" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_messages_translated_deployment_drops_thinking_and_cache_control(client, test_app):
    captured: dict[str, Any] = {}

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del headers, content, timeout
        captured.update(url=url, json=json)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    test_app.state.http_client.post = post
    body = {**CLAUDE_CODE_BODY, "messages": CLAUDE_CODE_BODY["messages"][:2]}
    response = await client.post(
        "/v1/messages", headers={"x-api-key": test_app.state._test_key}, json=body
    )

    assert response.status_code == 200, response.text
    assert captured["url"].endswith("/chat/completions")
    assert "thinking" not in captured["json"]
    assert response.json()["content"] == [{"type": "text", "text": "ok"}]


@pytest.mark.asyncio
async def test_x_api_key_is_not_accepted_on_openai_routes(client, test_app):
    response = await client.post(
        "/v1/chat/completions",
        headers={"x-api-key": test_app.state._test_key},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_x_api_key_lists_models(client, test_app):
    response = await client.get(
        "/v1/models?limit=1000", headers={"x-api-key": test_app.state._test_key}
    )

    assert response.status_code == 200
    assert any(item["id"] == "gpt-4o-mini" for item in response.json()["data"])


@pytest.mark.parametrize(
    "extra",
    [
        {"mcp_servers": [{"type": "url", "url": "https://mcp.example.com", "name": "x"}]},
        {"container": "container_123"},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t",
                            "content": [
                                {
                                    "type": "document",
                                    "source": {"type": "file", "file_id": "file_1"},
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    ],
)
def test_policy_view_rejects_provider_account_references(extra: dict[str, Any]) -> None:
    body = {
        "model": "gpt-4o-mini",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        **extra,
    }

    with pytest.raises(InvalidRequestError):
        _view(body)
