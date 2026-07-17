from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx
from botocore.eventstream import EventStreamBuffer

from src.models.errors import InvalidRequestError, RateLimitError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import ProviderAdapter, map_standard_provider_error, provider_http_error_message
from src.providers.healthcheck import is_provider_healthy

_STOP_REASON_MAP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "guardrail_intervened": "content_filter",
    "content_filtered": "content_filter",
}


def _flatten_text_content(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
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


def _map_stream_error(exception_type: str, message: str) -> Exception:
    normalized = exception_type.lower()
    if "throttling" in normalized:
        return RateLimitError(message=message, affects_deployment_health=True)
    if "validation" in normalized:
        return InvalidRequestError(message=message, affects_deployment_health=False)
    return ServiceUnavailableError(message=message, affects_deployment_health=True)


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
                    [{"toolResult": {"toolUseId": message.tool_call_id or "", "content": [{"text": text}]}}],
                )
                continue
            role = "assistant" if message.role == "assistant" else "user"
            blocks: list[dict[str, Any]] = []
            if text:
                blocks.append({"text": text})
            if role == "assistant":
                blocks.extend(_tool_call_to_tool_use_block(tool_call) for tool_call in message.tool_calls or [])
            if not blocks and role == "user":
                blocks.append({"text": text})
            append_blocks(role, blocks)

        payload: dict[str, Any] = {"messages": messages or [{"role": "user", "content": [{"text": ""}]}]}
        if system_blocks:
            payload["system"] = system_blocks
        if canonical_request.tools and canonical_request.tool_choice != "none":
            tool_config: dict[str, Any] = {"tools": [_function_tool_to_tool_spec(tool) for tool in canonical_request.tools]}
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
            inference_config["stopSequences"] = canonical_request.stop if isinstance(canonical_request.stop, list) else [canonical_request.stop]
        if inference_config:
            payload["inferenceConfig"] = inference_config

        return payload

    async def translate_response(self, provider_response: Any, model_name: str) -> ChatCompletionResponse:
        data = provider_response if isinstance(provider_response, dict) else json.loads(provider_response)
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

        finish_reason = _STOP_REASON_MAP.get(str(data.get("stopReason") or "end_turn"), "stop")
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

    async def translate_stream(self, provider_stream: AsyncIterator[bytes]) -> AsyncIterator[str]:
        stream_id = f"chatcmpl-bedrock-{int(time.time() * 1000)}"
        created = int(time.time())
        buffer = EventStreamBuffer()
        sent_role = False
        finish_reason = "stop"
        usage: dict[str, int] = {}

        def _chunk(delta: dict[str, Any], *, stop: str | None = None) -> str:
            body: dict[str, Any] = {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": "bedrock",
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
                except json.JSONDecodeError:
                    continue

                if message.headers.get(":message-type") in ("exception", "error"):
                    exception_type = str(message.headers.get(":exception-type") or event_type or "")
                    raise _map_stream_error(
                        exception_type,
                        str(event.get("message") or f"Bedrock stream error: {exception_type or 'unknown'}"),
                    )

                if event_type == "messageStart":
                    if not sent_role:
                        yield _chunk({"role": "assistant", "content": ""})
                        sent_role = True
                elif event_type == "contentBlockStart":
                    tool_use = (event.get("start") or {}).get("toolUse")
                    if isinstance(tool_use, dict):
                        index = int(event.get("contentBlockIndex") or 0)
                        yield _chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": tool_use.get("toolUseId") or "",
                                        "type": "function",
                                        "function": {"name": tool_use.get("name") or "", "arguments": ""},
                                    }
                                ]
                            }
                        )
                elif event_type == "contentBlockDelta":
                    delta = event.get("delta") or {}
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        yield _chunk({"content": text})
                    tool_use_delta = delta.get("toolUse")
                    if isinstance(tool_use_delta, dict):
                        partial = tool_use_delta.get("input")
                        if isinstance(partial, str) and partial:
                            index = int(event.get("contentBlockIndex") or 0)
                            yield _chunk({"tool_calls": [{"index": index, "function": {"arguments": partial}}]})
                elif event_type == "messageStop":
                    finish_reason = _STOP_REASON_MAP.get(str(event.get("stopReason") or "end_turn"), "stop")
                elif event_type == "metadata":
                    usage_data = event.get("usage") or {}
                    usage = {
                        "prompt_tokens": int(usage_data.get("inputTokens") or 0),
                        "completion_tokens": int(usage_data.get("outputTokens") or 0),
                        "total_tokens": int(usage_data.get("totalTokens") or 0),
                    }

        final: dict[str, Any] = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "bedrock",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
        if usage:
            final["usage"] = usage
        yield f"data: {json.dumps(final, separators=(',', ':'))}"
        yield "data: [DONE]"

    def map_error(self, provider_error: Exception) -> Exception:
        status = provider_error.response.status_code if isinstance(provider_error, httpx.HTTPStatusError) else None
        invalid_request_message = (
            provider_http_error_message(provider_error, fallback=f"Provider rejected request: {status}")
            if isinstance(provider_error, httpx.HTTPStatusError)
            else f"Provider rejected request: {status}"
        )
        return map_standard_provider_error(
            provider_error,
            invalid_request_message=invalid_request_message,
            rate_limit_message=invalid_request_message if status == 429 else f"Provider rate limited request: {status}",
        )

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        return await is_provider_healthy(
            self.http_client,
            provider_config,
            default_openai_base_url="https://api.openai.com/v1",
            default_provider=self.provider_name,
        )
