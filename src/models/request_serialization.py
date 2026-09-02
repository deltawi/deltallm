from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from src.models.requests import AssistantChatMessage, ChatCompletionRequest


def dump_request_for_preflight(payload: BaseModel) -> dict[str, Any]:
    """Serialize request data without losing explicit nulls or adding defaults."""
    return payload.model_dump(mode="python", exclude_unset=True)


def dump_openai_chat_request(payload: ChatCompletionRequest) -> dict[str, Any]:
    """Serialize chat data while preserving explicit-null assistant content."""
    data = payload.model_dump(mode="python", exclude_none=True)
    messages = data["messages"]
    if not isinstance(messages, list) or len(messages) != len(payload.messages):
        raise RuntimeError("serialized chat messages do not match the canonical request")

    for source, serialized in zip(payload.messages, messages, strict=True):
        if not isinstance(serialized, dict):
            raise RuntimeError("serialized chat message is not an object")
        if (
            isinstance(source, AssistantChatMessage)
            and source.content is None
            and "content" in source.model_fields_set
        ):
            serialized["content"] = None
    return data
