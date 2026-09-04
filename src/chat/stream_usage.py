from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from src.models.requests import ChatCompletionRequest
from src.providers.openai_stream_contract import OpenAIStreamDeltaField

StreamUsageSource = Literal["provider", "estimated"]
_TOKEN_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


@dataclass(frozen=True, slots=True)
class StreamLineInfo:
    is_usage_only_chunk: bool = False


@dataclass(frozen=True, slots=True)
class StreamUsage:
    usage: dict[str, Any]
    source: StreamUsageSource
    estimate_incomplete: bool = False

    @property
    def estimated(self) -> bool:
        return self.source == "estimated"

    def metadata(self) -> dict[str, Any]:
        return {
            "usage_source": self.source,
            "usage_estimated": self.estimated,
            "usage_estimate_incomplete": self.estimate_incomplete,
        }


@dataclass(frozen=True, slots=True)
class _CompletionDeltaUsage:
    ordinary_chars: int = 0
    reasoning_chars: int = 0
    reasoning_estimate_incomplete: bool = False


class StreamUsageTracker:
    def __init__(self) -> None:
        self._provider_usage: dict[str, Any] | None = None
        self._completion_chars = 0
        self._reasoning_chars = 0
        self._reasoning_estimate_incomplete = False

    def add_line(self, line: str) -> StreamLineInfo:
        if not line.startswith("data:"):
            return StreamLineInfo()
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            return StreamLineInfo()
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return StreamLineInfo()
        if not isinstance(chunk, dict):
            return StreamLineInfo()

        usage = chunk.get("usage")
        if isinstance(usage, dict) and _is_provider_token_usage(usage):
            self._provider_usage = dict(usage)

        delta_usage = _completion_delta_usage(chunk)
        self._completion_chars += delta_usage.ordinary_chars
        self._reasoning_chars += delta_usage.reasoning_chars
        self._reasoning_estimate_incomplete = (
            self._reasoning_estimate_incomplete or delta_usage.reasoning_estimate_incomplete
        )
        return StreamLineInfo(is_usage_only_chunk=_is_usage_only_chunk(chunk))

    def resolve(self, payload: ChatCompletionRequest) -> StreamUsage:
        if self._provider_usage is not None:
            return StreamUsage(usage=_normalized_usage(self._provider_usage), source="provider")

        prompt_tokens = estimate_chat_prompt_tokens(payload)
        completion_tokens = _estimate_tokens_from_chars(
            self._completion_chars + self._reasoning_chars,
            minimum=0,
        )
        return StreamUsage(
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            source="estimated",
            estimate_incomplete=self._reasoning_estimate_incomplete,
        )


def estimate_chat_prompt_tokens(payload: ChatCompletionRequest) -> int:
    total_chars = 0
    for message in payload.messages:
        total_chars += len(message.role)
        total_chars += _content_chars(message.content)
    return _estimate_tokens_from_chars(total_chars, minimum=1)


def _normalized_usage(usage: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(usage)
    prompt_tokens = _int_or_zero(normalized.get("prompt_tokens"))
    completion_tokens = _int_or_zero(normalized.get("completion_tokens"))
    total_tokens = _int_or_zero(normalized.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    normalized["prompt_tokens"] = prompt_tokens
    normalized["completion_tokens"] = completion_tokens
    normalized["total_tokens"] = total_tokens
    return normalized


def _is_provider_token_usage(usage: dict[str, Any]) -> bool:
    present_keys = tuple(key for key in _TOKEN_USAGE_KEYS if key in usage)
    return bool(present_keys) and all(_is_int_like(usage.get(key)) for key in present_keys)


def _content_chars(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    return len(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str))


def _completion_delta_usage(chunk: dict[str, Any]) -> _CompletionDeltaUsage:
    choices = chunk.get("choices")
    if not isinstance(choices, list):
        return _CompletionDeltaUsage()

    ordinary_chars = 0
    reasoning_chars = 0
    reasoning_estimate_incomplete = False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message")
        if not isinstance(delta, dict):
            continue
        delta_usage = _delta_usage(delta)
        ordinary_chars += delta_usage.ordinary_chars
        reasoning_chars += delta_usage.reasoning_chars
        reasoning_estimate_incomplete = (
            reasoning_estimate_incomplete or delta_usage.reasoning_estimate_incomplete
        )
    return _CompletionDeltaUsage(
        ordinary_chars=ordinary_chars,
        reasoning_chars=reasoning_chars,
        reasoning_estimate_incomplete=reasoning_estimate_incomplete,
    )


def _is_usage_only_chunk(chunk: dict[str, Any]) -> bool:
    usage = chunk.get("usage")
    choices = chunk.get("choices")
    return isinstance(usage, dict) and isinstance(choices, list) and not choices


def _delta_usage(delta: dict[str, Any]) -> _CompletionDeltaUsage:
    ordinary_chars = 0
    content = delta.get(OpenAIStreamDeltaField.CONTENT.value)
    if isinstance(content, str):
        ordinary_chars += len(content)
    elif content is not None:
        ordinary_chars += _content_chars(content)

    for key in (
        OpenAIStreamDeltaField.REFUSAL.value,
        OpenAIStreamDeltaField.FUNCTION_CALL.value,
        OpenAIStreamDeltaField.TOOL_CALLS.value,
    ):
        value = delta.get(key)
        if value is not None:
            ordinary_chars += _content_chars(value)

    reasoning_chars, reasoning_incomplete = _reasoning_text_chars(
        delta.get(OpenAIStreamDeltaField.REASONING.value)
    )
    reasoning_content_chars, reasoning_content_incomplete = _reasoning_text_chars(
        delta.get(OpenAIStreamDeltaField.REASONING_CONTENT.value)
    )
    reasoning_details_chars, reasoning_details_incomplete = _reasoning_details_text_chars(
        delta.get(OpenAIStreamDeltaField.REASONING_DETAILS.value)
    )
    return _CompletionDeltaUsage(
        ordinary_chars=ordinary_chars,
        reasoning_chars=max(
            reasoning_chars,
            reasoning_content_chars,
            reasoning_details_chars,
        ),
        reasoning_estimate_incomplete=(
            reasoning_incomplete or reasoning_content_incomplete or reasoning_details_incomplete
        ),
    )


def _reasoning_text_chars(value: Any) -> tuple[int, bool]:
    if value in (None, "", [], {}):
        return 0, False
    if isinstance(value, str):
        return len(value), False
    return _content_chars(value), True


def _reasoning_details_text_chars(value: Any) -> tuple[int, bool]:
    if value in (None, "", [], {}):
        return 0, False
    if isinstance(value, str):
        return len(value), False

    items = value if isinstance(value, list) else [value]
    total = 0
    incomplete = False
    for item in items:
        if isinstance(item, str):
            total += len(item)
            continue
        if not isinstance(item, dict):
            total += _content_chars(item)
            incomplete = True
            continue
        item_chars = sum(
            len(text) for key in ("text", "summary") if isinstance((text := item.get(key)), str)
        )
        if item_chars == 0:
            opaque_value = item.get("data")
            item_chars = _content_chars(opaque_value if opaque_value is not None else item)
            incomplete = True
        total += item_chars
    return total, incomplete


def _estimate_tokens_from_chars(char_count: int, *, minimum: int) -> int:
    if char_count <= 0:
        return minimum
    return max(minimum, (char_count + 3) // 4)


def _int_or_zero(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _is_int_like(value: Any) -> bool:
    try:
        int(value or 0)
    except (TypeError, ValueError):
        return False
    return True
