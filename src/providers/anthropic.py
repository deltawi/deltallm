from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx

from src.models.errors import InvalidRequestError, ServiceUnavailableError, TimeoutError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import ProviderAdapter
from src.providers.resolution import resolve_upstream_model


class AnthropicAdapter(ProviderAdapter):
    provider_name = "anthropic"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def translate_request(self, canonical_request: ChatCompletionRequest, provider_config: dict[str, Any]) -> dict[str, Any]:
        system_messages: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for message in canonical_request.messages:
            content = message.content
            if isinstance(content, list):
                text = "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
            else:
                text = str(content)
            role = message.role
            if role == "system":
                if text:
                    system_messages.append(text)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            anthropic_messages.append({"role": role, "content": text})

        upstream_model = resolve_upstream_model(provider_config)

        payload: dict[str, Any] = {
            "model": upstream_model or canonical_request.model,
            "messages": anthropic_messages or [{"role": "user", "content": ""}],
            "max_tokens": canonical_request.max_tokens or int(provider_config.get("max_tokens") or 1024),
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)
        if canonical_request.temperature is not None:
            payload["temperature"] = canonical_request.temperature
        elif canonical_request.top_p is not None:
            payload["top_p"] = canonical_request.top_p
        if canonical_request.stop:
            payload["stop_sequences"] = canonical_request.stop if isinstance(canonical_request.stop, list) else [canonical_request.stop]
        if canonical_request.stream:
            payload["stream"] = True
        return payload

    async def translate_response(self, provider_response: Any, model_name: str) -> ChatCompletionResponse:
        data = provider_response if isinstance(provider_response, dict) else json.loads(provider_response)
        content_blocks = data.get("content") or []
        text_parts: list[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0)
        finish_reason_map = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
        }
        stop_reason = str(data.get("stop_reason") or "end_turn")
        finish_reason = finish_reason_map.get(stop_reason, "stop")
        canonical = {
            "id": data.get("id") or f"chatcmpl-anthropic-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": data.get("model") or model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "".join(text_parts)},
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

    async def translate_stream(self, provider_stream: AsyncIterator[str]) -> AsyncIterator[str]:
        stream_id = f"chatcmpl-anthropic-{int(time.time() * 1000)}"
        model = "anthropic"
        created = int(time.time())
        sent_role = False
        finish_reason: str | None = None

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
                yield "data: [DONE]"
                return

            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            event_type = str(event.get("type") or "")
            if event_type == "message_start":
                message = event.get("message") or {}
                stream_id = str(message.get("id") or stream_id)
                model = str(message.get("model") or model)
                if not sent_role:
                    out = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(out, separators=(',', ':'))}"
                    sent_role = True
                continue

            if event_type == "content_block_delta":
                delta = event.get("delta") or {}
                text = delta.get("text")
                if isinstance(text, str) and text:
                    out = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(out, separators=(',', ':'))}"
                continue

            if event_type == "message_delta":
                delta = event.get("delta") or {}
                stop_reason = str(delta.get("stop_reason") or "")
                finish_map = {
                    "end_turn": "stop",
                    "stop_sequence": "stop",
                    "max_tokens": "length",
                    "tool_use": "tool_calls",
                }
                finish_reason = finish_map.get(stop_reason)
                continue

            if event_type == "message_stop":
                out = {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason or "stop"}],
                }
                yield f"data: {json.dumps(out, separators=(',', ':'))}"
                yield "data: [DONE]"
                return

        yield "data: [DONE]"

    def map_error(self, provider_error: Exception) -> Exception:
        if isinstance(provider_error, httpx.TimeoutException):
            return TimeoutError()
        if isinstance(provider_error, httpx.HTTPStatusError):
            status = provider_error.response.status_code
            if status >= 500:
                return ServiceUnavailableError(message=f"Provider error: {status}")
            return InvalidRequestError(message=f"Provider rejected request: {status}")
        return ServiceUnavailableError(message=str(provider_error))

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        api_key = provider_config.get("api_key")
        if not api_key:
            return False
        api_base = provider_config.get("api_base", "https://api.anthropic.com/v1").rstrip("/")
        version = provider_config.get("api_version") or "2023-06-01"
        try:
            response = await self.http_client.get(
                f"{api_base}/models",
                headers={
                    "x-api-key": str(api_key),
                    "anthropic-version": str(version),
                },
                timeout=10.0,
            )
            return response.status_code < 500
        except Exception:
            return False
