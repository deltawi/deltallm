from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx
from botocore.eventstream import EventStreamBuffer

from src.models.errors import InvalidRequestError
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
        for message in canonical_request.messages:
            content = message.content
            if isinstance(content, list):
                text = "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
            else:
                text = str(content)
            if message.role == "system":
                if text:
                    system_blocks.append({"text": text})
                continue
            role = "assistant" if message.role == "assistant" else "user"
            messages.append({"role": role, "content": [{"text": text}]})

        payload: dict[str, Any] = {"messages": messages or [{"role": "user", "content": [{"text": ""}]}]}
        if system_blocks:
            payload["system"] = system_blocks

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

        finish_reason = _STOP_REASON_MAP.get(str(data.get("stopReason") or "end_turn"), "stop")

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
                    "message": {"role": "assistant", "content": text},
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
                    raise InvalidRequestError(
                        message=str(event.get("message") or f"Bedrock stream error: {event_type}")
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
