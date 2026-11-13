"""Rebuild one assistant message from canonical ``chat.completion.chunk`` deltas.

Two owners use this accumulator:

- The MCP stream buffer needs the exact assistant turn of each hop to build the
  next hop. It runs without limits, because a cut turn would corrupt the
  follow-up request, and it keeps only the fields that the next hop sends.
- The text audit keeps the reply of a streamed completion (``max_chars`` is
  set). The reply size is bounded by the audit payload limit (at most 1 MiB)
  and not by the stream length. Text counts its size as JSON, as audit
  compaction measures it (a character outside ASCII is a six-character
  escape). The id, model and finish reason count too. The work for one
  chunk is bounded too: long lists, large objects and many JSON values are not
  read. When data is not kept, the reply is marked ``truncated``.

Text is kept as fragments that are joined in groups, so a long stream of small
deltas does not keep one string object for each delta. The shared memory budget
counts an upper bound of the heap bytes of the kept strings and values.
"""

from __future__ import annotations

import json
from typing import Any
import weakref

from src.providers.openai_stream_contract import OpenAIStreamDeltaField
from src.services.audit.payload_compaction import json_text_chars

_MAX_REASONING_DETAILS = 128
# Audit only: the MCP tool loop must see every call and every field the model sent.
_MAX_TOOL_CALLS = 128
# Most providers send one item in each delta; an adapter that sends a whole
# reply in one delta sends all its tool calls there. A longer list is not read.
_MAX_TOOL_CALLS_PER_DELTA = _MAX_TOOL_CALLS
_MAX_DETAILS_PER_DELTA = 16
_MAX_FIELDS_PER_DETAIL = 16
_MAX_FIELDS_PER_DELTA = 16
_MAX_CHOICES_PER_CHUNK = 8
# JSON values (not text fragments) that one reply may measure, such as the
# fields of reasoning details or tool arguments sent as an object.
_MAX_JSON_VALUES = 4096
# Tool arguments sent as an object are encoded on the event loop, and a number
# costs up to about 2 us to write. So one object has at most this many values.
_MAX_ARGUMENT_VALUES = 256
# Larger integers are not kept: their decimal text costs time quadratic in its
# length, and audit compaction replaces them.
_MAX_INTEGER_BITS = 1024
_MERGED_REASONING_TEXT_KEYS = ("text", "summary", "data", "signature")
_KNOWN_DELTA_FIELDS = frozenset(field.value for field in OpenAIStreamDeltaField)
# Upper bounds of heap bytes. A string has a header of at most 64 bytes and a
# list slot; its characters take 1 byte (ASCII) to 4 bytes each.
_STR_BYTES = 80
_VALUE_BYTES = 128
_ENTRY_BYTES = 512
# The JSON of a tool call, function call or reasoning detail around its counted
# values, such as a default tool call id ``call_<index>`` and type ``function``.
_ENTRY_CHARS = 128
# Audit only: a reasoning detail index is a dictionary key before its value is
# counted, so a longer string or a larger integer is not used as a key.
_MAX_KEY_CHARS = 64
# Audit only: the id, model and finish reason are kept when they are at most this
# long and fit the payload limit of the reply. Their characters count against that
# limit, and a fixed charge at creation covers their heap bytes.
_MAX_META_CHARS = 256
_META_BYTES = 3 * (_STR_BYTES + 4 * _MAX_META_CHARS)
# Audit only: provider usage above these bounds keeps only the token counts.
_MAX_USAGE_CHARS = 4096
_MAX_USAGE_VALUES = 128
_TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
_JOIN_EVERY = 64
AUDIT_REPLY_MEMORY_MAX_BYTES = 64 * 1024 * 1024


