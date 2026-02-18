from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from src.models.errors import InvalidRequestError, ServiceUnavailableError, TimeoutError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import ProviderAdapter


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
        upstream_model = provider_config.get("model")
        if upstream_model and "/" in upstream_model:
            payload["model"] = upstream_model.split("/", 1)[1]
        return payload

    async def translate_response(self, provider_response: Any, model_name: str) -> ChatCompletionResponse:
        data = provider_response if isinstance(provider_response, dict) else json.loads(provider_response)
        if "model" not in data:
            data["model"] = model_name
        return ChatCompletionResponse.model_validate(data)

    async def translate_stream(self, provider_stream: AsyncIterator[str]) -> AsyncIterator[str]:
        async for chunk in provider_stream:
            yield chunk

    def map_error(self, provider_error: Exception) -> Exception:
        if isinstance(provider_error, httpx.TimeoutException):
            return TimeoutError()
        if isinstance(provider_error, httpx.HTTPStatusError):
            status = provider_error.response.status_code
            if status >= 500:
                return ServiceUnavailableError(message=f"Provider error: {status}")
            return InvalidRequestError(message=f"Provider rejected request: {status}")
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
