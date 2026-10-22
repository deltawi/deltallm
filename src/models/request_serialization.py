from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from src.models.requests import AssistantChatMessage, ChatCompletionRequest, ToolChoice


def dump_request_for_preflight(payload: BaseModel) -> dict[str, Any]:
    """Serialize request data without losing explicit nulls or adding defaults."""
    return payload.model_dump(mode="python", exclude_unset=True)


def dump_declared_request_fields(payload: BaseModel) -> dict[str, Any]:
    """Serialize only declared fields, so stored request fingerprints stay stable."""
    return payload.model_dump(mode="json", exclude=set(payload.model_extra or ()))


def dump_openai_chat_request(payload: ChatCompletionRequest) -> dict[str, Any]:
    """Serialize client-sent chat data while preserving explicit-null assistant content.

    Field defaults are not sent: the provider's defaults and configured model defaults
    apply to anything the client left out.
    """
    data = payload.model_dump(mode="python", exclude_unset=True, exclude_none=True)
    # Protocol discriminators are required even when they came from typed defaults.
    if payload.tools:
        data["tools"] = [
            {**serialized, "type": tool.type}
            for tool, serialized in zip(payload.tools, data["tools"], strict=True)
        ]
    if isinstance(payload.tool_choice, ToolChoice):
        data["tool_choice"] = {**data["tool_choice"], "type": payload.tool_choice.type}
    messages = data["messages"]
    if not isinstance(messages, list) or len(messages) != len(payload.messages):
        raise RuntimeError("serialized chat messages do not match the canonical request")

    for source, serialized in zip(payload.messages, messages, strict=True):
        if not isinstance(serialized, dict):
            raise RuntimeError("serialized chat message is not an object")
        # Legacy adapters retain their previous request contract. Providers that
        # support reasoning history serialize it explicitly in their adapter.
        serialized.pop("reasoning_content", None)
        serialized.pop("reasoning_details", None)
        if (
            isinstance(source, AssistantChatMessage)
            and source.content is None
            and "content" in source.model_fields_set
        ):
            serialized["content"] = None
    return data
