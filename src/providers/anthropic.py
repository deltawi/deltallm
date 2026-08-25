from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, AsyncIterator

import httpx

from src.models.errors import FailureClassification, InvalidRequestError, ProxyError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import (
    ProviderAdapter,
    ProviderErrorDetails,
    classify_provider_failure,
    is_valid_provider_token_count,
    map_standard_provider_error,
    map_standard_provider_status_error,
    invalid_provider_response_error,
    provider_error_details,
    provider_error_details_from_payload,
    validate_provider_success_payload,
)
from src.providers.healthcheck import is_provider_healthy
from src.providers.resolution import resolve_upstream_model

_CONTEXT_IDENTIFIERS = frozenset(
    {
        "context_length_exceeded",
        "context_window_exceeded",
        "model_context_window_exceeded",
    }
)
_CONTENT_IDENTIFIERS = frozenset(
    {
        "content_filter",
        "content_policy_violation",
        "refusal",
        "safety",
    }
)
# Anthropic documents context overflow as invalid_request_error with this message,
# so the adapter owns this narrow fallback when no more specific code is present.
_CONTEXT_MESSAGE_MARKERS = ("prompt is too long",)
_CONTENT_MESSAGE_MARKERS = (
    "blocked by content filtering policy",
    "violates our usage policies",
)
_STREAM_ERROR_STATUS_BY_TYPE = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "request_too_large": 413,
    "rate_limit_error": 429,
    "api_error": 500,
    "overloaded_error": 529,
}


def _classify_anthropic_failure(
    details: ProviderErrorDetails,
) -> FailureClassification | None:
    return classify_provider_failure(
        details,
        context_identifiers=_CONTEXT_IDENTIFIERS,
        content_identifiers=_CONTENT_IDENTIFIERS,
        context_message_markers=_CONTEXT_MESSAGE_MARKERS,
        content_message_markers=_CONTENT_MESSAGE_MARKERS,
    )


def _map_anthropic_stream_error(event: object) -> ProxyError:
    details = provider_error_details_from_payload(event, status_code=None)
    error = event.get("error") if isinstance(event, dict) else None
    error_type = str(error.get("type") or "") if isinstance(error, dict) else ""
    return map_standard_provider_status_error(
        _STREAM_ERROR_STATUS_BY_TYPE.get(error_type.strip().lower(), 500),
        failure_classification=_classify_anthropic_failure(details),
    )


def _is_valid_anthropic_success_payload(data: Mapping[str, Any]) -> bool:
    content = data.get("content")
    stop_reason = data.get("stop_reason")
    usage = data.get("usage")
    return (
        isinstance(content, list)
        and all(isinstance(block, Mapping) for block in content)
        and isinstance(stop_reason, str)
        and bool(stop_reason.strip())
        and isinstance(usage, Mapping)
        and is_valid_provider_token_count(usage.get("input_tokens"))
        and is_valid_provider_token_count(usage.get("output_tokens"))
    )


def _anthropic_stop_failure(stop_reason: object) -> ProxyError | None:
    if not isinstance(stop_reason, str) or not stop_reason:
        return None
    normalized = stop_reason.rsplit("#", 1)[-1].strip().lower()
    classification = _classify_anthropic_failure(
        ProviderErrorDetails(status_code=400, identifiers=frozenset({normalized}))
    )
    if classification is None:
        return None
    return map_standard_provider_status_error(
        400,
        failure_classification=classification,
    )


