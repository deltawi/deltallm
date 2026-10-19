from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.models.errors import InvalidRequestError, ProxyError
from src.models.requests import ChatCompletionRequest, ResponsesRequest
from src.providers.openai import OpenAIAdapter
from src.providers.openai_responses import (
    NativeResponsesRequest,
    OpenAINativeResponsesAdapter,
    azure_base_serves_responses,
    canonical_from_native_response,
    native_responses_usage,
    openai_base_serves_responses,
)
from src.routers.responses_adapters import (
    ResponsesShape,
    ResponsesStreamTranslator,
    chat_response_to_responses_response,
)
from src.routers.responses_request_view import build_responses_policy_view

# The shape Codex CLI 0.163 sends on its first turn (text shortened).
CODEX_BODY: dict[str, Any] = {
    "model": "gpt-5.1-codex",
    "stream": True,
    "input": [
        {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "You are a coding agent."}],
        },
        {
            "type": "message",
            "id": "msg_1",
            "role": "user",
            "content": [{"type": "input_text", "text": "write hello to a file"}],
        },
    ],
    "tools": [
        {
            "type": "function",
            "name": "exec_command",
            "description": "Runs a command.",
            "strict": False,
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        },
        {
            "type": "namespace",
            "name": "multi_agent_v1",
            "description": "Sub-agents.",
            "tools": [
                {
                    "type": "function",
                    "name": "spawn_agent",
                    "description": "Spawn.",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        },
        {"type": "web_search", "external_web_access": True},
    ],
    "tool_choice": "auto",
    "parallel_tool_calls": True,
    "reasoning": {"summary": "auto"},
    "store": False,
    "include": ["reasoning.encrypted_content"],
    "prompt_cache_key": "thread-1",
    "client_metadata": {"session_id": "thread-1"},
    "metadata": {"tags": ["team-a"]},
}

TOOL_TURN_INPUT: list[dict[str, Any]] = [
    {"type": "message", "role": "user", "content": "list files"},
    {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC"},
    {
        "type": "function_call",
        "call_id": "call_1",
        "name": "exec_command",
        "arguments": '{"cmd":"ls"}',
    },
    {
        "type": "function_call",
        "call_id": "call_2",
        "name": "spawn_agent",
        "namespace": "multi_agent_v1",
        "arguments": "{}",
    },
    {"type": "function_call_output", "call_id": "call_1", "output": "a.py"},
    {"type": "function_call_output", "call_id": "call_2", "output": "agent-7"},
    {"type": "custom_tool_call", "call_id": "call_3", "name": "apply_patch", "input": "*** Begin"},
    {"type": "custom_tool_call_output", "call_id": "call_3", "output": "Done"},
]


def _view(body: dict[str, Any]):
    return build_responses_policy_view(ResponsesRequest.model_validate(body), body)


def test_policy_view_accepts_codex_request() -> None:
    view = _view(CODEX_BODY)

    messages = view.canonical.messages
    assert [message.role for message in messages] == ["system", "user"]
    assert messages[0].content == "You are a coding agent."
    assert messages[1].content == "write hello to a file"
    names = [tool.function["name"] for tool in view.canonical.tools]
    # Namespace tools become flat chat functions; hosted search has no chat form.
    assert names == ["exec_command", "multi_agent_v1__spawn_agent"]
    assert view.translation_blocker is None
    assert view.tools["multi_agent_v1__spawn_agent"].namespace == "multi_agent_v1"


def test_native_request_forwards_codex_body_without_deltallm_metadata() -> None:
    view = _view(CODEX_BODY)

    native = view.native_request(view.canonical)

    assert native is not None
    assert native.body == {k: v for k, v in CODEX_BODY.items() if k != "metadata"}
    upstream = native.upstream_body(model="gpt-5.1-codex-max", max_output_tokens=None, stream=True)
    assert upstream["model"] == "gpt-5.1-codex-max"
    assert upstream["include"] == ["reasoning.encrypted_content"]
    assert "max_output_tokens" not in upstream


def test_native_request_writes_guardrail_text_back() -> None:
    view = _view(CODEX_BODY)
    data = view.canonical.model_dump(mode="json", exclude_unset=True)
    data["messages"][1]["content"] = "write <REDACTED> to a file"

    native = view.native_request(ChatCompletionRequest.model_validate(data))

    assert native is not None
    assert native.body["input"][1]["content"][0]["text"] == "write <REDACTED> to a file"
    assert CODEX_BODY["input"][1]["content"][0]["text"] == "write hello to a file"


def test_policy_view_translates_tool_history() -> None:
    body = {
        "model": "m",
        "input": TOOL_TURN_INPUT,
        "tools": [
            CODEX_BODY["tools"][0],
            CODEX_BODY["tools"][1],
            {"type": "custom", "name": "apply_patch", "description": "Patch files."},
        ],
    }

    view = _view(body)

    messages = view.canonical.messages
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
        "tool",
    ]
    calls = messages[1].tool_calls
    assert [call["function"]["name"] for call in calls] == [
        "exec_command",
        "multi_agent_v1__spawn_agent",
    ]
    assert messages[2].tool_call_id == "call_1" and messages[2].content == "a.py"
    assert json.loads(messages[4].tool_calls[0]["function"]["arguments"]) == {"input": "*** Begin"}
    custom = view.canonical.tools[2].function
    assert custom["parameters"]["required"] == ["input"]
    assert view.translation_blocker is None


@pytest.mark.parametrize(
    ("body_update", "message"),
    [
        ({"previous_response_id": "resp_1"}, "previous_response_id"),
        ({"background": True}, "background"),
        ({"input": [{"type": "item_reference", "id": "msg_9"}]}, "item_reference"),
        (
            {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "f1"}]}]},
            "file references",
        ),
        ({"tools": [{"type": "file_search", "vector_store_ids": ["vs_1"]}]}, "file_search"),
        ({"tools": [{"type": "mcp", "server_label": "x", "server_url": "https://x"}]}, "MCP"),
        (
            {"tools": [{"type": "shell", "environment": {"type": "container_auto"}}]},
            "'shell'",
        ),
    ],
)
def test_policy_view_rejects_provider_account_objects(
    body_update: dict[str, Any], message: str
) -> None:
    with pytest.raises(InvalidRequestError) as exc_info:
        _view({"model": "m", "input": "hi", **body_update})

    assert message in exc_info.value.message


