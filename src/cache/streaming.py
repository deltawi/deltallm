from __future__ import annotations

import json
import logging
import time
import uuid
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


@dataclass(slots=True)
class _StreamAccumulator:
    max_buffer_bytes: int
    max_fragments: int
    response_id: str | None = None
    created: int | None = None
    model: str | None = None
    finish_reason: str = "stop"
    content_parts: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    buffered_bytes: int = 0
    fragment_count: int = 0
    disabled_reason: str | None = None
    saw_chunk: bool = False

    def disable(self, reason: str) -> None:
        if self.disabled_reason is not None:
            return
        self.disabled_reason = reason
        self.content_parts.clear()
        self.buffered_bytes = 0
        self.fragment_count = 0

    def add_chunk(self, chunk: dict[str, Any]) -> str | None:
        if self.disabled_reason is not None:
            return self.disabled_reason

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
            self.usage = {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            }

        choices = chunk.get("choices") or []
        if not choices:
            return None

        choice = choices[0] or {}
        finish_reason = choice.get("finish_reason")
        if finish_reason:
            self.finish_reason = str(finish_reason)

        delta = choice.get("delta") or {}
        if "content" not in delta:
            return None

        content = str(delta.get("content") or "")
        if not content:
            return None

        next_fragment_count = self.fragment_count + 1
        if next_fragment_count > self.max_fragments:
            return "fragment_limit_exceeded"

        next_buffered_bytes = self.buffered_bytes + len(content.encode("utf-8"))
        if next_buffered_bytes > self.max_buffer_bytes:
            return "buffer_limit_exceeded"

        self.content_parts.append(content)
        self.fragment_count = next_fragment_count
        self.buffered_bytes = next_buffered_bytes
        return None

    def build_response(self, *, fallback_model: str) -> dict[str, Any] | None:
        if not self.saw_chunk or self.disabled_reason is not None:
            return None

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
            "usage": dict(self.usage),
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

    def reconstruct_sse_stream(self, response: dict[str, Any]):
        async def generator():
            for chunk in self._response_to_chunks(response):
                yield f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
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

        reason = state.add_chunk(chunk)
        if reason is not None:
            self._disable_stream(state, reason=reason)

    async def finalize_and_store(self, stream_id: str, ctx: StreamWriteContext) -> None:
        state = self._active_streams.pop(stream_id, None)
        if state is None:
            return

        complete_response = state.build_response(fallback_model=ctx.model)
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

    def _response_to_chunks(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        choices = response.get("choices") or []
        if not choices:
            return []

        content = ((choices[0] or {}).get("message") or {}).get("content") or ""
        words = str(content).split(" ") if content else [""]
        base_id = response.get("id") or f"chatcmpl-{uuid.uuid4().hex}"
        created = int(response.get("created") or time.time())
        model = str(response.get("model") or "unknown")
        finish_reason = choices[0].get("finish_reason") or "stop"

        chunks: list[dict[str, Any]] = []
        for idx, word in enumerate(words):
            tail = " " if idx < len(words) - 1 else ""
            chunks.append(
                {
                    "id": base_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": f"{word}{tail}"},
                            "finish_reason": None,
                        }
                    ],
                }
            )

        chunks[-1]["choices"][0]["finish_reason"] = finish_reason
        return chunks
