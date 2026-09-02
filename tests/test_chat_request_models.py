from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.request_serialization import (
    dump_openai_chat_request,
    dump_request_for_preflight,
)
from src.models.requests import AssistantChatMessage, ChatCompletionRequest


def _tool_call() -> dict[str, object]:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search", "arguments": "{}"},
    }


@pytest.mark.parametrize(
    "include_content",
    [pytest.param(False, id="omitted"), pytest.param(True, id="null")],
)
def test_assistant_tool_call_content_may_be_omitted_or_null(include_content: bool) -> None:
    assistant: dict[str, object] = {"role": "assistant", "tool_calls": [_tool_call()]}
    if include_content:
        assistant["content"] = None

    request = ChatCompletionRequest.model_validate(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "search"},
                assistant,
                {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            ],
        }
    )

    parsed = request.messages[1]
    assert isinstance(parsed, AssistantChatMessage)
    assert parsed.content is None
    assert ("content" in parsed.model_fields_set) is include_content


@pytest.mark.parametrize(
    "assistant",
    [
        {"role": "assistant"},
        {"role": "assistant", "content": None},
        {"role": "assistant", "tool_calls": []},
        {"role": "assistant", "content": None, "tool_calls": []},
    ],
)
def test_assistant_requires_content_or_non_empty_tool_calls(
    assistant: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="content or non-empty tool_calls"):
        ChatCompletionRequest.model_validate({"model": "gpt-4o-mini", "messages": [assistant]})


@pytest.mark.parametrize("role", ["system", "user", "tool"])
@pytest.mark.parametrize("content", [pytest.param("omitted"), pytest.param(None)])
def test_non_assistant_content_remains_required(role: str, content: object) -> None:
    message: dict[str, object] = {"role": role}
    if role == "tool":
        message["tool_call_id"] = "call_1"
    if content != "omitted":
        message["content"] = content

    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate({"model": "gpt-4o-mini", "messages": [message]})


@pytest.mark.parametrize(
    "include_content",
    [pytest.param(False, id="omitted"), pytest.param(True, id="null")],
)
def test_chat_serializers_preserve_assistant_content_presence(include_content: bool) -> None:
    assistant: dict[str, object] = {"role": "assistant", "tool_calls": [_tool_call()]}
    if include_content:
        assistant["content"] = None
    request = ChatCompletionRequest.model_validate(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "search"},
                assistant,
                {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            ],
        }
    )

    preflight = dump_request_for_preflight(request)
    upstream = dump_openai_chat_request(request)
    assert ("content" in preflight["messages"][1]) is include_content
    assert ("content" in upstream["messages"][1]) is include_content
    if include_content:
        assert preflight["messages"][1]["content"] is None
        assert upstream["messages"][1]["content"] is None
