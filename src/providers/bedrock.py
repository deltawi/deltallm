from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, AsyncIterator

import httpx
from botocore.eventstream import EventStreamBuffer

from src.models.errors import (
    FailureClassification,
    InvalidRequestError,
    ProxyError,
    RateLimitError,
    ServiceUnavailableError,
)
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import (
    ProviderAdapter,
    ProviderErrorDetails,
    classify_provider_failure,
    invalid_provider_response_error,
    is_valid_provider_token_count,
    map_standard_provider_error,
    map_standard_provider_status_error,
    provider_error_details,
    validate_provider_success_payload,
)
from src.providers.healthcheck import is_provider_healthy

_CONTEXT_IDENTIFIERS = frozenset(
    {
        "context_length_exceeded",
        "context_window_exceeded",
        "model_context_window_exceeded",
    }
)
_CONTENT_IDENTIFIERS = frozenset(
    {
        "content_filtered",
        "contentpolicyviolationexception",
        "guardrail_intervened",
        "guardrailintervenedexception",
    }
)
_CONTEXT_MESSAGE_MARKERS = ("input is too long", "maximum context length")
_CONTENT_MESSAGE_MARKERS = ("guardrail intervened", "violates content policy")

_STOP_REASON_MAP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "guardrail_intervened": "content_filter",
    "content_filtered": "content_filter",
}

_BEDROCK_STREAM_STATUS_BY_EXCEPTION = {
    "validationexception": 400,
    "throttlingexception": 429,
    "modelstreamerrorexception": 424,
    "internalserverexception": 500,
    "serviceunavailableexception": 503,
}


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
        "toolUse": {
            "toolUseId": str(tool_call.get("id") or ""),
            "name": str(function.get("name") or ""),
            "input": tool_input if isinstance(tool_input, dict) else {},
        }
    }


def _function_tool_to_tool_spec(tool: Any) -> dict[str, Any]:
    if getattr(tool, "type", None) != "function":
        raise InvalidRequestError(
            message=f"Provider 'bedrock' only supports function tools, got '{getattr(tool, 'type', None)}'",
            param="tools",
        )
    function = tool.function or {}
    spec: dict[str, Any] = {
        "name": str(function.get("name") or ""),
        "inputSchema": {"json": function.get("parameters") or {"type": "object", "properties": {}}},
    }
    if function.get("description"):
        spec["description"] = function["description"]
    return {"toolSpec": spec}


