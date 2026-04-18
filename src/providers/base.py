from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from src.models.errors import (
    InvalidRequestError,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
    parse_retry_after_header,
)
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse


def map_standard_provider_error(
    provider_error: Exception,
    *,
    invalid_request_message: str,
    unavailable_message: str | None = None,
    rate_limit_message: str | None = None,
) -> Exception:
    if isinstance(provider_error, httpx.TimeoutException):
        return TimeoutError(affects_deployment_health=True)

    if isinstance(provider_error, httpx.HTTPStatusError):
        status = provider_error.response.status_code
        if status == 429:
            return RateLimitError(
                message=rate_limit_message or invalid_request_message,
                retry_after=parse_retry_after_header(provider_error.response.headers.get("retry-after")),
                affects_deployment_health=True,
            )
        if status >= 500:
            return ServiceUnavailableError(
                message=f"Provider error: {status}",
                affects_deployment_health=True,
            )
        return InvalidRequestError(
            message=invalid_request_message,
            affects_deployment_health=False,
        )

    return ServiceUnavailableError(
        message=unavailable_message or str(provider_error),
        affects_deployment_health=True,
    )


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