def test_native_only_items_block_translation() -> None:
    body = {
        "model": "m",
        "input": [
            {"type": "computer_call_output", "call_id": "c1", "output": {"type": "x"}},
            {"role": "user", "content": "go on"},
        ],
        "tools": [{"type": "computer_use_preview", "display_width": 10}],
    }

    view = _view(body)

    assert view.translation_blocker is not None
    assert "computer_use_preview" in view.translation_blocker
    native = view.native_request(view.canonical)
    assert native is not None and native.translation_blocker == view.translation_blocker


def test_native_usage_bills_like_openai_chat() -> None:
    usage = native_responses_usage(
        {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 100},
            "output_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 10},
            "total_tokens": 150,
        }
    )

    # Chat completions on OpenAI bill cached input at the full price; so does this hop.
    assert usage == {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
    with pytest.raises(ProxyError):
        native_responses_usage({"input_tokens": 1, "output_tokens": 1, "total_tokens": 5})
    with pytest.raises(ProxyError):
        native_responses_usage(
            {"input_tokens": 1, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 2}}
        )


def test_canonical_view_of_native_response_has_tool_calls() -> None:
    payload = {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "model": "gpt-5",
        "output": [
            {"type": "reasoning", "id": "rs", "summary": [{"type": "summary_text", "text": "hm"}]},
            {
                "type": "function_call",
                "call_id": "call_9",
                "name": "exec_command",
                "arguments": '{"cmd":"ls"}',
            },
        ],
        "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    }

    canonical = canonical_from_native_response(payload, "m")

    choice = canonical.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.tool_calls[0]["id"] == "call_9"
    assert choice.message.reasoning_content == "hm"
    incomplete = {**payload, "status": "incomplete", "output": []}
    incomplete["incomplete_details"] = {"reason": "max_output_tokens"}
    assert canonical_from_native_response(incomplete, "m").choices[0].finish_reason == "length"


async def _relay(events: list[dict[str, Any]]) -> list[Any]:
    adapter = OpenAINativeResponsesAdapter(
        OpenAIAdapter(httpx.AsyncClient()),
        NativeResponsesRequest(body={}),
        classify_failure=lambda details: None,
    )
    observed: list[int | None] = []

    async def lines():
        for event in events:
            yield f"event: {event['type']}"
            yield f"data: {json.dumps(event)}"
            yield ""

    frames = [
        frame
        async for frame in adapter.translate_stream(
            lines(), model_name="m", output_observer=observed.append
        )
    ]
    return [frames, observed]


@pytest.mark.asyncio
async def test_stream_relay_keeps_native_events_and_reports_usage() -> None:
    call = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "exec_command",
        "arguments": '{"cmd":"ls"}',
    }
    usage = {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13}
    events = [
        {"type": "response.created", "response": {"id": "resp_1", "model": "gpt-5"}},
        {"type": "response.output_text.delta", "delta": "Hi"},
        {"type": "response.function_call_arguments.delta", "delta": '{"cmd"'},
        {"type": "response.output_item.done", "output_index": 1, "item": call},
        {"type": "response.completed", "response": {"status": "completed", "usage": usage}},
    ]

    frames, observed = await _relay(events)

    assert [frame.native.split("\n")[0] for frame in frames if frame.native] == [
        f"event: {event['type']}" for event in events
    ]
    chunks = [json.loads(frame[len("data: ") :]) for frame in frames[:-1]]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[1]["choices"][0]["delta"] == {"content": "Hi"}
    assert chunks[3]["choices"][0]["delta"]["tool_calls"][0]["id"] == "call_1"
    assert chunks[4]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[4]["usage"]["total_tokens"] == 13
    # The completed event is held for the terminal frame, sent after accounting.
    assert frames[-1] == "data: [DONE]"
    assert frames[-1].native.startswith("event: response.completed")
    assert observed == [4]


