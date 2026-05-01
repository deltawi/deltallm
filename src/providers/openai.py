from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import ProviderAdapter, map_standard_provider_error
from src.providers.healthcheck import is_provider_healthy
from src.providers.resolution import normalize_openai_chat_payload, resolve_provider, resolve_upstream_model


class OpenAIAdapter(ProviderAdapter):
    provider_name = "openai"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def translate_request(
        self,
        canonical_request: ChatCompletionRequest,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        payload = canonical_request.model_dump(exclude_none=True)
        if payload.get("tool_choice") is not None and not payload.get("tools"):
            payload.pop("tool_choice", None)
        provider = resolve_provider(provider_config)
        upstream_model = resolve_upstream_model(provider_config)
        if upstream_model:
            payload["model"] = upstream_model
        normalize_openai_chat_payload(payload, provider=provider, upstream_model=upstream_model or str(payload.get("model") or ""))
        return payload

    async def translate_response(self, provider_response: Any, model_name: str) -> ChatCompletionResponse:
        data = provider_response if isinstance(provider_response, dict) else json.loads(provider_response)
        if "model" not in data:
            data["model"] = model_name
        choices = data.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    continue
                if "content" not in message or message.get("content") is None:
                    message["content"] = ""
        return ChatCompletionResponse.model_validate(data)

    async def translate_stream(self, provider_stream: AsyncIterator[str]) -> AsyncIterator[str]:
        async for chunk in provider_stream:
            yield chunk

    @staticmethod
    def _http_error_message(provider_error: httpx.HTTPStatusError) -> str:
        response = provider_error.response
        try:
            payload = response.json()
        except (AttributeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
                if message:
                    return message
        body = str(getattr(response, "text", "") or "").strip()
        if body:
            return body
        return f"Provider rejected request: {response.status_code}"

    def map_error(self, provider_error: Exception) -> Exception:
        status = provider_error.response.status_code if isinstance(provider_error, httpx.HTTPStatusError) else None
        message = (
            self._http_error_message(provider_error)
            if isinstance(provider_error, httpx.HTTPStatusError) and status is not None and status < 500
            else "Provider unavailable"
        )
        return map_standard_provider_error(
            provider_error,
            invalid_request_message=message,
            unavailable_message="Provider unavailable",
            rate_limit_message=message if status == 429 else None,
        )

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        return await is_provider_healthy(
            self.http_client,
            provider_config,
            default_openai_base_url="https://api.openai.com/v1",
            default_provider=self.provider_name,
        )
