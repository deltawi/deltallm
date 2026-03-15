from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from src.models.errors import InvalidRequestError, ServiceUnavailableError, TimeoutError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import ProviderAdapter
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
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
                if message:
                    return message
        body = response.text.strip()
        if body:
            return body
        return f"Provider rejected request: {response.status_code}"

    def map_error(self, provider_error: Exception) -> Exception:
        if isinstance(provider_error, httpx.TimeoutException):
            return TimeoutError()
        if isinstance(provider_error, httpx.HTTPStatusError):
            status = provider_error.response.status_code
            if status >= 500:
                return ServiceUnavailableError(message=f"Provider error: {status}")
            return InvalidRequestError(message=self._http_error_message(provider_error))
        return ServiceUnavailableError(message="Provider unavailable")

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        api_base = provider_config.get("api_base", "https://api.openai.com/v1")
        api_key = provider_config.get("api_key")
        if not api_key:
            return False
        try:
            response = await self.http_client.get(
                f"{api_base}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            return response.status_code < 500
        except Exception:
            return False
