"""Canonical chat view of an OpenAI Responses request.

DeltaLLM policy reads the canonical chat request. The raw Responses body stays the source
for the native Responses hop (see `policy_view`). The view also owns the tool-name map
that the translation hop uses to turn chat tool calls back into Responses call items.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from src.models.errors import InvalidRequestError
from src.models.requests import ChatCompletionRequest, ResponsesRequest
from src.providers.openai_responses import NativeResponsesRequest
from src.routers.policy_view import (
    TextPath,
    ViewBuilder,
    add_untracked_text,
    apply_policy_text,
)

ToolKind = Literal["function", "custom"]
_NAMESPACE_SEPARATOR = "__"
_TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text"})
# Provider state that only a native Responses deployment can use. Translation drops it;
# providers accept a history without it.
_POLICY_IGNORED_ITEM_TYPES = frozenset({"reasoning"})
# Hosted search has no chat form. Translation sends the request without it, so the model
# answers without searching; clients such as Codex add it to every request.
_DROPPED_ON_TRANSLATION_TOOL_TYPES = frozenset(
    {"web_search", "web_search_preview", "web_search_2025_08_26", "web_search_preview_2025_03_11"}
)
# These run inside the deployment's provider account (stored files, vector stores,
# containers, remote MCP servers) or add charges DeltaLLM does not bill.
_REJECTED_TOOL_TYPES = frozenset(
    {"file_search", "code_interpreter", "image_generation", "programmatic_tool_calling"}
)
# Stored provider objects. One DeltaLLM tenant must not reach another tenant's objects
# through a shared provider key, and DeltaLLM does not store responses yet.
_REJECTED_FIELDS = ("previous_response_id", "conversation", "prompt")
_FILE_REFERENCE_MESSAGE = (
    "file references are not supported by this gateway; send the content inline "
    "(base64 data or a URL)"
)
# These change the provider price and DeltaLLM pricing has no matching field.
_PRICE_CHANGING_FIELDS = ("service_tier",)


@dataclass(frozen=True, slots=True)
class ResponsesTool:
    """A client-executed Responses tool behind one chat function name."""

    kind: ToolKind
    name: str
    namespace: str | None = None


@dataclass(frozen=True, slots=True)
class ResponsesPolicyView:
    canonical: ChatCompletionRequest
    body: dict[str, Any] = field(repr=False)
    sources: tuple[Any, ...] = field(repr=False)
    tools: dict[str, ResponsesTool] = field(default_factory=dict, repr=False)
    translation_blocker: str | None = None
    # DeltaLLM gateway MCP tools run in the canonical chat loop; such a request always
    # uses the translation hop.
    uses_gateway_tools: bool = False

    def native_request(self, transformed: ChatCompletionRequest) -> NativeResponsesRequest | None:
        """Carry pre-call policy changes into the raw body, or return None to translate."""

        body = None
        if not self.uses_gateway_tools:
            body = apply_policy_text(
                canonical=self.canonical,
                sources=self.sources,
                body=self.body,
                transformed=transformed,
            )
        if body is None:
            if self.translation_blocker is not None:
                raise InvalidRequestError(
                    message=(
                        "A pre-call policy or a gateway tool requires the chat translation, "
                        "and the request has content that only the native Responses hop "
                        "supports"
                    )
                )
            return None
        return NativeResponsesRequest(
            body=_upstream_safe_body(body), translation_blocker=self.translation_blocker
        )


def build_responses_policy_view(
    payload: ResponsesRequest, body: dict[str, Any]
) -> ResponsesPolicyView:
    _reject_provider_account_references(body)
    builder = ViewBuilder(
        blocker_template=(
            "{context}: type '{part_type}' is supported only when the model is served by a "
            "deployment with the native OpenAI Responses API"
        )
    )
    tools, chat_tools, uses_gateway_tools = _tools(builder, payload.tools or [])
    if payload.instructions:
        builder.add({"role": "system", "content": payload.instructions}, [("instructions",)])
    if isinstance(payload.input, str):
        builder.add({"role": "user", "content": payload.input}, [("input",)])
    else:
        _add_items(builder, payload.input, tools)
    if not builder.messages:
        builder.add({"role": "user", "content": ""}, [])

    data: dict[str, Any] = {"model": payload.model, "messages": builder.messages}
    fields_set = payload.model_fields_set
    for source_field, target_field in (
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("max_output_tokens", "max_tokens"),
        ("stream", "stream"),
        ("user", "user"),
        ("metadata", "metadata"),
    ):
        if source_field in fields_set:
            data[target_field] = getattr(payload, source_field)
    if chat_tools:
        data["tools"] = chat_tools
    if payload.tool_choice is not None:
        data["tool_choice"] = _tool_choice(builder, payload.tool_choice, tools)
    response_format = _response_format(payload.text)
    if response_format is not None:
        data["response_format"] = response_format
    return ResponsesPolicyView(
        canonical=ChatCompletionRequest.model_validate(data),
        body=body,
        sources=tuple(builder.sources),
        tools=tools,
        translation_blocker=builder.blocker,
        uses_gateway_tools=uses_gateway_tools,
    )


def _reject_provider_account_references(body: dict[str, Any]) -> None:
    for name in _REJECTED_FIELDS:
        if body.get(name) is not None:
            raise InvalidRequestError(
                message=f"{name}: not supported by this gateway; send the full input instead",
                param=name,
            )
    if body.get("background"):
        raise InvalidRequestError(
            message="background: not supported by this gateway; use stream instead",
            param="background",
        )
    stack: list[Any] = [body.get("input")]
    while stack:
        value = stack.pop()
        if isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, dict):
            if value.get("type") == "item_reference":
                raise InvalidRequestError(
                    message=(
                        "item_reference: not supported by this gateway; send the full item instead"
                    ),
                    param="input",
                )
            if value.get("file_id") is not None:
                raise InvalidRequestError(message=_FILE_REFERENCE_MESSAGE, param="file_id")
            stack.extend(item for item in value.values() if isinstance(item, (list, dict)))


def _tools(
    builder: ViewBuilder, tools: list[dict[str, Any]]
) -> tuple[dict[str, ResponsesTool], list[dict[str, Any]], bool]:
    mapped: dict[str, ResponsesTool] = {}
    chat_tools: list[dict[str, Any]] = []
    uses_gateway_tools = False
    for index, tool in enumerate(tools):
        tool_type = str(tool.get("type") or "")
        context = f"tools[{index}]"
        if tool_type == "function" and isinstance(tool.get("function"), dict):
            # The chat tool shape, accepted on this endpoint before native support.
            chat_tools.append({"type": "function", "function": tool["function"]})
            name = str(tool["function"].get("name") or "")
            mapped[name] = ResponsesTool(kind="function", name=name)
        elif tool_type in {"function", "custom"}:
            chat_tools.append(_chat_function(mapped, tool, namespace=None))
        elif tool_type == "namespace":
            namespace = str(tool.get("name") or "")
            for nested_index, nested in enumerate(tool.get("tools") or []):
                nested_type = nested.get("type") if isinstance(nested, dict) else None
                if nested_type not in {"function", "custom"}:
                    builder.block(f"{context}.tools[{nested_index}]", str(nested_type))
                    continue
                chat_tools.append(_chat_function(mapped, nested, namespace=namespace))
        elif tool_type == "mcp" and _is_gateway_mcp_tool(tool):
            chat_tools.append(tool)
            uses_gateway_tools = True
        elif tool_type == "mcp":
            raise InvalidRequestError(
                message=(
                    f"{context}: remote MCP tools are not supported by this gateway; use a "
                    'DeltaLLM MCP server ({"type": "mcp", "server": "<name>"})'
                ),
                param="tools",
            )
        elif tool_type in _REJECTED_TOOL_TYPES or _is_hosted_shell(tool):
            raise InvalidRequestError(
                message=f"{context}: tool type '{tool_type}' is not supported by this gateway",
                param="tools",
            )
        elif tool_type not in _DROPPED_ON_TRANSLATION_TOOL_TYPES:
            builder.block(context, tool_type)
    return mapped, chat_tools, uses_gateway_tools


def _is_gateway_mcp_tool(tool: dict[str, Any]) -> bool:
    return (
        isinstance(tool.get("server"), str)
        and tool.get("server_url") is None
        and tool.get("connector_id") is None
    )


def _is_hosted_shell(tool: dict[str, Any]) -> bool:
    environment = tool.get("environment")
    return (
        tool.get("type") == "shell"
        and isinstance(environment, dict)
        and environment.get("type") not in (None, "local")
    )


def chat_tool_name(name: str, namespace: str | None) -> str:
    return f"{namespace}{_NAMESPACE_SEPARATOR}{name}" if namespace else name


def _chat_function(
    mapped: dict[str, ResponsesTool], tool: dict[str, Any], *, namespace: str | None
) -> dict[str, Any]:
    name = str(tool.get("name") or "")
    kind: ToolKind = "custom" if tool.get("type") == "custom" else "function"
    chat_name = chat_tool_name(name, namespace)
    mapped[chat_name] = ResponsesTool(kind=kind, name=name, namespace=namespace)
    function: dict[str, Any] = {"name": chat_name}
    if tool.get("description"):
        function["description"] = tool["description"]
    if kind == "custom":
        # A custom tool takes free text. Chat functions take JSON, so the text travels in
        # one string argument.
        function["parameters"] = {
            "type": "object",
            "properties": {"input": {"type": "string", "description": "The raw tool input."}},
            "required": ["input"],
        }
    else:
        function["parameters"] = tool.get("parameters") or {"type": "object", "properties": {}}
        if tool.get("strict") is not None:
            function["strict"] = tool["strict"]
    return {"type": "function", "function": function}


def _tool_choice(
    builder: ViewBuilder, choice: str | dict[str, Any], tools: dict[str, ResponsesTool]
) -> Any:
    if isinstance(choice, str):
        return choice
    choice_type = choice.get("type")
    if choice_type == "function" and isinstance(choice.get("function"), dict):
        return choice
    if choice_type in {"function", "custom"}:
        name = chat_tool_name(str(choice.get("name") or ""), choice.get("namespace"))
        if name not in tools:
            raise InvalidRequestError(
                message="tool_choice.name: must name a tool in `tools`", param="tool_choice"
            )
        return {"type": "function", "function": {"name": name}}
    if choice_type == "allowed_tools":
        return "required" if choice.get("mode") == "required" else "auto"
    builder.block("tool_choice", str(choice_type))
    return "auto"


def _response_format(text: dict[str, Any] | None) -> dict[str, Any] | None:
    text_format = text.get("format") if isinstance(text, dict) else None
    if not isinstance(text_format, dict):
        return None
    format_type = text_format.get("type")
    if format_type == "json_object":
        return {"type": "json_object"}
    if format_type == "json_schema":
        schema = {
            key: text_format[key]
            for key in ("name", "description", "schema", "strict")
            if key in text_format
        }
        return {"type": "json_schema", "json_schema": schema}
    return None


@dataclass(slots=True)
class _Items:
    builder: ViewBuilder
    tools: dict[str, ResponsesTool]
    # Index of the canonical assistant message that takes the next call items.
    open_assistant: int | None = None

    def add(self, message: dict[str, Any], paths: list[TextPath], **kwargs: Any) -> None:
        self.builder.add(message, paths, **kwargs)
        self.open_assistant = (
            len(self.builder.messages) - 1 if message["role"] == "assistant" else None
        )


def _add_items(builder: ViewBuilder, items: list[Any], tools: dict[str, ResponsesTool]) -> None:
    state = _Items(builder=builder, tools=tools)
    for index, item in enumerate(items):
        context = f"input[{index}]"
        if isinstance(item, str):
            state.add({"role": "user", "content": item}, [("input", index)])
            continue
        item_type = str(item.get("type") or ("message" if "role" in item else ""))
        if item_type == "message":
            _add_message(state, item, index)
        elif item_type in {"function_call", "custom_tool_call"}:
            _add_call(state, item)
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            _add_call_output(state, item, index)
        elif item_type in _POLICY_IGNORED_ITEM_TYPES:
            continue
        else:
            builder.block(context, item_type)
            texts: list[str] = []
            add_untracked_text(item, texts)
            if texts:
                state.add({"role": "user", "content": "\n".join(texts)}, [], writable=False)


def _add_message(state: _Items, item: dict[str, Any], index: int) -> None:
    role = item.get("role")
    if role not in {"user", "assistant", "system", "developer"}:
        raise InvalidRequestError(
            message=f"input[{index}].role: must be one of user, assistant, system, developer",
            param="input",
        )
    chat_role = "system" if role == "developer" else role
    content = item.get("content")
    if isinstance(content, str):
        state.add({"role": chat_role, "content": content}, [("input", index, "content")])
        return
    paths: list[TextPath] = []
    texts: list[str] = []
    images: list[dict[str, Any]] = []
    writable = True
    for part_index, part in enumerate(content if isinstance(content, list) else []):
        part_type = part.get("type") if isinstance(part, dict) else type(part).__name__
        context = f"input[{index}].content[{part_index}]"
        if part_type in _TEXT_PART_TYPES and isinstance(part.get("text"), str):
            paths.append(("input", index, "content", part_index, "text"))
            texts.append(part["text"])
        elif part_type == "refusal" and isinstance(part.get("refusal"), str):
            texts.append(part["refusal"])
            writable = False
        elif part_type == "input_image" and isinstance(part.get("image_url"), str):
            image: dict[str, Any] = {"url": part["image_url"]}
            if part.get("detail"):
                image["detail"] = part["detail"]
            images.append({"type": "image_url", "image_url": image})
        else:
            state.builder.block(context, str(part_type))
            writable = add_untracked_text(part, texts) and writable
    text = "\n".join(texts)
    if images:
        # Chat carries images as content parts; such a message is not written back.
        parts = ([{"type": "text", "text": text}] if text else []) + images
        state.add({"role": chat_role, "content": parts}, paths, writable=False)
        return
    state.add({"role": chat_role, "content": text}, paths, writable=writable)


def _add_call(state: _Items, item: dict[str, Any]) -> None:
    name = chat_tool_name(str(item.get("name") or ""), item.get("namespace"))
    if item.get("type") == "custom_tool_call":
        arguments = json.dumps({"input": str(item.get("input") or "")})
    else:
        arguments = item.get("arguments") if isinstance(item.get("arguments"), str) else "{}"
    call = {
        "id": str(item.get("call_id") or item.get("id") or ""),
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    messages = state.builder.messages
    if state.open_assistant is not None:
        messages[state.open_assistant].setdefault("tool_calls", []).append(call)
        return
    state.add({"role": "assistant", "content": "", "tool_calls": [call]}, [])


def _add_call_output(state: _Items, item: dict[str, Any], index: int) -> None:
    output = item.get("output")
    paths: list[TextPath] = []
    texts: list[str] = []
    writable = True
    if isinstance(output, str):
        paths.append(("input", index, "output"))
        texts.append(output)
    elif isinstance(output, list):
        for part_index, part in enumerate(output):
            part_type = part.get("type") if isinstance(part, dict) else type(part).__name__
            if part_type in _TEXT_PART_TYPES and isinstance(part.get("text"), str):
                paths.append(("input", index, "output", part_index, "text"))
                texts.append(part["text"])
            else:
                state.builder.block(f"input[{index}].output[{part_index}]", str(part_type))
                writable = add_untracked_text(part, texts) and writable
    state.add(
        {
            "role": "tool",
            "tool_call_id": str(item.get("call_id") or ""),
            "content": "\n".join(texts),
        },
        paths,
        writable=writable,
    )


def _upstream_safe_body(body: dict[str, Any]) -> dict[str, Any]:
    if "metadata" not in body and not any(name in body for name in _PRICE_CHANGING_FIELDS):
        return body
    # DeltaLLM reads its own controls (prompt_ref, cache, tags) from metadata, and the
    # provider stores metadata in the deployment's account. Neither belongs upstream.
    return {
        key: value
        for key, value in body.items()
        if key != "metadata" and key not in _PRICE_CHANGING_FIELDS
    }
