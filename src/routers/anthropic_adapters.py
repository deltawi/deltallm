from __future__ import annotations

import json
import uuid
from typing import Any

from src.models.requests import AnthropicMessage, AnthropicMessagesRequest, ChatCompletionRequest

_FINISH_REASON_TO_STOP_REASON = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}

_REQUEST_FIELD_MAP = {
    "temperature": "temperature",
    "top_p": "top_p",
    "stream": "stream",
    "metadata": "metadata",
}


def _extract_text_blocks(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _extract_tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _extract_text_blocks(content)
    return ""


def _anthropic_message_to_chat_messages(message: AnthropicMessage) -> list[dict[str, Any]]:
    role = message.role
    content = message.content
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_messages: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block_type == "tool_use" and role == "assistant":
            tool_calls.append(
                {
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
        elif block_type == "tool_result" and role == "user":
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or ""),
                    "content": _extract_tool_result_text(block.get("content")),
                }
            )

    messages: list[dict[str, Any]] = list(tool_messages)
    if text_parts or tool_calls:
        entry: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
        if tool_calls:
            entry["tool_calls"] = tool_calls
        messages.append(entry)
    if not messages:
        messages.append({"role": role, "content": ""})
    return messages


def _anthropic_tool_to_function_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        },
    }


def _anthropic_tool_choice_to_chat(tool_choice: dict[str, Any]) -> Any:
    choice_type = tool_choice.get("type")
    if choice_type == "any":
        return "required"
    if choice_type == "tool":
        return {"type": "function", "function": {"name": str(tool_choice.get("name") or "")}}
    if choice_type == "none":
        return "none"
    return "auto"


def anthropic_messages_to_chat_request(payload: AnthropicMessagesRequest) -> ChatCompletionRequest:
    messages: list[dict[str, Any]] = []
    if payload.system:
        system_text = payload.system if isinstance(payload.system, str) else _extract_text_blocks(payload.system)
        if system_text:
            messages.append({"role": "system", "content": system_text})
    for message in payload.messages:
        messages.extend(_anthropic_message_to_chat_messages(message))

    data: dict[str, Any] = {
        "model": payload.model,
        "messages": messages,
        "max_tokens": payload.max_tokens,
    }
    fields_set = payload.model_fields_set
    for source_field, target_field in _REQUEST_FIELD_MAP.items():
        if source_field in fields_set:
            data[target_field] = getattr(payload, source_field)
    if payload.stop_sequences:
        data["stop"] = payload.stop_sequences
    if payload.tools:
        data["tools"] = [_anthropic_tool_to_function_tool(tool) for tool in payload.tools]
    if payload.tool_choice:
        data["tool_choice"] = _anthropic_tool_choice_to_chat(payload.tool_choice)
    return ChatCompletionRequest.model_validate(data)


