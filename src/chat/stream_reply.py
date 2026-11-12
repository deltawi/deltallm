"""Rebuild one assistant message from canonical ``chat.completion.chunk`` deltas.

Two owners use this accumulator:

- The MCP stream buffer needs the exact assistant turn of each hop to build the
  next hop. It runs without a size limit, because a cut turn would corrupt the
  follow-up request.
- The text audit keeps the reply of a streamed completion. It runs with a
  character limit on every retained string, so the reply size is bounded by the
  audit payload limit and not by the stream length. When the limit is reached,
  later data is dropped and the reply is marked ``truncated``.
"""

from __future__ import annotations

import json
from typing import Any
import weakref

from src.providers.openai_stream_contract import OpenAIStreamDeltaField

_MAX_REASONING_DETAILS = 128
# Bounded mode only: the MCP tool loop must see every call the model made.
_MAX_TOOL_CALLS = 128
_MERGED_REASONING_TEXT_KEYS = ("text", "summary", "data", "signature")
AUDIT_REPLY_MEMORY_MAX_CHARS = 64 * 1024 * 1024


class ReplyMemory:
    """A process-wide character budget shared by the bounded accumulators.

    Each accumulator has its own limit, but many streams can run at the same
    time. This budget caps the sum. When it is used up, a reply is cut and
    marked ``truncated``; the stream itself never waits or fails.
    """

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max(0, int(max_chars))
        self.used = 0

    def take(self, size: int) -> bool:
        if self.used + size > self.max_chars:
            return False
        self.used += size
        return True

    def give_back(self, held: list[int]) -> None:
        self.used = max(0, self.used - held[0])
        held[0] = 0


AUDIT_REPLY_MEMORY = ReplyMemory(AUDIT_REPLY_MEMORY_MAX_CHARS)


