from __future__ import annotations

from typing import Any, AsyncIterator

from src.models.errors import ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import ProviderAdapter


class AnthropicAdapter(ProviderAdapter):
    provider_name = "anthropic"

    async def translate_request(self, canonical_request: ChatCompletionRequest, provider_config: dict[str, Any]) -> dict[str, Any]:
        raise ServiceUnavailableError(message="Anthropic adapter not implemented yet")

    async def translate_response(self, provider_response: Any, model_name: str) -> ChatCompletionResponse:
        raise ServiceUnavailableError(message="Anthropic adapter not implemented yet")

    async def translate_stream(self, provider_stream: AsyncIterator[str]) -> AsyncIterator[str]:
        if False:
            yield ""
        raise ServiceUnavailableError(message="Anthropic adapter not implemented yet")

    def map_error(self, provider_error: Exception) -> Exception:
        return ServiceUnavailableError(message=str(provider_error))

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        return False