def chat_response_to_anthropic_response(chat_payload: dict[str, Any]) -> dict[str, Any]:
    first_choice = ((chat_payload.get("choices") or [{}])[0]) or {}
    message = first_choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        text = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    else:
        text = str(content or "")

    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get("id") or "",
                "name": function.get("name") or "",
                "input": arguments,
            }
        )

    stop_reason = _FINISH_REASON_TO_STOP_REASON.get(str(first_choice.get("finish_reason") or ""), "end_turn")
    usage = chat_payload.get("usage") or {}
    return {
        "id": str(chat_payload.get("id") or "") or f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": chat_payload.get("model"),
        "content": blocks or [{"type": "text", "text": ""}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


class AnthropicStreamTranslator:
    """Translates canonical `chat.completion.chunk` SSE lines into Anthropic Messages SSE events.

    One instance is scoped to a single request/response cycle since it tracks
    open content-block state across calls to `translate_line`.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self._message_id = f"msg_{uuid.uuid4().hex}"
        self._message_started = False
        self._finalized = False
        self._open_block_index: int | None = None
        self._next_block_index = 0
        self._tool_call_blocks: dict[int, int] = {}
        self._finish_reason: str | None = None
        self._usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_line(self, line: str) -> str | None:
        if not line.startswith("data:"):
            return None
        payload = line[len("data:") :].strip()
        if not payload:
            return None
        if payload == "[DONE]":
            return self._finalize()

        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return None

        events: list[str] = []
        if not self._message_started:
            events.append(self._message_start_event(chunk.get("model")))
            self._message_started = True

        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self._usage = {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            }

        choices = chunk.get("choices") or []
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                self._finish_reason = finish_reason

            text = delta.get("content")
            if isinstance(text, str) and text:
                events.extend(self._text_delta_events(text))

            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if isinstance(tool_call, dict):
                        events.extend(self._tool_call_delta_events(tool_call))

        return "\n\n".join(events) if events else None

    def _message_start_event(self, model: str | None) -> str:
        data = {
            "type": "message_start",
            "message": {
                "id": self._message_id,
                "type": "message",
                "role": "assistant",
                "model": model or self._model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
        return f"event: message_start\ndata: {json.dumps(data, separators=(',', ':'))}"

    def _open_text_block(self) -> list[str]:
        if self._open_block_index is not None:
            return []
        index = self._next_block_index
        self._next_block_index += 1
        self._open_block_index = index
        return [self._content_block_start_event(index, {"type": "text", "text": ""})]

    def _text_delta_events(self, text: str) -> list[str]:
        events = self._open_text_block()
        events.append(self._content_block_delta_event(self._open_block_index, {"type": "text_delta", "text": text}))
        return events

    def _tool_call_delta_events(self, tool_call: dict[str, Any]) -> list[str]:
        openai_index = int(tool_call.get("index") or 0)
        function = tool_call.get("function") or {}
        events: list[str] = []
        if openai_index not in self._tool_call_blocks:
            if self._open_block_index is not None:
                events.append(self._content_block_stop_event(self._open_block_index))
                self._open_block_index = None
            index = self._next_block_index
            self._next_block_index += 1
            self._tool_call_blocks[openai_index] = index
            self._open_block_index = index
            events.append(
                self._content_block_start_event(
                    index,
                    {
                        "type": "tool_use",
                        "id": tool_call.get("id") or f"toolu_{index}",
                        "name": function.get("name") or "",
                        "input": {},
                    },
                )
            )
        index = self._tool_call_blocks[openai_index]
        arguments_fragment = function.get("arguments")
        if isinstance(arguments_fragment, str) and arguments_fragment:
            events.append(self._content_block_delta_event(index, {"type": "input_json_delta", "partial_json": arguments_fragment}))
        return events

    def _content_block_start_event(self, index: int, block: dict[str, Any]) -> str:
        data = {"type": "content_block_start", "index": index, "content_block": block}
        return f"event: content_block_start\ndata: {json.dumps(data, separators=(',', ':'))}"

    def _content_block_delta_event(self, index: int, delta: dict[str, Any]) -> str:
        data = {"type": "content_block_delta", "index": index, "delta": delta}
        return f"event: content_block_delta\ndata: {json.dumps(data, separators=(',', ':'))}"

    def _content_block_stop_event(self, index: int) -> str:
        data = {"type": "content_block_stop", "index": index}
        return f"event: content_block_stop\ndata: {json.dumps(data, separators=(',', ':'))}"

    def _finalize(self) -> str | None:
        if self._finalized:
            return None
        self._finalized = True

        events: list[str] = []
        if not self._message_started:
            events.append(self._message_start_event(self._model))
            self._message_started = True
        if self._open_block_index is not None:
            events.append(self._content_block_stop_event(self._open_block_index))
            self._open_block_index = None

        stop_reason = _FINISH_REASON_TO_STOP_REASON.get(str(self._finish_reason or ""), "end_turn")
        message_delta = {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": self._usage,
        }
        events.append(f"event: message_delta\ndata: {json.dumps(message_delta, separators=(',', ':'))}")
        events.append(f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'}, separators=(',', ':'))}")
        return "\n\n".join(events)
