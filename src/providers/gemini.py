from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx

from src.models.errors import InvalidRequestError, ServiceUnavailableError, TimeoutError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import ProviderAdapter


class GeminiAdapter(ProviderAdapter):
    provider_name = "gemini"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def translate_request(
        self,
        canonical_request: ChatCompletionRequest,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []
        for message in canonical_request.messages:
            role = message.role
            content = message.content
            if isinstance(content, list):
                text = "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
            else:
                text = str(content)
            if role == "system":
                if text:
                    system_parts.append({"text": text})
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})

        payload: dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}]}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        generation_config: dict[str, Any] = {}
        if canonical_request.temperature is not None:
            generation_config["temperature"] = canonical_request.temperature
        if canonical_request.top_p is not None:
            generation_config["topP"] = canonical_request.top_p
        if canonical_request.max_tokens is not None:
            generation_config["maxOutputTokens"] = canonical_request.max_tokens
        if canonical_request.stop:
            generation_config["stopSequences"] = canonical_request.stop if isinstance(canonical_request.stop, list) else [canonical_request.stop]
        if generation_config:
            payload["generationConfig"] = generation_config

        return payload

    async def translate_response(self, provider_response: Any, model_name: str) -> ChatCompletionResponse:
        data = provider_response if isinstance(provider_response, dict) else json.loads(provider_response)
        candidates = data.get("candidates") or []
        first = candidates[0] if candidates else {}
        content = first.get("content") or {}
        parts = content.get("parts") or []
        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
        finish_reason_map = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
        }
        finish_reason = finish_reason_map.get(str(first.get("finishReason") or "STOP"), "stop")

        usage = data.get("usageMetadata") or {}
        prompt_tokens = int(usage.get("promptTokenCount") or 0)
        completion_tokens = int(usage.get("candidatesTokenCount") or 0)
        total_tokens = int(usage.get("totalTokenCount") or (prompt_tokens + completion_tokens))

        canonical = {
            "id": data.get("responseId") or f"chatcmpl-gemini-{int(time.time())}",
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
        # Native Gemini stream translation is not implemented in this phase.
        if False:
            yield ""
        raise InvalidRequestError(message="Gemini streaming is not supported yet")

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
        api_base = str(provider_config.get("api_base") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        api_key = provider_config.get("api_key")
        if not api_key:
            return False
        try:
            response = await self.http_client.get(
                f"{api_base}/models?key={api_key}",
                timeout=10.0,
            )
            return response.status_code < 500
        except Exception:
            return False
