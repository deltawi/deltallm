from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from src.models.requests import ChatCompletionRequest

StreamUsageSource = Literal["provider", "estimated"]
_TOKEN_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


@dataclass(frozen=True, slots=True)
class StreamLineInfo:
    is_usage_only_chunk: bool = False


@dataclass(frozen=True, slots=True)
class StreamUsage:
    usage: dict[str, Any]
    source: StreamUsageSource

    @property
    def estimated(self) -> bool:
        return self.source == "estimated"

    def metadata(self) -> dict[str, Any]:
        return {
            "usage_source": self.source,
            "usage_estimated": self.estimated,
        }


class StreamUsageTracker:
    def __init__(self) -> None:
        self._provider_usage: dict[str, Any] | None = None
        self._completion_chars = 0

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

        self._completion_chars += _completion_delta_chars(chunk)
        return StreamLineInfo(is_usage_only_chunk=_is_usage_only_chunk(chunk))

    def resolve(self, payload: ChatCompletionRequest) -> StreamUsage:
        if self._provider_usage is not None:
            return StreamUsage(usage=_normalized_usage(self._provider_usage), source="provider")

        prompt_tokens = estimate_chat_prompt_tokens(payload)
        completion_tokens = _estimate_tokens_from_chars(self._completion_chars, minimum=0)
        return StreamUsage(
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            source="estimated",
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


def _completion_delta_chars(chunk: dict[str, Any]) -> int:
    choices = chunk.get("choices")
    if not isinstance(choices, list):
        return 0

    total = 0
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            total += _delta_chars(delta)
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            total += _delta_chars(message)
    return total


def _is_usage_only_chunk(chunk: dict[str, Any]) -> bool:
    usage = chunk.get("usage")
    choices = chunk.get("choices")
    return isinstance(usage, dict) and isinstance(choices, list) and not choices


def _delta_chars(delta: dict[str, Any]) -> int:
    total = 0
    content = delta.get("content")
    if isinstance(content, str):
        total += len(content)
    elif content is not None:
        total += _content_chars(content)

    for key in ("refusal", "function_call", "tool_calls"):
        value = delta.get(key)
        if value is not None:
            total += _content_chars(value)
    return total


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
