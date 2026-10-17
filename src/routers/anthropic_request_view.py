"""Canonical chat view of an Anthropic Messages request.

DeltaLLM policy (prompts, hooks, guardrails, admission, accounting) reads the canonical
chat request. The raw Messages body stays the source for the native Anthropic hop, so this
module also records where each canonical message's text lives in that body. A policy that
changes text is written back; a policy that changes the message structure makes the request
use the translation hop instead.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from src.models.errors import InvalidRequestError
from src.models.requests import AnthropicMessage, AnthropicMessagesRequest, ChatCompletionRequest
from src.providers.anthropic_messages import NativeMessagesRequest

_REQUEST_FIELD_MAP = {
    "temperature": "temperature",
    "top_p": "top_p",
    "stream": "stream",
    "metadata": "metadata",
}

# Thinking blocks are signed provider state. The native hop forwards them; the
# translation hop drops them, which providers accept for earlier turns.
_POLICY_IGNORED_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})

TextPath: TypeAlias = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class _ViewMessage:
    """Where the text of one canonical message lives in the raw body."""

    paths: tuple[TextPath, ...]
    error_prefix: str = ""
    # False when the message also holds text from blocks the view cannot write back
    # (documents, search results). A policy change to it cannot reach the native body.
    writable: bool = True


@dataclass(slots=True)
class _ViewBuilder:
    messages: list[dict[str, Any]] = field(default_factory=list)
    sources: list[_ViewMessage] = field(default_factory=list)
    blocker: str | None = None

    def block(self, context: str, block_type: str) -> None:
        if self.blocker is None:
            self.blocker = (
                f"{context}: content block type '{block_type}' is supported only when the "
                "model is served by an Anthropic deployment"
            )

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
            _ViewMessage(paths=tuple(paths), error_prefix=error_prefix, writable=writable)
        )


@dataclass(frozen=True, slots=True)
class MessagesPolicyView:
    canonical: ChatCompletionRequest
    body: dict[str, Any] = field(repr=False)
    sources: tuple[_ViewMessage, ...] = field(repr=False)
    translation_blocker: str | None = None
    anthropic_beta: str | None = None
    anthropic_version: str | None = None

    def native_request(self, transformed: ChatCompletionRequest) -> NativeMessagesRequest | None:
        """Carry pre-call policy changes into the raw body, or return None to translate."""

        body = _apply_policy_text(self, transformed)
        if body is None:
            if self.translation_blocker is not None:
                raise InvalidRequestError(
                    message=(
                        "A pre-call policy changed this request in a way the native Messages "
                        "path cannot carry, and the request has content that only that path "
                        "supports"
                    )
                )
            return None
        return NativeMessagesRequest(
            body=_upstream_safe_body(body),
            anthropic_beta=self.anthropic_beta,
            anthropic_version=self.anthropic_version,
            translation_blocker=self.translation_blocker,
        )


def build_messages_policy_view(
    payload: AnthropicMessagesRequest,
    body: dict[str, Any],
    *,
    anthropic_beta: str | None = None,
    anthropic_version: str | None = None,
) -> MessagesPolicyView:
    _reject_provider_account_references(body)
    builder = _ViewBuilder()
    if isinstance(payload.system, str) and payload.system:
        builder.add({"role": "system", "content": payload.system}, [("system",)])
    elif isinstance(payload.system, list):
        paths: list[TextPath] = []
        texts: list[str] = []
        writable = _collect_text_blocks(
            builder, payload.system, context="system", path=("system",), paths=paths, texts=texts
        )
        if texts:
            builder.add({"role": "system", "content": "\n".join(texts)}, paths, writable=writable)
    for index, message in enumerate(payload.messages):
        _add_message(builder, message, index)
    if not builder.messages:
        builder.add({"role": "user", "content": ""}, [])

    data: dict[str, Any] = {
        "model": payload.model,
        "messages": builder.messages,
        "max_tokens": payload.max_tokens,
    }
    fields_set = payload.model_fields_set
    for source_field, target_field in _REQUEST_FIELD_MAP.items():
        if source_field in fields_set:
            data[target_field] = getattr(payload, source_field)
    if payload.stop_sequences:
        data["stop"] = payload.stop_sequences
    tools = _function_tools(builder, payload.tools or [])
    if tools:
        data["tools"] = tools
    if payload.tool_choice:
        data["tool_choice"] = _anthropic_tool_choice_to_chat(payload.tool_choice)
    return MessagesPolicyView(
        canonical=ChatCompletionRequest.model_validate(data),
        body=body,
        sources=tuple(builder.sources),
        translation_blocker=builder.blocker,
        anthropic_beta=anthropic_beta,
        anthropic_version=anthropic_version,
    )


def anthropic_messages_to_chat_request(payload: AnthropicMessagesRequest) -> ChatCompletionRequest:
    return build_messages_policy_view(
        payload, payload.model_dump(mode="json", exclude_unset=True)
    ).canonical


# These fields make Anthropic act on objects that belong to the deployment's Anthropic
# account (uploaded files, code containers) or call outside servers with its credentials.
# One DeltaLLM tenant must not reach another tenant's objects through a shared key.
_PROVIDER_ACCOUNT_FIELDS = ("container", "mcp_servers")
_FILE_REFERENCE_MESSAGE = (
    "file references are not supported by this gateway; send the content inline "
    "(base64, text, or url)"
)


def _reject_provider_account_references(body: dict[str, Any]) -> None:
    for name in _PROVIDER_ACCOUNT_FIELDS:
        if body.get(name) is not None:
            raise InvalidRequestError(
                message=f"{name}: not supported by this gateway; remove it from the request",
                param=name,
            )
    stack: list[Any] = [body.get("system"), body.get("messages")]
    while stack:
        value = stack.pop()
        if isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, dict):
            source = value.get("source")
            if isinstance(source, dict) and source.get("type") == "file":
                raise InvalidRequestError(message=_FILE_REFERENCE_MESSAGE, param="source")
            if "file_id" in value or value.get("type") == "container_upload":
                raise InvalidRequestError(message=_FILE_REFERENCE_MESSAGE, param="file_id")
            stack.extend(item for item in value.values() if isinstance(item, (list, dict)))


def _collect_text_blocks(
    builder: _ViewBuilder,
    content: list[Any],
    *,
    context: str,
    path: TextPath,
    paths: list[TextPath],
    texts: list[str],
) -> bool:
    """Collect text blocks; return False when other blocks added text the view cannot write."""

    writable = True
    for index, block in enumerate(content):
        block_type = block.get("type") if isinstance(block, dict) else type(block).__name__
        if block_type == "text" and isinstance(block.get("text"), str):
            paths.append((*path, index, "text"))
            texts.append(block["text"])
        elif block_type not in _POLICY_IGNORED_BLOCK_TYPES:
            builder.block(f"{context}[{index}]", str(block_type))
            writable = _add_untracked_text(block, texts) and writable
    return writable


# Keys that hold identifiers or provider state, not text the model reads.
_NON_TEXT_KEYS = frozenset(
    {
        "type",
        "id",
        "tool_use_id",
        "media_type",
        "cache_control",
        "signature",
        "is_error",
        "encrypted_content",
        "encrypted_index",
    }
)
# Inline binary or fetched sources: their payload is not text for policy to read.
_BINARY_SOURCE_TYPES = frozenset({"base64", "url"})


def _add_untracked_text(block: Any, texts: list[str]) -> bool:
    """Add the text of a native-only block (document, search result) to the policy view.

    Guardrails must see every text the native hop sends. The view cannot write these
    texts back, so it returns False when it found any.
    """

    found: list[str] = []
    stack: list[Any] = [block]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            if value:
                found.append(value)
        elif isinstance(value, list):
            stack.extend(reversed(value))
        elif isinstance(value, dict):
            binary = value.get("type") in _BINARY_SOURCE_TYPES
            stack.extend(
                item
                for key, item in reversed(value.items())
                if key not in _NON_TEXT_KEYS and not (binary and key in {"data", "url"})
            )
    texts.extend(found)
    return not found


def _add_message(builder: _ViewBuilder, message: AnthropicMessage, index: int) -> None:
    role = message.role
    content = message.content
    if isinstance(content, str):
        builder.add({"role": role, "content": content}, [("messages", index, "content")])
        return

    paths: list[TextPath] = []
    texts: list[str] = []
    writable = True
    tool_calls: list[dict[str, Any]] = []
    tool_results = 0
    for block_index, block in enumerate(content):
        context = f"messages[{index}].content[{block_index}]"
        block_path: TextPath = ("messages", index, "content", block_index)
        block_type = str(block.get("type") or "") if isinstance(block, dict) else ""
        if not isinstance(block, dict):
            builder.block(context, type(block).__name__)
        elif block_type == "text" and isinstance(block.get("text"), str):
            paths.append((*block_path, "text"))
            texts.append(block["text"])
        elif block_type == "tool_use" and role == "assistant":
            tool_calls.append(
                {
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
        elif block_type == "tool_result" and role == "user":
            _add_tool_result(builder, block, context=context, path=block_path)
            tool_results += 1
        elif block_type not in _POLICY_IGNORED_BLOCK_TYPES:
            builder.block(context, block_type)
            writable = _add_untracked_text(block, texts) and writable

    if texts or tool_calls or not tool_results:
        entry: dict[str, Any] = {"role": role, "content": "\n".join(texts)}
        if tool_calls:
            entry["tool_calls"] = tool_calls
        builder.add(entry, paths, writable=writable)


def _add_tool_result(
    builder: _ViewBuilder, block: dict[str, Any], *, context: str, path: TextPath
) -> None:
    result = block.get("content")
    paths: list[TextPath] = []
    texts: list[str] = []
    writable = True
    if isinstance(result, str):
        paths.append((*path, "content"))
        texts.append(result)
    elif isinstance(result, list):
        writable = _collect_text_blocks(
            builder,
            result,
            context=f"{context}.content",
            path=(*path, "content"),
            paths=paths,
            texts=texts,
        )
    text = "\n".join(texts)
    prefix = "Error: " if block.get("is_error") else ""
    if prefix:
        text = f"{prefix}{text}" if text else "Error"
    builder.add(
        {"role": "tool", "tool_call_id": str(block.get("tool_use_id") or ""), "content": text},
        paths,
        error_prefix=prefix,
        writable=writable,
    )


def _function_tools(builder: _ViewBuilder, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    function_tools: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        if tool.get("type") not in (None, "custom"):
            builder.block(f"tools[{index}]", str(tool.get("type")))
            continue
        function_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description"),
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return function_tools


def _anthropic_tool_choice_to_chat(tool_choice: dict[str, Any]) -> Any:
    choice_type = tool_choice.get("type")
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "tool":
        name = str(tool_choice.get("name") or "").strip()
        if not name:
            raise InvalidRequestError(
                message="tool_choice.name: required when tool_choice.type is 'tool'",
                param="tool_choice.name",
            )
        return {"type": "function", "function": {"name": name}}
    if choice_type == "none":
        return "none"
    raise InvalidRequestError(
        message="tool_choice.type: must be one of 'auto', 'any', 'tool', or 'none'",
        param="tool_choice.type",
    )


# Anthropic charges a different token price for these options, and DeltaLLM pricing has
# no matching field. The native hop sends the request without them.
_PRICE_CHANGING_FIELDS = ("speed", "inference_geo")


def _upstream_safe_body(body: dict[str, Any]) -> dict[str, Any]:
    metadata = body.get("metadata")
    if metadata is None and not any(name in body for name in _PRICE_CHANGING_FIELDS):
        return body
    result = {key: value for key, value in body.items() if key not in _PRICE_CHANGING_FIELDS}
    # DeltaLLM reads its own controls (prompt_ref, cache, tags) from metadata; the
    # Messages API accepts only user_id there.
    if isinstance(metadata, dict) and "user_id" in metadata:
        result["metadata"] = {"user_id": metadata["user_id"]}
    else:
        result.pop("metadata", None)
    return result


# Request fields that the native hop reads from the raw body. A policy change to one of
# them cannot be written back, so the request uses the translation hop.
_POLICY_COMPARED_FIELDS = frozenset({"tools", "tool_choice", "temperature", "top_p", "stop"})
_MESSAGE_STRUCTURE_FIELDS = frozenset({"role", "name", "tool_calls", "tool_call_id"})


def _apply_policy_text(
    view: MessagesPolicyView, transformed: ChatCompletionRequest
) -> dict[str, Any] | None:
    original = view.canonical.messages
    if len(transformed.messages) != len(original) or any(
        after.model_dump(include=_MESSAGE_STRUCTURE_FIELDS)
        != before.model_dump(include=_MESSAGE_STRUCTURE_FIELDS)
        for after, before in zip(transformed.messages, original)
    ):
        return None
    if transformed.model_dump(include=_POLICY_COMPARED_FIELDS) != view.canonical.model_dump(
        include=_POLICY_COMPARED_FIELDS
    ):
        return None
    changes: list[tuple[_ViewMessage, str]] = []
    for after, before, source in zip(transformed.messages, original, view.sources):
        if after.content == before.content:
            continue
        if not isinstance(after.content, str) or not source.paths or not source.writable:
            return None
        text = after.content
        if source.error_prefix and text.startswith(source.error_prefix):
            text = text[len(source.error_prefix) :]
        changes.append((source, text))
    if not changes:
        return view.body
    body = copy.deepcopy(view.body)
    removed: list[TextPath] = []
    for source, text in changes:
        _set_path(body, source.paths[0], text)
        # The changed text is now in the first block of the message; its later text
        # blocks are removed so the provider sees each text once.
        removed.extend(source.paths[1:])
    for path in sorted(removed, key=_path_sort_key, reverse=True):
        _remove_text_block(body, path)
    return body


def _path_sort_key(path: TextPath) -> tuple[tuple[int, int | str], ...]:
    return tuple((0, part) if isinstance(part, int) else (1, part) for part in path)


def _set_path(body: dict[str, Any], path: TextPath, value: str) -> None:
    target: Any = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _remove_text_block(body: dict[str, Any], path: TextPath) -> None:
    # Text block paths end with (..., block_index, "text").
    if len(path) < 2 or path[-1] != "text" or not isinstance(path[-2], int):
        return
    container: Any = body
    for key in path[:-2]:
        container = container[key]
    removed = container.pop(path[-2])
    cache_control = removed.get("cache_control") if isinstance(removed, dict) else None
    if cache_control is None:
        return
    # Keep the cache breakpoint on the text block that now holds the merged text.
    keeper = next(
        (
            block
            for block in reversed(container[: path[-2]])
            if isinstance(block, dict) and block.get("type") == "text"
        ),
        None,
    )
    if keeper is not None:
        keeper.setdefault("cache_control", cache_control)