def _chat_tool_choice_to_bedrock(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice == "required":
        return {"any": {}}
    named_function = getattr(tool_choice, "function", None)
    if isinstance(named_function, dict) and named_function.get("name"):
        return {"tool": {"name": str(named_function["name"])}}
    return None


def _map_stream_error(exception_type: str, message: str) -> ProxyError:
    normalized = exception_type.rsplit("#", 1)[-1].strip().lower()
    status_code = _BEDROCK_STREAM_STATUS_BY_EXCEPTION.get(normalized, 500)
    if status_code == 429:
        return RateLimitError(
            message="Provider rate limited request",
            affects_deployment_health=True,
            failure_classification=FailureClassification.RATE_LIMIT,
        )
    if status_code == 400:
        classification = _classify_bedrock_failure(
            ProviderErrorDetails(
                status_code=status_code,
                identifiers=frozenset({normalized}),
                message=message,
            )
        )
        return InvalidRequestError(
            message="Provider rejected request",
            affects_deployment_health=False,
            failure_classification=classification or FailureClassification.GENERIC,
        )
    return ServiceUnavailableError(
        message="Provider unavailable",
        affects_deployment_health=True,
        failure_classification=FailureClassification.GENERIC,
    )


def _classify_bedrock_failure(
    details: ProviderErrorDetails,
) -> FailureClassification | None:
    return classify_provider_failure(
        details,
        context_identifiers=_CONTEXT_IDENTIFIERS,
        content_identifiers=_CONTENT_IDENTIFIERS,
        context_message_markers=_CONTEXT_MESSAGE_MARKERS,
        content_message_markers=_CONTENT_MESSAGE_MARKERS,
    )


def _bedrock_stop_failure(stop_reason: object) -> ProxyError | None:
    if not isinstance(stop_reason, str) or not stop_reason:
        return None
    normalized = stop_reason.rsplit("#", 1)[-1].strip().lower()
    classification = _classify_bedrock_failure(
        ProviderErrorDetails(
            status_code=400,
            identifiers=frozenset({normalized}),
        )
    )
    if classification is None:
        return None
    return map_standard_provider_status_error(
        400,
        failure_classification=classification,
    )


def _is_valid_bedrock_success_payload(data: Mapping[str, Any]) -> bool:
    output = data.get("output")
    message = output.get("message") if isinstance(output, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    usage = data.get("usage")
    stop_reason = data.get("stopReason")
    return (
        isinstance(content, list)
        and all(isinstance(block, Mapping) for block in content)
        and isinstance(stop_reason, str)
        and bool(stop_reason.strip())
        and isinstance(usage, Mapping)
        and is_valid_provider_token_count(usage.get("inputTokens"))
        and is_valid_provider_token_count(usage.get("outputTokens"))
        and is_valid_provider_token_count(usage.get("totalTokens"))
    )


def _stream_index(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise invalid_provider_response_error()
    return value


def _classified_stream_finish_reason(failure: ProxyError) -> str:
    if failure.failure_classification is FailureClassification.CONTENT_POLICY:
        return "content_filter"
    return "length"


class BedrockAdapter(ProviderAdapter):
    provider_name = "bedrock"
    stream_uses_bytes = True

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def translate_request(
        self,
        canonical_request: ChatCompletionRequest,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        system_blocks: list[dict[str, str]] = []
        messages: list[dict[str, Any]] = []

        def append_blocks(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            # Converse requires alternating user/assistant turns, so consecutive
            # same-role messages (e.g. multiple tool results) are merged.
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})

        for message in canonical_request.messages:
            text = _flatten_text_content(message.content)
            if message.role == "system":
                if text:
                    system_blocks.append({"text": text})
                continue
            if message.role == "tool":
                append_blocks(
                    "user",
                    [
                        {
                            "toolResult": {
                                "toolUseId": message.tool_call_id or "",
                                "content": [{"text": text}],
                            }
                        }
                    ],
                )
                continue
            role = "assistant" if message.role == "assistant" else "user"
            blocks: list[dict[str, Any]] = []
            if text:
                blocks.append({"text": text})
            if role == "assistant":
                blocks.extend(
                    _tool_call_to_tool_use_block(tool_call)
                    for tool_call in message.tool_calls or []
                )
            if not blocks and role == "user":
                blocks.append({"text": text})
            append_blocks(role, blocks)

        payload: dict[str, Any] = {
            "messages": messages or [{"role": "user", "content": [{"text": ""}]}]
        }
        if system_blocks:
            payload["system"] = system_blocks
        if canonical_request.tools and canonical_request.tool_choice != "none":
            tool_config: dict[str, Any] = {
                "tools": [_function_tool_to_tool_spec(tool) for tool in canonical_request.tools]
            }
            tool_choice = _chat_tool_choice_to_bedrock(canonical_request.tool_choice)
            if tool_choice is not None:
                tool_config["toolChoice"] = tool_choice
            payload["toolConfig"] = tool_config

        inference_config: dict[str, Any] = {}
        if canonical_request.max_tokens is not None:
            inference_config["maxTokens"] = canonical_request.max_tokens
        fields_set = getattr(canonical_request, "model_fields_set", set())
        if "temperature" in fields_set and canonical_request.temperature is not None:
            inference_config["temperature"] = canonical_request.temperature
        if "top_p" in fields_set and canonical_request.top_p is not None:
            inference_config["topP"] = canonical_request.top_p
        if canonical_request.stop:
            inference_config["stopSequences"] = (
                canonical_request.stop
                if isinstance(canonical_request.stop, list)
                else [canonical_request.stop]
            )
        if inference_config:
            payload["inferenceConfig"] = inference_config

        return payload

    async def translate_response(
        self, provider_response: Any, model_name: str
    ) -> ChatCompletionResponse:
        data = (
            provider_response
            if isinstance(provider_response, dict)
            else json.loads(provider_response)
        )
        validate_provider_success_payload(data, _is_valid_bedrock_success_payload)
        output = data.get("output") or {}
        message = output.get("message") or {}
        contents = message.get("content") or []
        text = "".join(str(block.get("text", "")) for block in contents if isinstance(block, dict))
        tool_calls: list[dict[str, Any]] = []
        for block in contents:
            tool_use = block.get("toolUse") if isinstance(block, dict) else None
            if isinstance(tool_use, dict):
                tool_calls.append(
                    {
                        "id": str(tool_use.get("toolUseId") or ""),
                        "type": "function",
                        "function": {
                            "name": str(tool_use.get("name") or ""),
                            "arguments": json.dumps(tool_use.get("input") or {}),
                        },
                    }
                )

        stop_reason = data.get("stopReason") or "end_turn"
        if failure := _bedrock_stop_failure(stop_reason):
            raise failure
        finish_reason = _STOP_REASON_MAP.get(str(stop_reason), "stop")
        response_message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            response_message["tool_calls"] = tool_calls

        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("inputTokens") or 0)
        completion_tokens = int(usage.get("outputTokens") or 0)
        total_tokens = int(usage.get("totalTokens") or (prompt_tokens + completion_tokens))

        canonical = {
            "id": data.get("requestId") or f"chatcmpl-bedrock-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": response_message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }
        return ChatCompletionResponse.model_validate(canonical)

    async def translate_stream(
        self,
        provider_stream: AsyncIterator[bytes],
        *,
        model_name: str | None = None,
    ) -> AsyncIterator[str]:
        stream_id = f"chatcmpl-bedrock-{int(time.time() * 1000)}"
        created = int(time.time())
        stream_model = model_name or "bedrock"
        buffer = EventStreamBuffer()
        sent_role = False
        finish_reason = "stop"
        usage: dict[str, int] = {}
        tool_call_indexes: dict[int, int] = {}
        saw_message_start = False
        saw_message_stop = False
        emitted_output = False

        def _chunk(delta: dict[str, Any], *, stop: str | None = None) -> str:
            body: dict[str, Any] = {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": stream_model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": stop}],
            }
            return f"data: {json.dumps(body, separators=(',', ':'))}"

        async for raw in provider_stream:
            buffer.add_data(raw)
            while True:
                try:
                    message = buffer.next()
                except StopIteration:
                    break

                event_type = message.headers.get(":event-type")
                try:
                    event = json.loads(message.payload) if message.payload else {}
                except (RecursionError, TypeError, ValueError) as exc:
                    raise invalid_provider_response_error() from exc
                if not isinstance(event, Mapping):
                    raise invalid_provider_response_error()

                if message.headers.get(":message-type") in ("exception", "error"):
                    exception_type = str(message.headers.get(":exception-type") or event_type or "")
                    raise _map_stream_error(
                        exception_type,
                        str(
                            event.get("message")
                            or f"Bedrock stream error: {exception_type or 'unknown'}"
                        ),
                    )

                if event_type == "messageStart":
                    if saw_message_start or saw_message_stop:
                        raise invalid_provider_response_error()
                    saw_message_start = True
                elif event_type == "contentBlockStart":
                    if not saw_message_start or saw_message_stop:
                        raise invalid_provider_response_error()
                    start = event.get("start")
                    if not isinstance(start, Mapping):
                        raise invalid_provider_response_error()
                    tool_use = start.get("toolUse")
                    if isinstance(tool_use, dict):
                        if not sent_role:
                            yield _chunk({"role": "assistant", "content": ""})
                            sent_role = True
                        emitted_output = True
                        block_index = _stream_index(event.get("contentBlockIndex"))
                        tool_index = tool_call_indexes.setdefault(
                            block_index, len(tool_call_indexes)
                        )
                        yield _chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": tool_index,
                                        "id": tool_use.get("toolUseId") or "",
                                        "type": "function",
                                        "function": {
                                            "name": tool_use.get("name") or "",
                                            "arguments": "",
                                        },
                                    }
                                ]
                            }
                        )
                elif event_type == "contentBlockDelta":
                    if not saw_message_start or saw_message_stop:
                        raise invalid_provider_response_error()
                    delta = event.get("delta")
                    if not isinstance(delta, Mapping):
                        raise invalid_provider_response_error()
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        if not sent_role:
                            yield _chunk({"role": "assistant", "content": ""})
                            sent_role = True
                        emitted_output = True
                        yield _chunk({"content": text})
                    tool_use_delta = delta.get("toolUse")
                    if isinstance(tool_use_delta, dict):
                        partial = tool_use_delta.get("input")
                        if isinstance(partial, str) and partial:
                            block_index = _stream_index(event.get("contentBlockIndex"))
                            tool_index = tool_call_indexes.get(block_index)
                            if tool_index is not None:
                                yield _chunk(
                                    {
                                        "tool_calls": [
                                            {
                                                "index": tool_index,
                                                "function": {"arguments": partial},
                                            }
                                        ]
                                    }
                                )
                elif event_type == "messageStop":
                    if not saw_message_start or saw_message_stop:
                        raise invalid_provider_response_error()
                    stop_reason = event.get("stopReason") or "end_turn"
                    if failure := _bedrock_stop_failure(stop_reason):
                        if not emitted_output:
                            raise failure
                        finish_reason = _classified_stream_finish_reason(failure)
                    else:
                        finish_reason = _STOP_REASON_MAP.get(str(stop_reason), "stop")
                    saw_message_stop = True
                elif event_type == "metadata":
                    if not saw_message_start or not saw_message_stop:
                        raise invalid_provider_response_error()
                    usage_data = event.get("usage")
                    if not isinstance(usage_data, Mapping) or not all(
                        is_valid_provider_token_count(usage_data.get(key))
                        for key in ("inputTokens", "outputTokens", "totalTokens")
                    ):
                        raise invalid_provider_response_error()
                    usage = {
                        "prompt_tokens": int(usage_data["inputTokens"]),
                        "completion_tokens": int(usage_data["outputTokens"]),
                        "total_tokens": int(usage_data["totalTokens"]),
                    }

        if not saw_message_start or not saw_message_stop:
            raise invalid_provider_response_error()
        if not sent_role:
            yield _chunk({"role": "assistant", "content": ""})
            sent_role = True

        final: dict[str, Any] = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": stream_model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
        if usage:
            final["usage"] = usage
        yield f"data: {json.dumps(final, separators=(',', ':'))}"
        yield "data: [DONE]"

    def map_error(
        self,
        provider_error: Exception,
        *,
        details: ProviderErrorDetails | None = None,
    ) -> ProxyError:
        classification = _classify_bedrock_failure(
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
