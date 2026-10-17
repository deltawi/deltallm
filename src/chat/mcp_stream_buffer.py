"""Per-hop SSE filter for streamed chat with gateway-run MCP tools.

The buffer forwards text deltas at once and holds tool-call deltas and the
finish chunk until the hop ends. Only then is it known whether the gateway
runs the tool calls or the client receives them.
"""

from __future__ import annotations

import json
from typing import Any

from src.providers.openai_stream_contract import OpenAIStreamDeltaField

_MAX_REASONING_DETAILS = 128
_MERGED_REASONING_TEXT_KEYS = ("text", "summary", "data", "signature")


class MCPStreamHopBuffer:
    def __init__(self, *, completion_id: str | None) -> None:
        # Later hops use the id of the first hop, so the client sees one completion.
        self.completion_id = completion_id
        self._rewrite_id = completion_id is not None
        self.created: Any = None
        self.model: Any = None
        self._content: list[str] = []
        self._reasoning: list[str] = []
        self._reasoning_details: dict[Any, dict[str, Any]] = {}
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._finish_chunk: dict[str, Any] | None = None

    def filter(self, line: str) -> str | None:
        """Return the line to forward now, or None to hold or drop it."""
        if not line.startswith("data:"):
            return line
        try:
            chunk = json.loads(line[len("data:") :].strip())
        except json.JSONDecodeError:
            return line
        if not isinstance(chunk, dict):
            return line
        changed = self._observe_identity(chunk)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return _encode(chunk) if changed else line
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta")
        if isinstance(delta, dict):
            self._observe_delta(delta)
            tool_calls = delta.get(OpenAIStreamDeltaField.TOOL_CALLS.value)
            if tool_calls is not None:
                self._absorb_tool_calls(tool_calls)
                delta = {
                    key: value
                    for key, value in delta.items()
                    if key != OpenAIStreamDeltaField.TOOL_CALLS.value
                }
                choice = {**choice, "delta": delta}
                chunk = {**chunk, "choices": [choice, *choices[1:]]}
                changed = True
        if choice.get("finish_reason") is not None:
            self._finish_chunk = {**chunk, "choices": [{**choice, "delta": {}}, *choices[1:]]}
            if not _has_delta(choice):
                return None
            chunk = {**chunk, "choices": [{**choice, "finish_reason": None}, *choices[1:]]}
            return _encode(chunk)
        if changed and not _has_delta(choice):
            return None
        return _encode(chunk) if changed else line

    def tool_calls(self) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for index in sorted(self._tool_calls):
            entry = self._tool_calls[index]
            calls.append(
                {
                    "id": entry["id"] or f"call_{index}",
                    "type": entry["type"] or "function",
                    "function": {
                        "name": entry["name"],
                        # Some adapters stream "" for a tool without input.
                        "arguments": entry["arguments"] or "{}",
                    },
                }
            )
        return calls

    def assistant_response(self) -> dict[str, Any]:
        """Return the hop as a non-streaming response shape for the tool loop."""
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self._content) or None,
            "tool_calls": self.tool_calls(),
        }
        if self._reasoning:
            message["reasoning_content"] = "".join(self._reasoning)
        if self._reasoning_details:
            message["reasoning_details"] = list(self._reasoning_details.values())
        return {"choices": [{"index": 0, "message": message}]}

    def client_lines(self) -> list[str]:
        """Return the held frames when the client, not the gateway, gets the turn."""
        lines: list[str] = []
        calls = self.tool_calls()
        if calls:
            lines.append(
                _encode(
                    self._chunk(
                        {
                            OpenAIStreamDeltaField.TOOL_CALLS.value: [
                                {"index": index, **call} for index, call in enumerate(calls)
                            ]
                        }
                    )
                )
            )
        if self._finish_chunk is not None:
            lines.append(_encode(self._finish_chunk))
        return lines

    def usage_line(self, usage: dict[str, Any]) -> str:
        return _encode({**self._chunk(None), "choices": [], "usage": usage})

    def _chunk(self, delta: dict[str, Any] | None) -> dict[str, Any]:
        chunk: dict[str, Any] = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
        }
        if delta is not None:
            chunk["choices"] = [{"index": 0, "delta": delta, "finish_reason": None}]
        return chunk

    def _observe_identity(self, chunk: dict[str, Any]) -> bool:
        if self.created is None:
            self.created = chunk.get("created")
        if self.model is None:
            self.model = chunk.get("model")
        chunk_id = chunk.get("id")
        if self.completion_id is None:
            self.completion_id = chunk_id if isinstance(chunk_id, str) else None
            return False
        if self._rewrite_id and chunk_id != self.completion_id:
            chunk["id"] = self.completion_id
            return True
        return False

    def _observe_delta(self, delta: dict[str, Any]) -> None:
        content = delta.get(OpenAIStreamDeltaField.CONTENT.value)
        if isinstance(content, str):
            self._content.append(content)
        reasoning = delta.get(OpenAIStreamDeltaField.REASONING_CONTENT.value)
        if isinstance(reasoning, str):
            self._reasoning.append(reasoning)
        details = delta.get(OpenAIStreamDeltaField.REASONING_DETAILS.value)
        if isinstance(details, list):
            for item in details:
                if isinstance(item, dict):
                    self._merge_reasoning_detail(item)

    def _merge_reasoning_detail(self, item: dict[str, Any]) -> None:
        key = item.get("index", len(self._reasoning_details))
        current = self._reasoning_details.get(key)
        if current is None:
            if len(self._reasoning_details) >= _MAX_REASONING_DETAILS:
                return
            self._reasoning_details[key] = dict(item)
            return
        for name, value in item.items():
            previous = current.get(name)
            if (
                name in _MERGED_REASONING_TEXT_KEYS
                and isinstance(value, str)
                and isinstance(previous, str)
            ):
                current[name] = previous + value
            else:
                current[name] = value

    def _absorb_tool_calls(self, tool_calls: Any) -> None:
        if not isinstance(tool_calls, list):
            return
        for position, item in enumerate(tool_calls):
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                index = position
            entry = self._tool_calls.setdefault(
                index, {"id": None, "type": None, "name": "", "arguments": ""}
            )
            if isinstance(item.get("id"), str) and item["id"]:
                entry["id"] = item["id"]
            if isinstance(item.get("type"), str) and item["type"]:
                entry["type"] = item["type"]
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            # Most providers send the name once; some repeat it on every delta.
            if isinstance(name, str) and name and name != entry["name"]:
                entry["name"] += name
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                entry["arguments"] += arguments
            elif isinstance(arguments, dict):
                entry["arguments"] = json.dumps(arguments, separators=(",", ":"))


def _has_delta(choice: dict[str, Any]) -> bool:
    delta = choice.get("delta")
    return isinstance(delta, dict) and any(value not in (None, "") for value in delta.values())


def _encode(chunk: dict[str, Any]) -> str:
    return "data: " + json.dumps(chunk, separators=(",", ":"))
