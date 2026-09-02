from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from src.models.errors import FailureClassification, ProxyError
from src.models.request_serialization import dump_openai_chat_request
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import (
    ProviderAdapter,
    ProviderErrorDetails,
    classify_provider_failure,
    map_standard_provider_error,
    provider_error_details,
    reject_openai_compatible_failure_response,
)
from src.providers.healthcheck import is_provider_healthy
from src.providers.openai_compatible import (
    translate_openai_compatible_stream,
    validate_openai_compatible_chat_success,
)
from src.providers.resolution import (
    normalize_openai_chat_payload,
    resolve_provider,
    resolve_upstream_model,
)

_CONTEXT_IDENTIFIERS = frozenset(
    {
        "context_length_exceeded",
        "context_window_exceeded",
        "input_too_long",
    }
)
_CONTENT_IDENTIFIERS = frozenset(
    {
        "content_filter",
        "content_policy_violation",
        "responsibleaipolicyviolation",
    }
)
_CONTEXT_MESSAGE_MARKERS = ("maximum context length", "too many tokens")
_CONTENT_MESSAGE_MARKERS = ("content management policy", "responsible ai policy")


class AzureOpenAIAdapter(ProviderAdapter):
    provider_name = "azure_openai"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def translate_request(
        self, canonical_request: ChatCompletionRequest, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        payload = dump_openai_chat_request(canonical_request)
        if payload.get("tool_choice") is not None and not payload.get("tools"):
            payload.pop("tool_choice", None)
        provider = resolve_provider(provider_config)
        upstream_model = resolve_upstream_model(provider_config)
        if upstream_model:
            payload["model"] = upstream_model
        normalize_openai_chat_payload(
            payload,
            provider=provider,
            upstream_model=upstream_model or str(payload.get("model") or ""),
        )
        return payload

    async def translate_response(
        self, provider_response: Any, model_name: str
    ) -> ChatCompletionResponse:
        data = (
            provider_response
            if isinstance(provider_response, dict)
            else json.loads(provider_response)
        )
        reject_openai_compatible_failure_response(data)
        validate_openai_compatible_chat_success(data)
        if "model" not in data:
            data["model"] = model_name
        return ChatCompletionResponse.model_validate(data)

    async def translate_stream(
        self,
        provider_stream: AsyncIterator[str],
        *,
        model_name: str | None = None,
    ) -> AsyncIterator[str]:
        async for chunk in translate_openai_compatible_stream(
            provider_stream,
            classify_failure=self._classify_failure,
        ):
            yield chunk

    @staticmethod
    def _classify_failure(details: ProviderErrorDetails) -> FailureClassification | None:
        return classify_provider_failure(
            details,
            context_identifiers=_CONTEXT_IDENTIFIERS,
            content_identifiers=_CONTENT_IDENTIFIERS,
            context_message_markers=_CONTEXT_MESSAGE_MARKERS,
            content_message_markers=_CONTENT_MESSAGE_MARKERS,
        )

    def map_error(
        self,
        provider_error: Exception,
        *,
        details: ProviderErrorDetails | None = None,
    ) -> ProxyError:
        classification = self._classify_failure(details or provider_error_details(provider_error))
        return map_standard_provider_error(
            provider_error,
            failure_classification=classification,
        )

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        return await is_provider_healthy(
            self.http_client,
            provider_config,
            default_openai_base_url="https://api.openai.com/v1",
            default_provider=self.provider_name,
        )
