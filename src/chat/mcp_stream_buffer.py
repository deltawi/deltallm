"""Per-hop SSE filter for streamed chat with gateway-run MCP tools.

The buffer forwards text deltas at once and holds tool-call deltas and the
finish chunk until the hop ends. Only then is it known whether the gateway
runs the tool calls or the client receives them.
"""

from __future__ import annotations

import json
from typing import Any

from src.chat.stream_reply import StreamReplyAccumulator
from src.providers.openai_stream_contract import OpenAIStreamDeltaField


class MCPStreamHopBuffer:
    def __init__(self, *, completion_id: str | None) -> None:
        # Later hops use the id of the first hop, so the client sees one completion.
        self.completion_id = completion_id
        self._rewrite_id = completion_id is not None
        self.created: Any = None
        self.model: Any = None
        # Unbounded: the next hop must carry the exact assistant turn.
        self._reply = StreamReplyAccumulator()
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
            self._reply.add_delta(delta)
            tool_calls = delta.get(OpenAIStreamDeltaField.TOOL_CALLS.value)
            if tool_calls is not None:
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
        return self._reply.tool_calls()

    def assistant_response(self) -> dict[str, Any]:
        """Return the hop as a non-streaming response shape for the tool loop."""
        return {"choices": [{"index": 0, "message": self._reply.message()}]}

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


def _has_delta(choice: dict[str, Any]) -> bool:
    delta = choice.get("delta")
    return isinstance(delta, dict) and any(value not in (None, "") for value in delta.values())


def _encode(chunk: dict[str, Any]) -> str:
    return "data: " + json.dumps(chunk, separators=(",", ":"))
