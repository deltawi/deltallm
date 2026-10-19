"""Shared mechanics of a canonical chat view over a native request body.

DeltaLLM policy (prompts, hooks, guardrails, admission, accounting) reads the canonical
chat request. For an edge format with a native provider hop (`/v1/messages`,
`/v1/responses`), the raw body stays the source for that hop, so the view also records
where the text of each canonical message lives in the body. A policy that changes text is
written back into the body; a policy that changes the message structure makes the request
use the translation hop instead.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from src.models.requests import ChatCompletionRequest

TextPath: TypeAlias = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class ViewMessage:
    """Where the text of one canonical message lives in the raw body."""

    paths: tuple[TextPath, ...]
    error_prefix: str = ""
    # False when the message also holds text from parts the view cannot write back
    # (documents, search results). A policy change to it cannot reach the native body.
    writable: bool = True


@dataclass(slots=True)
class ViewBuilder:
    # Formatted with `context` and `part_type` when content has no chat form.
    blocker_template: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    sources: list[ViewMessage] = field(default_factory=list)
    blocker: str | None = None

    def block(self, context: str, part_type: str) -> None:
        if self.blocker is None:
            self.blocker = self.blocker_template.format(context=context, part_type=part_type)

    def add(
        self,
        message: dict[str, Any],
        paths: list[TextPath],
        *,
        error_prefix: str = "",
        writable: bool = True,
    ) -> None:
        self.messages.append(message)
        self.sources.append(
            ViewMessage(paths=tuple(paths), error_prefix=error_prefix, writable=writable)
        )


# Keys that hold identifiers or provider state, not text the model reads.
_NON_TEXT_KEYS = frozenset(
    {
        "type",
        "id",
        "call_id",
        "tool_use_id",
        "media_type",
        "cache_control",
        "signature",
        "is_error",
        "status",
        "encrypted_content",
        "encrypted_index",
    }
)
# Inline binary or fetched sources: their payload is not text for policy to read.
_BINARY_SOURCE_TYPES = frozenset({"base64", "url"})
_BINARY_KEYS = frozenset({"data", "url", "image_url", "file_data"})


def add_untracked_text(part: Any, texts: list[str]) -> bool:
    """Add the text of a native-only part (document, search result) to the policy view.

    Guardrails must see every text the native hop sends. The view cannot write these
    texts back, so it returns False when it found any.
    """

    found: list[str] = []
    stack: list[Any] = [part]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            if value:
                found.append(value)
        elif isinstance(value, list):
            stack.extend(reversed(value))
        elif isinstance(value, dict):
            binary = value.get("type") in _BINARY_SOURCE_TYPES or value.get("type") in {
                "input_image",
                "input_file",
            }
            stack.extend(
                item
                for key, item in reversed(value.items())
                if key not in _NON_TEXT_KEYS and not (binary and key in _BINARY_KEYS)
            )
    texts.extend(found)
    return not found


# Request fields that the native hop reads from the raw body. A policy change to one of
# them cannot be written back, so the request uses the translation hop.
_POLICY_COMPARED_FIELDS = frozenset({"tools", "tool_choice", "temperature", "top_p", "stop"})
_MESSAGE_STRUCTURE_FIELDS = frozenset({"role", "name", "tool_calls", "tool_call_id"})


def apply_policy_text(
    *,
    canonical: ChatCompletionRequest,
    sources: Sequence[ViewMessage],
    body: dict[str, Any],
    transformed: ChatCompletionRequest,
) -> dict[str, Any] | None:
    """Write policy text changes into a copy of the body; None when they cannot be."""

    original = canonical.messages
    if len(transformed.messages) != len(original) or any(
        after.model_dump(include=_MESSAGE_STRUCTURE_FIELDS)
        != before.model_dump(include=_MESSAGE_STRUCTURE_FIELDS)
        for after, before in zip(transformed.messages, original)
    ):
        return None
    if transformed.model_dump(include=_POLICY_COMPARED_FIELDS) != canonical.model_dump(
        include=_POLICY_COMPARED_FIELDS
    ):
        return None
    changes: list[tuple[ViewMessage, str]] = []
    for after, before, source in zip(transformed.messages, original, sources):
        if after.content == before.content:
            continue
        if not isinstance(after.content, str) or not source.paths or not source.writable:
            return None
        text = after.content
        if source.error_prefix and text.startswith(source.error_prefix):
            text = text[len(source.error_prefix) :]
        changes.append((source, text))
    if not changes:
        return body
    body = copy.deepcopy(body)
    removed: list[TextPath] = []
    for source, text in changes:
        _set_path(body, source.paths[0], text)
        # The changed text is now in the first part of the message; its later text
        # parts are removed so the provider sees each text once.
        removed.extend(source.paths[1:])
    for path in sorted(removed, key=_path_sort_key, reverse=True):
        _remove_text_part(body, path)
    return body


def _path_sort_key(path: TextPath) -> tuple[tuple[int, int | str], ...]:
    return tuple((0, part) if isinstance(part, int) else (1, part) for part in path)


def _set_path(body: dict[str, Any], path: TextPath, value: str) -> None:
    target: Any = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _remove_text_part(body: dict[str, Any], path: TextPath) -> None:
    # Text part paths end with (..., part_index, "text").
    if len(path) < 2 or path[-1] != "text" or not isinstance(path[-2], int):
        return
    container: Any = body
    for key in path[:-2]:
        container = container[key]
    removed = container.pop(path[-2])
    cache_control = removed.get("cache_control") if isinstance(removed, dict) else None
    if cache_control is None:
        return
    # Keep an Anthropic cache breakpoint on the text block that now holds the merged text.
    keeper = next(
        (
            part
            for part in reversed(container[: path[-2]])
            if isinstance(part, dict) and part.get("type") == "text"
        ),
        None,
    )
    if keeper is not None:
        keeper.setdefault("cache_control", cache_control)