def _stream_index(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise invalid_provider_response_error()
    return value


def _flatten_text_content(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    return str(content or "")


def _tool_call_to_tool_use_block(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function") or {}
    try:
        tool_input = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        tool_input = {}
    return {
        "type": "tool_use",
        "id": str(tool_call.get("id") or ""),
        "name": str(function.get("name") or ""),
        "input": tool_input if isinstance(tool_input, dict) else {},
    }


def _function_tool_to_anthropic_tool(tool: Any) -> dict[str, Any]:
    if getattr(tool, "type", None) != "function":
        raise InvalidRequestError(
            message=f"Provider 'anthropic' only supports function tools, got '{getattr(tool, 'type', None)}'",
            param="tools",
        )
    function = tool.function or {}
    spec: dict[str, Any] = {
        "name": str(function.get("name") or ""),
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }
    if function.get("description"):
        spec["description"] = function["description"]
    return spec


def _chat_tool_choice_to_anthropic(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice == "required":
        return {"type": "any"}
    if tool_choice == "none":
        return {"type": "none"}
    named_function = getattr(tool_choice, "function", None)
    if isinstance(named_function, dict) and named_function.get("name"):
        return {"type": "tool", "name": str(named_function["name"])}
    return None


class AnthropicAdapter(ProviderAdapter):
    provider_name = "anthropic"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def translate_request(
        self, canonical_request: ChatCompletionRequest, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        system_messages: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        def append_blocks(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            # Anthropic requires alternating user/assistant turns, so consecutive
            # same-role messages (e.g. multiple tool results) are merged.
            if anthropic_messages and anthropic_messages[-1]["role"] == role:
                anthropic_messages[-1]["content"].extend(blocks)
            else:
                anthropic_messages.append({"role": role, "content": blocks})

        for message in canonical_request.messages:
            text = _flatten_text_content(message.content)
            if message.role == "system":
                if text:
                    system_messages.append(text)
                continue
            if message.role == "tool":
                append_blocks(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id or "",
                            "content": text,
                        }
                    ],
                )
                continue
            role = message.role if message.role in {"user", "assistant"} else "user"
            blocks: list[dict[str, Any]] = []
            if text:
                blocks.append({"type": "text", "text": text})
            if role == "assistant":
                blocks.extend(
                    _tool_call_to_tool_use_block(tool_call)
                    for tool_call in message.tool_calls or []
                )
            append_blocks(role, blocks)

        upstream_model = resolve_upstream_model(provider_config)

        payload: dict[str, Any] = {
            "model": upstream_model or canonical_request.model,
            "messages": anthropic_messages or [{"role": "user", "content": ""}],
            "max_tokens": canonical_request.max_tokens
            or int(provider_config.get("max_tokens") or 1024),
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)
        fields_set = getattr(canonical_request, "model_fields_set", set())
        if "temperature" in fields_set and canonical_request.temperature is not None:
            payload["temperature"] = canonical_request.temperature
        if "top_p" in fields_set and canonical_request.top_p is not None:
            payload["top_p"] = canonical_request.top_p
        if canonical_request.stop:
            payload["stop_sequences"] = (
                canonical_request.stop
                if isinstance(canonical_request.stop, list)
                else [canonical_request.stop]
            )
        if canonical_request.stream:
            payload["stream"] = True
        if canonical_request.tools:
            payload["tools"] = [
                _function_tool_to_anthropic_tool(tool) for tool in canonical_request.tools
            ]
            tool_choice = _chat_tool_choice_to_anthropic(canonical_request.tool_choice)
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        return payload

    async def translate_response(
        self, provider_response: Any, model_name: str
    ) -> ChatCompletionResponse:
        data = (
            provider_response
            if isinstance(provider_response, dict)
            else json.loads(provider_response)
        )
        validate_provider_success_payload(data, _is_valid_anthropic_success_payload)
        stop_reason = data["stop_reason"]
        if failure := _anthropic_stop_failure(stop_reason):
            raise failure
        content_blocks = data.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
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
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0)
        finish_reason_map = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
        }
        finish_reason = finish_reason_map.get(str(stop_reason), "stop")
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
        if tool_calls:
            message["tool_calls"] = tool_calls
        canonical = {
            "id": data.get("id") or f"chatcmpl-anthropic-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": data.get("model") or model_name,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        return ChatCompletionResponse.model_validate(canonical)

    async def translate_stream(
        self,
        provider_stream: AsyncIterator[str],
        *,
        model_name: str | None = None,
    ) -> AsyncIterator[str]:
        stream_id = f"chatcmpl-anthropic-{int(time.time() * 1000)}"
        model = model_name or "anthropic"
        created = int(time.time())
        sent_role = False
        finish_reason: str | None = None
        saw_message_start = False
        saw_terminal_delta = False
        # Maps Anthropic content-block indexes to OpenAI tool_calls indexes.
        tool_call_indexes: dict[int, int] = {}

        def role_chunk() -> str:
            out = {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            return f"data: {json.dumps(out, separators=(',', ':'))}"

        async for line in provider_stream:
            if not line:
                continue
            if line.startswith("event:"):
                continue
            if not line.startswith("data:"):
                continue

            payload = line[len("data:") :].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                raise invalid_provider_response_error()

            try:
                event = json.loads(payload)
            except (RecursionError, TypeError, ValueError) as exc:
                raise invalid_provider_response_error() from exc
            if not isinstance(event, dict):
                raise invalid_provider_response_error()

            event_type = str(event.get("type") or "")
            if event_type == "error":
                raise _map_anthropic_stream_error(event)
            if event_type == "ping":
                continue
            if event_type == "message_start":
                if saw_message_start:
                    raise invalid_provider_response_error()
                message = event.get("message")
                if not isinstance(message, Mapping):
                    raise invalid_provider_response_error()
                saw_message_start = True
                stream_id = str(message.get("id") or stream_id)
                model = str(message.get("model") or model)
                continue

            if event_type == "content_block_start":
                if not saw_message_start:
                    raise invalid_provider_response_error()
                content_block = event.get("content_block")
                if not isinstance(content_block, Mapping):
                    raise invalid_provider_response_error()
                if content_block.get("type") == "tool_use":
                    if not sent_role:
                        yield role_chunk()
                        sent_role = True
                    block_index = _stream_index(event.get("index"))
                    tool_index = len(tool_call_indexes)
                    tool_call_indexes[block_index] = tool_index
                    out = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": tool_index,
                                            "id": str(content_block.get("id") or ""),
                                            "type": "function",
                                            "function": {
                                                "name": str(content_block.get("name") or ""),
                                                "arguments": "",
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(out, separators=(',', ':'))}"
                continue

            if event_type == "content_block_delta":
                if not saw_message_start:
                    raise invalid_provider_response_error()
                delta = event.get("delta")
                if not isinstance(delta, Mapping):
                    raise invalid_provider_response_error()
                text = delta.get("text")
                if isinstance(text, str) and text:
                    if not sent_role:
                        yield role_chunk()
                        sent_role = True
                    out = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {"index": 0, "delta": {"content": text}, "finish_reason": None}
                        ],
                    }
                    yield f"data: {json.dumps(out, separators=(',', ':'))}"
                partial_json = delta.get("partial_json")
                if isinstance(partial_json, str) and partial_json:
                    tool_index = tool_call_indexes.get(_stream_index(event.get("index")))
                    if tool_index is not None:
                        if not sent_role:
                            yield role_chunk()
                            sent_role = True
                        out = {
                            "id": stream_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": tool_index,
                                                "function": {"arguments": partial_json},
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(out, separators=(',', ':'))}"
                continue

            if event_type == "message_delta":
                if not saw_message_start:
                    raise invalid_provider_response_error()
                delta = event.get("delta")
                if not isinstance(delta, Mapping):
                    raise invalid_provider_response_error()
                stop_reason = str(delta.get("stop_reason") or "")
                if not stop_reason:
                    continue
                saw_terminal_delta = True
                finish_map = {
                    "end_turn": "stop",
                    "stop_sequence": "stop",
                    "max_tokens": "length",
                    "tool_use": "tool_calls",
                }
                if failure := _anthropic_stop_failure(stop_reason):
                    if not sent_role:
                        raise failure
                    finish_reason = (
                        "content_filter"
                        if failure.failure_classification is FailureClassification.CONTENT_POLICY
                        else "length"
                    )
                else:
                    finish_reason = finish_map.get(stop_reason)
                continue

            if event_type == "message_stop":
                if not saw_message_start or not saw_terminal_delta:
                    raise invalid_provider_response_error()
                if not sent_role:
                    yield role_chunk()
                    sent_role = True
                out = {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": finish_reason or "stop"}
                    ],
                }
                yield f"data: {json.dumps(out, separators=(',', ':'))}"
                yield "data: [DONE]"
                return

        raise invalid_provider_response_error()

    def map_error(
        self,
        provider_error: Exception,
        *,
        details: ProviderErrorDetails | None = None,
    ) -> ProxyError:
        classification = _classify_anthropic_failure(
            details or provider_error_details(provider_error)
        )
        return map_standard_provider_error(
            provider_error,
            failure_classification=classification,
        )

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        return await is_provider_healthy(
            self.http_client,
            provider_config,
            default_openai_base_url="https://api.openai.com/v1",
            default_provider=self.provider_name,
        )
