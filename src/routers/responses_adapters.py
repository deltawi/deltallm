"""Chat completion results in the OpenAI Responses format, for the translation hop.

The native Responses hop returns the provider's own response and events. Every other
deployment answers in chat format; this module builds the Responses object and the
Responses SSE event stream (`response.created` ... `response.completed`) from it.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.models.errors import ProxyError
from src.routers.responses_request_view import ResponsesTool, chat_tool_name

# Request fields that a Responses object repeats back to the client.
_ECHOED_FIELDS = (
    "instructions",
    "max_output_tokens",
    "parallel_tool_calls",
    "reasoning",
    "store",
    "temperature",
    "text",
    "tool_choice",
    "tools",
    "top_p",
    "truncation",
    "user",
)
_INCOMPLETE_REASONS = {"length": "max_output_tokens", "content_filter": "content_filter"}


@dataclass(frozen=True, slots=True)
class ResponsesShape:
    """What the translation needs from the request to answer in its terms."""

    model: str
    echo: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, ResponsesTool] = field(default_factory=dict)

    @classmethod
    def from_body(cls, body: dict[str, Any], tools: dict[str, ResponsesTool]) -> ResponsesShape:
        return cls(
            model=str(body.get("model") or ""),
            echo={name: body[name] for name in _ECHOED_FIELDS if name in body},
            tools=tools,
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def responses_usage(usage: object) -> dict[str, Any]:
    usage = usage if isinstance(usage, dict) else {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = usage.get("prompt_tokens_cached")
    prompt_details = usage.get("prompt_tokens_details")
    if cached is None and isinstance(prompt_details, dict):
        cached = prompt_details.get("cached_tokens")
    completion_details = usage.get("completion_tokens_details")
    reasoning = (
        completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else 0
    )
    return {
        "input_tokens": prompt,
        "input_tokens_details": {"cached_tokens": int(cached or 0)},
        "output_tokens": completion,
        "output_tokens_details": {"reasoning_tokens": int(reasoning or 0)},
        "total_tokens": prompt + completion,
    }


def _call_item(shape: ResponsesShape, call: dict[str, Any], *, status: str) -> dict[str, Any]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    chat_name = str(function.get("name") or "")
    arguments = str(function.get("arguments") or "")
    call_id = str(call.get("id") or _new_id("call"))
    tool = shape.tools.get(chat_name)
    name = tool.name if tool is not None else chat_name
    if tool is not None and tool.kind == "custom":
        item: dict[str, Any] = {
            "type": "custom_tool_call",
            "id": f"ctc_{call_id}",
            "call_id": call_id,
            "name": name,
            "input": _custom_input(arguments),
            "status": status,
        }
    else:
        item = {
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
            "status": status,
        }
    if tool is not None and tool.namespace:
        item["namespace"] = tool.namespace
    return item


def _custom_input(arguments: str) -> str:
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments
    if isinstance(parsed, dict) and isinstance(parsed.get("input"), str):
        return parsed["input"]
    return arguments


def _message_item(item_id: str, text: str, *, status: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": item_id,
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]
        if status == "completed"
        else [],
    }


def _reasoning_item(item_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "reasoning",
        "id": item_id,
        "summary": [],
        "content": [{"type": "reasoning_text", "text": text}],
    }


def _response_object(
    shape: ResponsesShape,
    *,
    response_id: str,
    created_at: int,
    status: str,
    output: list[dict[str, Any]],
    usage: dict[str, Any] | None,
    finish_reason: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incomplete = _INCOMPLETE_REASONS.get(str(finish_reason or ""))
    if status == "completed" and incomplete is not None:
        status = "incomplete"
    response: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": error,
        "incomplete_details": {"reason": incomplete} if status == "incomplete" else None,
        "model": shape.model,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": usage,
    }
    response.update(shape.echo)
    return response


def chat_response_to_responses_response(
    chat_payload: dict[str, Any], shape: ResponsesShape
) -> dict[str, Any]:
    choice = ((chat_payload.get("choices") or [{}])[0]) or {}
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        text = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    else:
        text = str(content or "")
    output: list[dict[str, Any]] = []
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        output.append(_reasoning_item(_new_id("rs"), reasoning))
    tool_calls = [call for call in message.get("tool_calls") or [] if isinstance(call, dict)]
    if text or not tool_calls:
        output.append(_message_item(_new_id("msg"), text, status="completed"))
    output.extend(_call_item(shape, call, status="completed") for call in tool_calls)
    return _response_object(
        shape,
        response_id=_new_id("resp"),
        created_at=int(chat_payload.get("created") or time.time()),
        status="completed",
        output=output,
        usage=responses_usage(chat_payload.get("usage")),
        finish_reason=choice.get("finish_reason"),
    )


@dataclass(slots=True)
class _OpenItem:
    output_index: int
    item: dict[str, Any]
    text: str = ""


class ResponsesStreamTranslator:
    """Translates canonical `chat.completion.chunk` SSE lines into Responses SSE events.

    One instance serves one response. It tracks open output items across lines.
    """

    def __init__(self, shape: ResponsesShape) -> None:
        self._shape = shape
        self._id = _new_id("resp")
        self._created_at = int(time.time())
        self._sequence = 0
        self._started = False
        self._finished = False
        self._output: list[dict[str, Any] | None] = []
        self._text: _OpenItem | None = None
        self._reasoning: _OpenItem | None = None
        self._calls: dict[int, _OpenItem] = {}
        self._finish_reason: str | None = None
        self._usage: dict[str, Any] | None = None

    def translate_line(self, line: str) -> str | None:
        if not line.startswith("data:"):
            return None
        payload = line[len("data:") :].strip()
        if not payload:
            return None
        if payload == "[DONE]":
            return self._join(self._finish())
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return None
        events = self._start()
        if isinstance(chunk.get("usage"), dict):
            self._usage = chunk["usage"]
        for choice in (chunk.get("choices") or [])[:1]:
            delta = choice.get("delta") or {}
            if choice.get("finish_reason"):
                self._finish_reason = choice["finish_reason"]
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                events.extend(self._reasoning_delta(reasoning))
            text = delta.get("content")
            if isinstance(text, str) and text:
                events.extend(self._text_delta(text))
            for call in delta.get("tool_calls") or []:
                if isinstance(call, dict):
                    events.extend(self._call_delta(call))
        return self._join(events)

    def error_event(self, exc: ProxyError) -> str:
        """The terminal event for a stream that fails after it started."""

        events = self._start()
        response = self._response(
            "failed",
            usage=None,
            error={"code": exc.code or exc.error_type, "message": exc.message},
        )
        events.append(self._event("response.failed", response=response))
        return self._join(events) or ""

    def _join(self, events: list[str]) -> str | None:
        return "\n\n".join(events) if events else None

    def _event(self, event_type: str, **data: Any) -> str:
        body = {"type": event_type, "sequence_number": self._sequence, **data}
        self._sequence += 1
        return f"event: {event_type}\ndata: {json.dumps(body, separators=(',', ':'))}"

    def _response(self, status: str, **kwargs: Any) -> dict[str, Any]:
        return _response_object(
            self._shape,
            response_id=self._id,
            created_at=self._created_at,
            status=status,
            output=[item for item in self._output if item is not None],
            **kwargs,
        )

    def _start(self) -> list[str]:
        if self._started:
            return []
        self._started = True
        response = self._response("in_progress", usage=None)
        return [
            self._event("response.created", response=response),
            self._event("response.in_progress", response=response),
        ]

    def _open(self, item: dict[str, Any]) -> tuple[_OpenItem, str]:
        opened = _OpenItem(output_index=len(self._output), item=item)
        self._output.append(None)
        return opened, self._event(
            "response.output_item.added", output_index=opened.output_index, item=item
        )

    def _done(self, opened: _OpenItem, item: dict[str, Any]) -> str:
        self._output[opened.output_index] = item
        return self._event("response.output_item.done", output_index=opened.output_index, item=item)

    def _reasoning_delta(self, text: str) -> list[str]:
        events: list[str] = []
        if self._reasoning is None:
            events.extend(self._close_text())
            item = {"type": "reasoning", "id": _new_id("rs"), "summary": [], "content": []}
            self._reasoning, added = self._open(item)
            events.append(added)
        self._reasoning.text += text
        events.append(
            self._event(
                "response.reasoning_text.delta",
                item_id=self._reasoning.item["id"],
                output_index=self._reasoning.output_index,
                content_index=0,
                delta=text,
            )
        )
        return events

    def _text_delta(self, text: str) -> list[str]:
        events: list[str] = []
        if self._text is None:
            events.extend(self._close_reasoning())
            item = _message_item(_new_id("msg"), "", status="in_progress")
            self._text, added = self._open(item)
            events.append(added)
            events.append(
                self._event(
                    "response.content_part.added",
                    item_id=item["id"],
                    output_index=self._text.output_index,
                    content_index=0,
                    part={"type": "output_text", "text": "", "annotations": [], "logprobs": []},
                )
            )
        self._text.text += text
        events.append(
            self._event(
                "response.output_text.delta",
                item_id=self._text.item["id"],
                output_index=self._text.output_index,
                content_index=0,
                delta=text,
                logprobs=[],
            )
        )
        return events

    def _call_delta(self, call: dict[str, Any]) -> list[str]:
        index = int(call.get("index") or 0)
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        events: list[str] = []
        opened = self._calls.get(index)
        if opened is None:
            events.extend(self._close_reasoning())
            events.extend(self._close_text())
            start = {"id": call.get("id"), "function": {"name": function.get("name")}}
            item = _call_item(self._shape, start, status="in_progress")
            if item["type"] == "function_call":
                item["arguments"] = ""
            else:
                item["input"] = ""
            opened, added = self._open(item)
            self._calls[index] = opened
            events.append(added)
        fragment = function.get("arguments")
        if isinstance(fragment, str) and fragment:
            opened.text += fragment
            if opened.item["type"] == "function_call":
                # Custom tool input is JSON-wrapped on the chat side; it is sent whole at
                # the end of the call.
                events.append(
                    self._event(
                        "response.function_call_arguments.delta",
                        item_id=opened.item["id"],
                        output_index=opened.output_index,
                        delta=fragment,
                    )
                )
        return events

    def _close_reasoning(self) -> list[str]:
        opened, self._reasoning = self._reasoning, None
        if opened is None:
            return []
        item = _reasoning_item(opened.item["id"], opened.text)
        return [
            self._event(
                "response.reasoning_text.done",
                item_id=item["id"],
                output_index=opened.output_index,
                content_index=0,
                text=opened.text,
            ),
            self._done(opened, item),
        ]

    def _close_text(self) -> list[str]:
        opened, self._text = self._text, None
        if opened is None:
            return []
        item = _message_item(opened.item["id"], opened.text, status="completed")
        common = {"item_id": item["id"], "output_index": opened.output_index, "content_index": 0}
        return [
            self._event("response.output_text.done", text=opened.text, logprobs=[], **common),
            self._event("response.content_part.done", part=item["content"][0], **common),
            self._done(opened, item),
        ]

    def _close_call(self, opened: _OpenItem) -> list[str]:
        start = opened.item
        call = {
            "id": start["call_id"],
            "function": {"name": _chat_name(start), "arguments": opened.text},
        }
        item = _call_item(self._shape, call, status="completed")
        common = {"item_id": item["id"], "output_index": opened.output_index}
        if item["type"] == "function_call":
            events = [
                self._event(
                    "response.function_call_arguments.done", arguments=item["arguments"], **common
                )
            ]
        else:
            events = [
                self._event("response.custom_tool_call_input.delta", delta=item["input"], **common),
                self._event("response.custom_tool_call_input.done", input=item["input"], **common),
            ]
        events.append(self._done(opened, item))
        return events

    def _finish(self) -> list[str]:
        if self._finished:
            return []
        self._finished = True
        events = self._start()
        events.extend(self._close_reasoning())
        events.extend(self._close_text())
        for opened in self._calls.values():
            events.extend(self._close_call(opened))
        self._calls = {}
        response = self._response(
            "completed", usage=responses_usage(self._usage), finish_reason=self._finish_reason
        )
        terminal = (
            "response.completed" if response["status"] == "completed" else "response.incomplete"
        )
        events.append(self._event(terminal, response=response))
        return events


def _chat_name(item: dict[str, Any]) -> str:
    namespace = item.get("namespace")
    return chat_tool_name(str(item.get("name") or ""), namespace)
