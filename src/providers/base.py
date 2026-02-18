from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse


class ProviderAdapter(ABC):
    provider_name: str

    @abstractmethod
    async def translate_request(
        self,
        canonical_request: ChatCompletionRequest,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def translate_response(self, provider_response: Any, model_name: str) -> ChatCompletionResponse:
        raise NotImplementedError

    @abstractmethod
    async def translate_stream(self, provider_stream: AsyncIterator[str]) -> AsyncIterator[str]:
        raise NotImplementedError

    @abstractmethod
    def map_error(self, provider_error: Exception) -> Exception:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        raise NotImplementedError
