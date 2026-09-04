from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .backends.base import CacheBackend, CacheEntry

logger = logging.getLogger(__name__)


@dataclass
class StreamWriteContext:
    cache_key: str
    ttl: int
    model: str
    pricing: dict[str, Any] | None = None
    deployment_id: str | None = None
    provider: str | None = None
    deployment_model: str | None = None


@dataclass(slots=True)
class _StreamAccumulator:
    max_buffer_bytes: int
    max_fragments: int
    response_id: str | None = None
    created: int | None = None
    model: str | None = None
    finish_reason: str = "stop"
    content_parts: list[str] = field(default_factory=list)
    stream_lines: list[str] = field(default_factory=list)
    stream_usage_line: str | None = None
    usage: dict[str, Any] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    buffered_bytes: int = 0
    fragment_count: int = 0
    disabled_reason: str | None = None
    saw_chunk: bool = False

    def disable(self, reason: str) -> None:
        if self.disabled_reason is not None:
            return
        self.disabled_reason = reason
        self.content_parts.clear()
        self.stream_lines.clear()
        self.stream_usage_line = None
        self.buffered_bytes = 0
        self.fragment_count = 0

    def add_chunk(self, chunk: dict[str, Any], line: str) -> str | None:
        if self.disabled_reason is not None:
            return self.disabled_reason

        next_fragment_count = self.fragment_count + 1
        if next_fragment_count > self.max_fragments:
            return "fragment_limit_exceeded"

        next_buffered_bytes = self.buffered_bytes + len(line.encode("utf-8"))
        if next_buffered_bytes > self.max_buffer_bytes:
            return "buffer_limit_exceeded"

        self.saw_chunk = True
        if self.response_id is None:
            response_id = chunk.get("id")
            if response_id:
                self.response_id = str(response_id)
        if self.created is None:
            created = chunk.get("created")
            if created is not None:
                try:
                    self.created = int(created)
                except (TypeError, ValueError):
                    self.created = None
        if self.model is None:
            model = chunk.get("model")
            if model:
                self.model = str(model)

        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.usage = _normalized_usage(usage)

        choices = chunk.get("choices") or []
        if not choices:
            if isinstance(usage, dict):
                self.stream_usage_line = line
            else:
                self.stream_lines.append(line)
            self.fragment_count = next_fragment_count
            self.buffered_bytes = next_buffered_bytes
            return None

        choice = choices[0] or {}
        finish_reason = choice.get("finish_reason")
        if finish_reason:
            self.finish_reason = str(finish_reason)

        delta = choice.get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.content_parts.append(content)
        self.stream_lines.append(line)
        self.fragment_count = next_fragment_count
        self.buffered_bytes = next_buffered_bytes
        return None

    def build_response(
        self, *, fallback_model: str, usage: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not self.saw_chunk or self.disabled_reason is not None:
            return None
        resolved_usage = _normalized_usage(usage if usage is not None else self.usage)

        return {
            "id": self.response_id or f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": self.created or int(time.time()),
            "model": self.model or fallback_model or "unknown",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "".join(self.content_parts)},
                    "finish_reason": self.finish_reason,
                }
            ],
            "usage": resolved_usage,
        }


class StreamingCacheHandler:
    def __init__(
        self,
        backend: CacheBackend,
        *,
        max_buffer_bytes: int = 262_144,
        max_fragments: int = 2_048,
    ) -> None:
        self.backend = backend
        self.max_buffer_bytes = max_buffer_bytes
        self.max_fragments = max_fragments
        self._active_streams: dict[str, _StreamAccumulator] = {}
        self._disabled_streams_total = 0
        self._write_failures_total = 0

    @property
    def active_stream_count(self) -> int:
        return len(self._active_streams)

    @property
    def disabled_streams_total(self) -> int:
        return self._disabled_streams_total

    @property
    def write_failures_total(self) -> int:
        return self._write_failures_total

    def can_replay(self, entry: CacheEntry) -> bool:
        lines = entry.stream_lines
        usage_line = entry.stream_usage_line
        valid_usage_line = usage_line is None or (
            usage_line.startswith("data:") and usage_line.strip() != "data: [DONE]"
        )
        return (
            valid_usage_line
            and bool(lines)
            and all(
                isinstance(line, str)
                and line.startswith("data:")
                and line.strip() != "data: [DONE]"
                for line in lines
            )
        )

    def reconstruct_sse_stream(self, entry: CacheEntry, *, include_usage: bool = False):
        async def generator():
            for line in entry.stream_lines or []:
                yield f"{line}\n\n"
            if include_usage:
                if entry.stream_usage_line is not None:
                    yield f"{entry.stream_usage_line}\n\n"
                else:
                    response = entry.response
                    usage_chunk = {
                        "id": response.get("id"),
                        "object": "chat.completion.chunk",
                        "created": response.get("created"),
                        "model": response.get("model"),
                        "choices": [],
                        "usage": _normalized_usage(response.get("usage") or {}),
                    }
                    yield f"data: {json.dumps(usage_chunk, separators=(',', ':'))}\n\n"
            yield "data: [DONE]\n\n"

        return generator()

    def start_stream(self, stream_id: str) -> None:
        self._active_streams[stream_id] = _StreamAccumulator(
            max_buffer_bytes=self.max_buffer_bytes,
            max_fragments=self.max_fragments,
        )

    def add_chunk_from_line(self, stream_id: str, line: str) -> None:
        if not line.startswith("data:"):
            return

        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            return

        state = self._active_streams.get(stream_id)
        if state is None or state.disabled_reason is not None:
            return

        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            self._disable_stream(state, reason="invalid_json")
            return

        if not isinstance(chunk, dict):
            self._disable_stream(state, reason="invalid_payload")
            return

        reason = state.add_chunk(chunk, line)
        if reason is not None:
            self._disable_stream(state, reason=reason)

    async def finalize_and_store(
        self,
        stream_id: str,
        ctx: StreamWriteContext,
        *,
        usage: Mapping[str, Any] | None = None,
    ) -> None:
        state = self._active_streams.pop(stream_id, None)
        if state is None:
            return

        complete_response = state.build_response(fallback_model=ctx.model, usage=usage)
        if complete_response is None:
            return

        token_count = int((complete_response.get("usage") or {}).get("total_tokens") or 0)
        entry = CacheEntry(
            response=complete_response,
            model=ctx.model,
            cached_at=time.time(),
            ttl=ctx.ttl,
            token_count=token_count,
            pricing=ctx.pricing,
            deployment_id=ctx.deployment_id,
            provider=ctx.provider,
            deployment_model=ctx.deployment_model,
            stream_lines=list(state.stream_lines),
            stream_usage_line=state.stream_usage_line,
        )
        try:
            await self.backend.set(ctx.cache_key, entry, ctx.ttl)
        except Exception as exc:  # pragma: no cover - defensive guard
            self._write_failures_total += 1
            logger.warning("streaming cache write failed: %s", exc)

    def discard_stream(self, stream_id: str) -> None:
        self._active_streams.pop(stream_id, None)

    def _disable_stream(self, state: _StreamAccumulator, *, reason: str) -> None:
        was_enabled = state.disabled_reason is None
        state.disable(reason)
        if was_enabled:
            self._disabled_streams_total += 1


def _normalized_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    prompt_tokens = _int_or_zero(usage.get("prompt_tokens"))
    completion_tokens = _int_or_zero(usage.get("completion_tokens"))
    total_tokens = _int_or_zero(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _int_or_zero(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
