from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx

from src.models.errors import InvalidRequestError, ServiceUnavailableError, TimeoutError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import ProviderAdapter


class BedrockAdapter(ProviderAdapter):
    provider_name = "bedrock"

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
        if canonical_request.temperature is not None:
            inference_config["temperature"] = canonical_request.temperature
        if canonical_request.top_p is not None:
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

        stop_reason_map = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "guardrail_intervened": "content_filter",
            "content_filtered": "content_filter",
        }
        finish_reason = stop_reason_map.get(str(data.get("stopReason") or "end_turn"), "stop")

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

    async def translate_stream(self, provider_stream: AsyncIterator[str]) -> AsyncIterator[str]:
        if False:
            yield ""
        raise InvalidRequestError(message="Bedrock streaming is not supported yet")

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
        access_key = provider_config.get("aws_access_key_id")
        secret_key = provider_config.get("aws_secret_access_key")
        region = provider_config.get("region")
        return bool(access_key and secret_key and region)
