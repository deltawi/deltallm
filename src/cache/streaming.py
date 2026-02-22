from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .backends.base import CacheBackend, CacheEntry


@dataclass
class StreamWriteContext:
    cache_key: str
    ttl: int
    model: str


class StreamingCacheHandler:
    def __init__(self, backend: CacheBackend) -> None:
        self.backend = backend
        self._active_streams: dict[str, list[dict[str, Any]]] = {}

    def reconstruct_sse_stream(self, response: dict[str, Any]):
        async def generator():
            for chunk in self._response_to_chunks(response):
                yield f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
            yield "data: [DONE]\n\n"

        return generator()

    def start_stream(self, stream_id: str) -> None:
        self._active_streams[stream_id] = []

    def add_chunk_from_line(self, stream_id: str, line: str) -> None:
        if not line.startswith("data:"):
            return

        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            return

        chunk = json.loads(payload)
        self._active_streams.setdefault(stream_id, []).append(chunk)

    async def finalize_and_store(self, stream_id: str, ctx: StreamWriteContext) -> None:
        chunks = self._active_streams.pop(stream_id, [])
        if not chunks:
            return

        complete_response = self._assemble_response(chunks)
        token_count = int((complete_response.get("usage") or {}).get("total_tokens") or 0)
        entry = CacheEntry(
            response=complete_response,
            model=ctx.model,
            cached_at=time.time(),
            ttl=ctx.ttl,
            token_count=token_count,
        )
        await self.backend.set(ctx.cache_key, entry, ctx.ttl)

    def discard_stream(self, stream_id: str) -> None:
        self._active_streams.pop(stream_id, None)

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

    def _assemble_response(self, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        content_parts: list[str] = []
        finish_reason = "stop"

        for chunk in chunks:
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if "content" in delta:
                content_parts.append(str(delta["content"]))
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

        first = chunks[0]
        return {
            "id": first.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": first.get("created") or int(time.time()),
            "model": first.get("model") or "unknown",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "".join(content_parts)},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