@pytest.mark.asyncio
async def test_stream_relay_maps_failed_event_and_rejects_missing_created() -> None:
    failed = {"type": "response.failed", "response": {"error": {"code": "server_error"}}}
    with pytest.raises(ProxyError):
        await _relay([{"type": "response.created", "response": {}}, failed])
    with pytest.raises(ProxyError):
        await _relay([{"type": "response.output_text.delta", "delta": "x"}])


def test_native_base_detection() -> None:
    assert openai_base_serves_responses("https://api.openai.com/v1")
    assert not openai_base_serves_responses("https://api.groq.com/openai/v1")
    assert not openai_base_serves_responses("http://vllm:8000/v1")
    assert azure_base_serves_responses("https://x.openai.azure.com/openai/v1/")
    assert not azure_base_serves_responses("https://x.openai.azure.com/openai/deployments/d")


def test_native_defaults_use_responses_names() -> None:
    adapter = OpenAINativeResponsesAdapter(
        OpenAIAdapter(httpx.AsyncClient()),
        NativeResponsesRequest(body={}),
        classify_failure=lambda details: None,
    )
    payload: dict[str, Any] = {"reasoning": {"summary": "auto"}}

    adapter.apply_default_params(
        payload,
        {
            "default_params": {
                "max_tokens": 512,
                "temperature": 0.2,
                "presence_penalty": 1,
                "reasoning_effort": "high",
            }
        },
    )

    assert payload == {
        "reasoning": {"summary": "auto", "effort": "high"},
        "max_output_tokens": 512,
        "temperature": 0.2,
    }


def _shape() -> ResponsesShape:
    view = _view(
        {
            "model": "m",
            "input": "hi",
            "tools": [
                CODEX_BODY["tools"][1],
                {"type": "custom", "name": "apply_patch"},
                CODEX_BODY["tools"][0],
            ],
        }
    )
    return ResponsesShape.from_body(view.body, view.tools)


def test_chat_response_becomes_responses_output_items() -> None:
    chat = {
        "id": "chatcmpl-1",
        "created": 1700000000,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "think",
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "function": {"name": "multi_agent_v1__spawn_agent", "arguments": "{}"},
                        },
                        {
                            "id": "call_b",
                            "function": {"name": "apply_patch", "arguments": '{"input":"P"}'},
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt_tokens_cached": 4,
        },
    }

    response = chat_response_to_responses_response(chat, _shape())

    assert response["object"] == "response" and response["status"] == "completed"
    types = [item["type"] for item in response["output"]]
    assert types == ["reasoning", "function_call", "custom_tool_call"]
    spawn = response["output"][1]
    assert (spawn["name"], spawn["namespace"], spawn["call_id"]) == (
        "spawn_agent",
        "multi_agent_v1",
        "call_a",
    )
    assert response["output"][2]["input"] == "P"
    assert response["usage"] == {
        "input_tokens": 10,
        "input_tokens_details": {"cached_tokens": 4},
        "output_tokens": 5,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 15,
    }


def _events(text: str | None) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :])
        for line in (text or "").split("\n")
        if line.startswith("data: ")
    ]


def test_stream_translator_emits_responses_event_lifecycle() -> None:
    translator = ResponsesStreamTranslator(_shape())

    def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
        choices = [{"index": 0, "delta": delta, "finish_reason": finish}]
        return "data: " + json.dumps({"choices": choices, **extra})

    events: list[dict[str, Any]] = []
    for line in (
        chunk({"role": "assistant"}),
        chunk({"content": "Let me run it."}),
        chunk({"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "exec_command"}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"cmd":'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"ls"}'}}]}),
        chunk({}, "tool_calls"),
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
        "data: [DONE]",
    ):
        events.extend(_events(translator.translate_line(line)))

    assert [event["type"] for event in events] == [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.output_item.added",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
    ]
    assert [event["sequence_number"] for event in events] == list(range(len(events)))
    completed = events[-1]["response"]
    assert [item["type"] for item in completed["output"]] == ["message", "function_call"]
    assert completed["output"][1]["arguments"] == '{"cmd":"ls"}'
    assert completed["usage"]["total_tokens"] == 5


def test_stream_translator_reports_length_as_incomplete_and_errors_as_failed() -> None:
    translator = ResponsesStreamTranslator(_shape())
    translator.translate_line('data: {"choices":[{"delta":{"content":"a"},"index":0}]}')
    translator.translate_line('data: {"choices":[{"delta":{},"finish_reason":"length"}]}')

    final = _events(translator.translate_line("data: [DONE]"))[-1]

    assert final["type"] == "response.incomplete"
    assert final["response"]["incomplete_details"] == {"reason": "max_output_tokens"}
    failed = _events(ResponsesStreamTranslator(_shape()).error_event(ProxyError("boom")))
    assert failed[-1]["type"] == "response.failed"
    assert failed[-1]["response"]["error"]["message"] == "boom"
