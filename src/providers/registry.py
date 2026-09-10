from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from fastapi import Request

from src.models.errors import ProxyError
from src.providers.base import ProviderAdapter, map_standard_provider_error
from src.providers.resolution import is_openai_compatible_provider
from src.providers.chat_upstream import ChatUpstream, resolve_chat_upstream_from_registry


@dataclass(frozen=True, slots=True)
class ProviderErrorMapperRegistry:
    """Resolve provider-owned error classifiers for every routed endpoint."""

    openai: ProviderAdapter
    azure_openai: ProviderAdapter
    anthropic: ProviderAdapter
    gemini: ProviderAdapter
    bedrock: ProviderAdapter
    compatible_chat: Mapping[str, ProviderAdapter]

    def __post_init__(self) -> None:
        object.__setattr__(self, "compatible_chat", MappingProxyType(dict(self.compatible_chat)))

    def resolve(self, provider: str) -> ProviderAdapter | None:
        normalized = (provider or "").strip().lower()
        if normalized in self.compatible_chat:
            return self.compatible_chat[normalized]
        if normalized in {"azure", "azure_openai"}:
            return self.azure_openai
        if normalized == "anthropic":
            return self.anthropic
        if normalized == "gemini":
            return self.gemini
        if normalized == "bedrock":
            return self.bedrock
        if normalized == "elevenlabs":
            return None
        if normalized in {"", "unknown"} or is_openai_compatible_provider(normalized):
            return self.openai
        return None

    def map_error(self, provider: str, error: Exception) -> ProxyError:
        adapter = self.resolve(provider)
        if adapter is None:
            return map_standard_provider_error(error)
        return adapter.map_error(error)


def resolve_chat_upstream(
    request: Request,
    params: dict[str, Any],
    *,
    is_stream: bool = False,
) -> ChatUpstream:
    """Resolve legacy transport dependencies once and delegate provider policy."""
    return resolve_chat_upstream_from_registry(
        request.app.state.provider_error_mapper_registry,
        params,
        default_openai_base_url=request.app.state.settings.openai_base_url,
        is_stream=is_stream,
    )