class StreamReplyAccumulator:
    def __init__(self, *, max_chars: int | None = None, memory: ReplyMemory | None = None) -> None:
        self._max_chars = max_chars
        self._chars = 0
        self._memory = memory
        self._held = [0]
        self._released = False
        # The share goes back when release() runs or when the accumulator is
        # freed, so a failed or cancelled stream cannot leak budget.
        self._release = (
            weakref.finalize(self, memory.give_back, self._held) if memory is not None else None
        )
        self.truncated = False
        self.saw_chunk = False
        self.response_id: str | None = None
        self.created: int | None = None
        self.model: str | None = None
        self.finish_reason: str | None = None
        self._content: list[str] = []
        self._reasoning: list[str] = []
        # Audit only: the MCP tool loop does not send refusals to the next hop.
        self._refusal: list[str] = []
        self._reasoning_details: dict[Any, dict[str, Any]] = {}
        self._tool_calls: dict[int, dict[str, Any]] = {}

    def add_chunk(self, chunk: dict[str, Any]) -> None:
        """Fold one parsed stream chunk in. Only choice 0 is kept, like the cache."""
        self.saw_chunk = True
        if self.response_id is None and isinstance(chunk.get("id"), str):
            self.response_id = chunk["id"]
        created = chunk.get("created")
        if self.created is None and isinstance(created, int) and not isinstance(created, bool):
            self.created = created
        if self.model is None and isinstance(chunk.get("model"), str):
            self.model = chunk["model"]
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            finish_reason = choice.get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason:
                self.finish_reason = finish_reason
            delta = choice.get("delta")
            if isinstance(delta, dict):
                self.add_delta(delta)

    def add_delta(self, delta: dict[str, Any]) -> None:
        content = delta.get(OpenAIStreamDeltaField.CONTENT.value)
        if isinstance(content, str):
            self._append(self._content, content)
        reasoning = delta.get(OpenAIStreamDeltaField.REASONING_CONTENT.value)
        if isinstance(reasoning, str):
            self._append(self._reasoning, reasoning)
        refusal = delta.get(OpenAIStreamDeltaField.REFUSAL.value)
        if isinstance(refusal, str):
            self._append(self._refusal, refusal)
        details = delta.get(OpenAIStreamDeltaField.REASONING_DETAILS.value)
        if isinstance(details, list):
            for item in details:
                if isinstance(item, dict):
                    self._merge_reasoning_detail(item)
        tool_calls = delta.get(OpenAIStreamDeltaField.TOOL_CALLS.value)
        if isinstance(tool_calls, list):
            self._absorb_tool_calls(tool_calls)

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

    def message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self._content) or None,
            "tool_calls": self.tool_calls(),
        }
        if self._reasoning:
            message["reasoning_content"] = "".join(self._reasoning)
        if self._reasoning_details:
            message["reasoning_details"] = list(self._reasoning_details.values())
        return message

    def completion(
        self, *, fallback_model: str, usage: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Return the reply as a non-streaming ``chat.completion`` body, or None."""
        if not self.saw_chunk:
            return None
        message = self.message()
        if not message["tool_calls"]:
            del message["tool_calls"]
        if self._refusal:
            message["refusal"] = "".join(self._refusal)
        body: dict[str, Any] = {
            "id": self.response_id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model or fallback_model,
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish_reason}],
            # Readers can tell a rebuilt stream reply from a provider response body.
            "stream": True,
        }
        if usage is not None:
            body["usage"] = dict(usage)
        if self.truncated:
            body["truncated"] = True
        return body

    def _append(self, parts: list[str], text: str) -> None:
        if not text or not self._reserve(len(text)):
            return
        parts.append(text)

    def release(self) -> None:
        """Give this reply's share of the shared budget back. Later data is dropped."""
        self._released = True
        if self._release is not None:
            self._release()

    def _reserve(self, size: int) -> bool:
        if self.truncated or self._released:
            return False
        if self._max_chars is not None and self._chars + size > self._max_chars:
            self.truncated = True
            return False
        if self._memory is not None:
            if not self._memory.take(size):
                self.truncated = True
                return False
            self._held[0] += size
        self._chars += size
        return True

    def _reserve_text(self, text: str) -> bool:
        return self._reserve(len(text))

    def _reserve_json(self, value: Any) -> bool:
        if self._max_chars is None:
            return True
        return self._reserve(_json_size(value))

    def _merge_reasoning_detail(self, item: dict[str, Any]) -> None:
        index = item.get("index", len(self._reasoning_details))
        # A JSON array or object is not hashable. Treat it as a missing index.
        if isinstance(index, (list, dict)):
            index = len(self._reasoning_details)
        current = self._reasoning_details.get(index)
        if current is None:
            if len(self._reasoning_details) >= _MAX_REASONING_DETAILS:
                return
            if not self._reserve_json(item):
                return
            self._reasoning_details[index] = dict(item)
            return
        for name, value in item.items():
            previous = current.get(name)
            if (
                name in _MERGED_REASONING_TEXT_KEYS
                and isinstance(value, str)
                and isinstance(previous, str)
            ):
                if self._reserve_text(value):
                    current[name] = previous + value
            elif self._reserve_json({name: value}):
                # The replaced value stays counted, so the bound is conservative.
                current[name] = value

    def _absorb_tool_calls(self, tool_calls: list[Any]) -> None:
        for position, item in enumerate(tool_calls):
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                index = position
            entry = self._tool_calls.get(index)
            if entry is None:
                if self._max_chars is not None and len(self._tool_calls) >= _MAX_TOOL_CALLS:
                    self.truncated = True
                    continue
                entry = self._tool_calls[index] = {
                    "id": None,
                    "type": None,
                    "name": "",
                    "arguments": "",
                }
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id and call_id != entry["id"]:
                if self._reserve_text(call_id):
                    entry["id"] = call_id
            call_type = item.get("type")
            if isinstance(call_type, str) and call_type and call_type != entry["type"]:
                if self._reserve_text(call_type):
                    entry["type"] = call_type
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            # Most providers send the name once; some repeat it on every delta.
            if isinstance(name, str) and name and name != entry["name"]:
                if self._reserve_text(name):
                    entry["name"] += name
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                if self._reserve_text(arguments):
                    entry["arguments"] += arguments
            elif isinstance(arguments, dict):
                encoded = json.dumps(arguments, separators=(",", ":"))
                if self._reserve_text(encoded):
                    entry["arguments"] = encoded


def _json_size(value: Any) -> int:
    # The chunk was decoded from JSON, so it encodes again without an error.
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))