class ReplyMemory:
    """A process-wide byte budget shared by the bounded accumulators.

    Each accumulator has its own limit, but many streams can run at the same
    time. This budget caps the sum. When it is used up, a reply is cut and
    marked ``truncated``; the stream itself never waits or fails.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self.used = 0

    def take(self, size: int) -> bool:
        if self.used + size > self.max_bytes:
            return False
        self.used += size
        return True

    def give(self, size: int) -> None:
        self.used = max(0, self.used - size)


AUDIT_REPLY_MEMORY = ReplyMemory(AUDIT_REPLY_MEMORY_MAX_BYTES)


def _give_back(memory: ReplyMemory, held: list[int]) -> None:
    memory.give(held[0])
    held[0] = 0


class _Text:
    """Text built from stream fragments, joined in groups of ``_JOIN_EVERY``."""

    __slots__ = ("_parts", "_blocks", "length", "wide", "present")

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._blocks = 0
        self.length = 0
        self.wide = False
        # A field sent as "" is still sent; the MCP hop passes it on.
        self.present = False

    def cost(self) -> int:
        return (4 if self.wide else 1) * self.length + _STR_BYTES * len(self._parts)

    def cost_of(self, fragment: str) -> int:
        """Return the heap bytes that ``add(fragment)`` adds, before any join."""
        wide = self.wide or not fragment.isascii()
        after = (4 if wide else 1) * (self.length + len(fragment))
        return after + _STR_BYTES * (len(self._parts) + 1) - self.cost()

    def add(self, fragment: str) -> int:
        """Append a fragment and return the heap bytes that a join freed."""
        self._parts.append(fragment)
        self.length += len(fragment)
        self.wide = self.wide or not fragment.isascii()
        pending = len(self._parts) - self._blocks
        if pending < _JOIN_EVERY:
            return 0
        block = "".join(self._parts[self._blocks :])
        del self._parts[self._blocks :]
        self._parts.append(block)
        self._blocks += 1
        return _STR_BYTES * (pending - 1)

    def value(self) -> str:
        return "".join(self._parts)

    def equals(self, text: str) -> bool:
        return len(text) == self.length and self.value() == text


class _FunctionCall:
    __slots__ = ("name", "arguments")

    def __init__(self) -> None:
        self.name = _Text()
        self.arguments = _Text()


class _ToolCall(_FunctionCall):
    __slots__ = ("id", "type")

    def __init__(self) -> None:
        super().__init__()
        self.id: str | None = None
        self.type: str | None = None


class StreamReplyAccumulator:
    def __init__(
        self,
        *,
        max_chars: int | None = None,
        max_usage_chars: int = _MAX_USAGE_CHARS,
        memory: ReplyMemory | None = None,
    ) -> None:
        # A limit means the audit owner: bounded work, audit-only fields, and a
        # ``truncated`` marker for data that is not kept.
        self._audit = max_chars is not None
        self._max_chars = max_chars
        # The provider usage is added at the end, from the margin of the payload
        # limit that is not part of ``max_chars``.
        self._max_usage_chars = max(0, min(_MAX_USAGE_CHARS, max_usage_chars))
        self._chars = 0
        self._json_values_left = _MAX_JSON_VALUES
        self._memory = memory
        self._held = [0]
        # The share goes back when release() runs or when the accumulator is
        # freed, so a failed or cancelled stream cannot leak budget.
        self._finalizer = (
            weakref.finalize(self, _give_back, memory, self._held) if memory is not None else None
        )
        self._stopped = False
        self._released = False
        self.truncated = False
        self.saw_chunk = False
        self._clear()
        self._keep_meta = True
        if memory is not None:
            if memory.take(_META_BYTES):
                self._held[0] += _META_BYTES
            else:
                self._keep_meta = False
                self._stop()

    def _clear(self) -> None:
        self.response_id: str | None = None
        self.created: int | None = None
        self.model: str | None = None
        self.finish_reason: str | None = None
        self._clear_data()

    def _clear_data(self) -> None:
        self._content = _Text()
        self._reasoning_content = _Text()
        self._reasoning_details: dict[Any, dict[str, Any]] = {}
        self._tool_calls: dict[int, _ToolCall] = {}
        # Audit only: the MCP tool loop does not send these fields to the next hop.
        self._reasoning = _Text()
        self._refusal = _Text()
        self._function_call: _FunctionCall | None = None

    def add_chunk(self, chunk: dict[str, Any]) -> None:
        """Fold one parsed stream chunk in. Only choice 0 is kept, like the cache."""
        if self._released:
            return
        self.saw_chunk = True
        if self.response_id is None:
            self.response_id = self._meta(chunk.get("id"))
        created = chunk.get("created")
        if self.created is None and isinstance(created, int) and not isinstance(created, bool):
            # A Unix time; a larger integer is not kept.
            self.created = created if created.bit_length() <= 64 else None
        if self.model is None:
            self.model = self._meta(chunk.get("model"))
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            return
        if len(choices) > _MAX_CHOICES_PER_CHUNK:
            self.truncated = True
        for choice in choices[:_MAX_CHOICES_PER_CHUNK]:
            if not isinstance(choice, dict):
                continue
            if choice.get("index", 0) != 0:
                # Other choices (n > 1) are not kept; the marker tells the reader.
                self.truncated = True
                continue
            finish_reason = choice.get("finish_reason")
            # Some providers repeat the finish reason; it counts once.
            if finish_reason and finish_reason != self.finish_reason:
                kept = self._meta(finish_reason)
                if kept:
                    self.finish_reason = kept
            if choice.get("logprobs") is not None:
                self.truncated = True
            delta = choice.get("delta")
            if isinstance(delta, dict):
                self.add_delta(delta)

    def add_delta(self, delta: dict[str, Any]) -> None:
        # Check the stopped state first: a stopped reply does no work for a chunk.
        if self._stopped:
            return
        if self._audit and not self.truncated:
            self._mark_unknown_fields(delta)
        self._add_field(self._content, delta.get(OpenAIStreamDeltaField.CONTENT.value))
        self._add_field(
            self._reasoning_content, delta.get(OpenAIStreamDeltaField.REASONING_CONTENT.value)
        )
        details = delta.get(OpenAIStreamDeltaField.REASONING_DETAILS.value)
        if isinstance(details, list):
            self._add_reasoning_details(details)
        elif details is not None and self._audit:
            self.truncated = True
        tool_calls = delta.get(OpenAIStreamDeltaField.TOOL_CALLS.value)
        if isinstance(tool_calls, list):
            self._absorb_tool_calls(tool_calls)
        elif tool_calls is not None and self._audit:
            self.truncated = True
        if self._audit:
            self._add_field(self._reasoning, delta.get(OpenAIStreamDeltaField.REASONING.value))
            self._add_field(self._refusal, delta.get(OpenAIStreamDeltaField.REFUSAL.value))
            self._add_function_call(delta.get(OpenAIStreamDeltaField.FUNCTION_CALL.value))

    def tool_calls(self) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for index in sorted(self._tool_calls):
            entry = self._tool_calls[index]
            calls.append(
                {
                    "id": entry.id or f"call_{index}",
                    "type": entry.type or "function",
                    "function": {
                        "name": entry.name.value(),
                        # Some adapters stream "" for a tool without input.
                        "arguments": entry.arguments.value() or "{}",
                    },
                }
            )
        return calls

    def message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self._content.value() or None,
            "tool_calls": self.tool_calls(),
        }
        if self._reasoning_content.present:
            message["reasoning_content"] = self._reasoning_content.value()
        if self._reasoning_details:
            message["reasoning_details"] = [
                {
                    name: value.value() if isinstance(value, _Text) else value
                    for name, value in detail.items()
                }
                for detail in self._reasoning_details.values()
            ]
        return message

    def completion(
        self, *, fallback_model: str, usage: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Return the reply as a non-streaming ``chat.completion`` body, or None.

        The body takes the kept data: the accumulator drops its own copy and
        keeps no later data. Its share of the memory budget stays until
        ``release()``, when the next owner (the audit) has the body.
        """
        if not self.saw_chunk or self._released:
            return None
        message = self.message()
        if not message["tool_calls"]:
            del message["tool_calls"]
        if self._reasoning.present:
            message["reasoning"] = self._reasoning.value()
        if self._refusal.present:
            message["refusal"] = self._refusal.value()
        if self._function_call is not None:
            message["function_call"] = {
                "name": self._function_call.name.value(),
                "arguments": self._function_call.arguments.value(),
            }
        model = self.model
        if model is None:
            # The request's model name counts against the limit like the provider's.
            model = self._meta(fallback_model)
        body: dict[str, Any] = {
            "id": self.response_id,
            "object": "chat.completion",
            "created": self.created,
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish_reason}],
            # Readers can tell a rebuilt stream reply from a provider response body.
            "stream": True,
        }
        if usage is not None:
            body["usage"] = self._bounded_usage(usage)
        if self.truncated:
            body["truncated"] = True
        self._stopped = True
        self._clear()
        return body

    def _meta(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        if not self._audit:
            return value
        if not self._keep_meta or len(value) > _MAX_META_CHARS or not self._reserve_meta(value):
            # Not kept; the reply text goes on.
            self.truncated = True
            return None
        return value

    def _reserve_meta(self, value: str) -> bool:
        # The heap bytes were charged at creation. The characters count against the
        # payload limit of this reply, so a small limit still fits the whole body.
        remaining = self._max_chars - self._chars
        chars = json_text_chars(value, stop=remaining) + 2
        if chars > remaining:
            return False
        self._chars += chars
        return True

    def _bounded_usage(self, usage: dict[str, Any]) -> dict[str, Any]:
        if not self._audit:
            return dict(usage)
        size = _json_size(usage, max_chars=self._max_usage_chars, max_values=_MAX_USAGE_VALUES)
        if size is not None:
            return dict(usage)
        self.truncated = True
        # The margin of the payload limit covers three numbers of at most 64 bits.
        return {key: usage[key] for key in _TOKEN_KEYS if _small_number(usage.get(key))}

    def release(self) -> None:
        """Drop the kept data and give this reply's share of the budget back."""
        self._released = True
        self._stopped = True
        self._clear()
        if self._finalizer is not None:
            self._finalizer()

    def _stop(self) -> None:
        self._stopped = True
        self.truncated = True

    def _reserve(self, chars: int, heap: int) -> bool:
        if self._stopped:
            return False
        if self._max_chars is None:
            return True
        if self._chars + chars > self._max_chars:
            self._stop()
            return False
        if self._memory is not None:
            if not self._memory.take(heap):
                self._stop()
                return False
            self._held[0] += heap
        self._chars += chars
        return True

    def _give(self, heap: int) -> None:
        if self._memory is not None:
            heap = min(heap, self._held[0])
            self._memory.give(heap)
            self._held[0] -= heap

    def _add_text(self, text: _Text, fragment: str) -> None:
        if self._stopped:
            return
        text.present = True
        if not fragment:
            return
        if self._max_chars is not None:
            # The exact JSON size, escapes included. The scan stops past the
            # remaining budget, so its work is bounded.
            chars = json_text_chars(fragment, stop=self._max_chars - self._chars)
            if not self._reserve(chars, text.cost_of(fragment)):
                return
        freed = text.add(fragment)
        if freed:
            self._give(freed)

    def _add_field(self, text: _Text, value: Any) -> None:
        if isinstance(value, str):
            self._add_text(text, value)
        elif value is not None and self._audit:
            # A shape that cannot be rebuilt as text (for example a list).
            self.truncated = True

    def _reserve_string(self, value: str) -> bool:
        if self._max_chars is None:
            return True
        if self._stopped:
            return False
        remaining = self._max_chars - self._chars
        chars = json_text_chars(value, stop=remaining) + 2
        width = 1 if value.isascii() else 4
        return self._reserve(chars, _STR_BYTES + width * len(value))

    def _measure_json(
        self, value: Any, *, max_values: int = _MAX_JSON_VALUES
    ) -> tuple[int, int] | None:
        """Measure a JSON value with bounded work: (JSON characters, heap bytes)."""
        if self._stopped or self._max_chars is None:
            return None
        size = _json_size(
            value,
            max_chars=self._max_chars - self._chars,
            max_values=min(max_values, self._json_values_left),
        )
        if size is None:
            self._stop()
            return None
        chars, heap, values = size
        self._json_values_left -= values
        return chars, heap

    def _reserve_json(self, value: Any) -> bool:
        if self._max_chars is None:
            return True
        size = self._measure_json(value)
        return size is not None and self._reserve(*size)

    def _mark_unknown_fields(self, delta: dict[str, Any]) -> None:
        if len(delta) > _MAX_FIELDS_PER_DELTA:
            self.truncated = True
            return
        for name, value in delta.items():
            if name not in _KNOWN_DELTA_FIELDS and value not in (None, "", [], {}):
                # For example audio or annotations: not kept, so the reply is incomplete.
                self.truncated = True
                return

    def _add_reasoning_details(self, details: list[Any]) -> None:
        if self._audit and len(details) > _MAX_DETAILS_PER_DELTA:
            self.truncated = True
            return
        for item in details:
            if self._stopped:
                return
            if isinstance(item, dict):
                self._merge_reasoning_detail(item)
            elif item is not None and self._audit:
                self.truncated = True

    def _merge_reasoning_detail(self, item: dict[str, Any]) -> None:
        if self._audit and len(item) > _MAX_FIELDS_PER_DETAIL:
            self.truncated = True
            return
        index = item.get("index", len(self._reasoning_details))
        # A JSON array or object is not hashable. Treat it as a missing index.
        if isinstance(index, (list, dict)):
            index = len(self._reasoning_details)
        elif self._audit and not _small_key(index):
            # The key is kept before its value is counted, so a long one is not used.
            # Later deltas with that index are then not merged into this detail.
            index = len(self._reasoning_details)
            self.truncated = True
        current = self._reasoning_details.get(index)
        if current is None:
            if len(self._reasoning_details) >= _MAX_REASONING_DETAILS:
                if self._audit:
                    self.truncated = True
                return
            if not self._reserve(_ENTRY_CHARS, _ENTRY_BYTES):
                return
            current = self._reasoning_details[index] = {}
        for name, value in item.items():
            if self._stopped:
                return
            previous = current.get(name)
            if name in _MERGED_REASONING_TEXT_KEYS and isinstance(value, str):
                if not isinstance(previous, _Text):
                    previous = _Text()
                    if not self._reserve_string(name):
                        return
                    current[name] = previous
                self._add_text(previous, value)
            elif name in current and _same_scalar(previous, value):
                # Most providers repeat the type, format and index on every delta.
                continue
            elif self._reserve_json({name: value}):
                # The replaced value stays counted, so the bound is conservative.
                current[name] = value

    def _absorb_tool_calls(self, tool_calls: list[Any]) -> None:
        if self._audit and len(tool_calls) > _MAX_TOOL_CALLS_PER_DELTA:
            self.truncated = True
            return
        for position, item in enumerate(tool_calls):
            if self._stopped:
                return
            if not isinstance(item, dict):
                if item is not None and self._audit:
                    self.truncated = True
                continue
            index = item.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                index = position
            elif self._audit and index.bit_length() > 64:
                # The key is kept before any value is counted.
                index = position
                self.truncated = True
            entry = self._tool_calls.get(index)
            if entry is None:
                if self._audit and len(self._tool_calls) >= _MAX_TOOL_CALLS:
                    self.truncated = True
                    continue
                if not self._reserve(_ENTRY_CHARS, _ENTRY_BYTES):
                    return
                entry = self._tool_calls[index] = _ToolCall()
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id and call_id != entry.id:
                if self._reserve_string(call_id):
                    entry.id = call_id
            call_type = item.get("type")
            if isinstance(call_type, str) and call_type and call_type != entry.type:
                if self._reserve_string(call_type):
                    entry.type = call_type
            function = item.get("function")
            if isinstance(function, dict):
                self._add_function(entry, function)

    def _add_function_call(self, value: Any) -> None:
        if value is None or self._stopped:
            return
        if not isinstance(value, dict):
            self.truncated = True
            return
        if self._function_call is None:
            if not self._reserve(_ENTRY_CHARS, _ENTRY_BYTES):
                return
            self._function_call = _FunctionCall()
        self._add_function(self._function_call, value)

    def _add_function(self, entry: _FunctionCall, function: dict[str, Any]) -> None:
        name = function.get("name")
        # Most providers send the name once; some repeat it on every delta.
        if isinstance(name, str) and name and not entry.name.equals(name):
            self._add_text(entry.name, name)
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            self._add_text(entry.arguments, arguments)
        elif isinstance(arguments, dict):
            # The walk bounds the work of the encode (values and integer sizes); the
            # kept string is then charged as text, at its JSON size and heap bytes.
            if self._audit and (
                self._measure_json(arguments, max_values=_MAX_ARGUMENT_VALUES) is None
            ):
                return
            encoded = json.dumps(arguments, separators=(",", ":"))
            entry.arguments = _Text()
            self._add_text(entry.arguments, encoded)
        elif arguments is not None and self._audit:
            self.truncated = True


def _small_key(value: Any) -> bool:
    if value is None or isinstance(value, (bool, float)):
        return True
    if isinstance(value, int):
        return value.bit_length() <= 64
    return isinstance(value, str) and len(value) <= _MAX_KEY_CHARS


def _small_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value.bit_length() <= 64
    return isinstance(value, float)


def _same_scalar(previous: Any, value: Any) -> bool:
    if type(previous) is not type(value):
        return False
    return (value is None or isinstance(value, (str, int, float, bool))) and previous == value


def _json_size(value: Any, *, max_chars: int, max_values: int) -> tuple[int, int, int] | None:
    """Return (JSON characters, heap bytes, values read) for a decoded JSON value.

    The sizes are upper bounds. The characters include the separators that the
    audit writer (``json.dumps`` with its default ``", "`` and ``": "``) puts
    between items. The walk stops with None when the value is more than
    ``max_chars``, holds more than ``max_values`` values or has an integer over
    1,024 bits, so its work is bounded by those limits and not by the value. It
    never encodes the value.
    """
    chars = 0
    heap = 0
    count = 0
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        count += 1
        if count > max_values:
            return None
        if isinstance(item, str):
            remaining = max_chars - chars - 2
            size = json_text_chars(item, stop=remaining)
            if size > remaining:
                return None
            chars += size + 2
            heap += _STR_BYTES + (1 if item.isascii() else 4) * len(item)
            continue
        heap += _VALUE_BYTES
        if item is None or isinstance(item, bool):
            chars += 5
        elif isinstance(item, int):
            bits = item.bit_length()
            if bits > _MAX_INTEGER_BITS:
                return None
            # Decimal digits are at most 0.31 per bit, plus a sign; at least what
            # audit compaction counts for the number.
            chars += max(4, bits * 31 // 100 + 3)
            # A large integer object keeps 4 bytes for each 30 bits.
            heap += 4 * (bits // 30 + 1)
        elif isinstance(item, float):
            chars += 24
        elif isinstance(item, list):
            if count + len(stack) + len(item) > max_values:
                return None
            # Brackets, and ", " after each item.
            chars += 2 + 2 * len(item)
            stack.extend(item)
        elif isinstance(item, dict):
            if count + len(stack) + 2 * len(item) > max_values:
                return None
            # Braces, and ": " and ", " for each entry.
            chars += 2 + 4 * len(item)
            for key, child in item.items():
                stack.append(key if isinstance(key, str) else str(key))
                stack.append(child)
        else:
            return None
        if chars > max_chars:
            return None
    return chars, heap, count
