from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from enum import StrEnum
from typing import Protocol

from src.models.errors import InvalidRequestError
from src.providers.base import ProviderAdapter
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.resolution import (
    is_openai_compatible_provider,
    resolve_provider,
    resolve_upstream_model,
)
from src.upstream_auth import build_openai_compatible_auth_headers
from src.upstream_http import configured_timeout_seconds


class ChatAdapterLookup(Protocol):
    def resolve(self, provider: str) -> ProviderAdapter | None: ...


class OptionalGenerationControl(StrEnum):
    UNKNOWN = "unknown"
    SUPPORTED = "supported"


@dataclass(frozen=True, slots=True)
class ChatGenerationProfile:
    """Provider-owned proof for optional controls; unknown defaults remain portable."""

    zero_temperature: OptionalGenerationControl = OptionalGenerationControl.UNKNOWN
    json_object: OptionalGenerationControl = OptionalGenerationControl.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.zero_temperature, OptionalGenerationControl) or not isinstance(
            self.json_object, OptionalGenerationControl
        ):
            raise ValueError("invalid chat generation profile")


@dataclass(frozen=True, slots=True)
class ChatUpstream:
    adapter: ProviderAdapter = field(repr=False)
    api_base: str = field(repr=False)
    endpoint: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    timeout: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


def resolve_chat_upstream_from_registry(
    adapters: ChatAdapterLookup,
    params: Mapping[str, object],
    *,
    default_openai_base_url: str,
    is_stream: bool = False,
) -> ChatUpstream:
    provider = resolve_provider(params)
    adapter = adapters.resolve(provider)
    if adapter is None:
        raise InvalidRequestError(message=f"Unsupported provider '{provider}' for chat endpoint")
    timeout = configured_timeout_seconds(params.get("timeout"))
    if provider == "bedrock":
        region = str(params.get("region") or "us-east-1")
        action = "converse-stream" if is_stream else "converse"
        return ChatUpstream(
            adapter=adapter,
            api_base=str(
                params.get("api_base") or f"https://bedrock-runtime.{region}.amazonaws.com"
            ).rstrip("/"),
            endpoint=f"/model/{resolve_upstream_model(params)}/{action}",
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")
    profile = CHAT_PROVIDER_PROFILES.get(provider)
    if profile is not None:
        return ChatUpstream(
            adapter=adapter,
            api_base=str(params.get("api_base") or profile.api_base).rstrip("/"),
            endpoint="/chat/completions",
            headers=build_openai_compatible_auth_headers(
                provider=provider, api_key=str(api_key), content_type="application/json"
            ),
            timeout=timeout,
        )
    if provider in {"anthropic", "azure", "azure_openai", "gemini"}:
        return _native_upstream(
            adapter,
            params,
            provider=provider,
            api_key=str(api_key),
            default_openai_base_url=default_openai_base_url,
            timeout=timeout,
            is_stream=is_stream,
        )
    if provider not in {"unknown", ""} and not is_openai_compatible_provider(provider):
        raise InvalidRequestError(message=f"Unsupported provider '{provider}' for chat endpoint")
    return ChatUpstream(
        adapter=adapter,
        api_base=str(params.get("api_base", default_openai_base_url)).rstrip("/"),
        endpoint="/chat/completions",
        headers=build_openai_compatible_auth_headers(
            provider=provider,
            api_key=str(api_key),
            auth_header_name=params.get("auth_header_name"),
            auth_header_format=params.get("auth_header_format"),
            content_type="application/json",
        ),
        timeout=timeout,
    )


def _native_upstream(
    adapter: ProviderAdapter,
    params: Mapping[str, object],
    *,
    provider: str,
    api_key: str,
    default_openai_base_url: str,
    timeout: float | None,
    is_stream: bool,
) -> ChatUpstream:
    if provider == "anthropic":
        return ChatUpstream(
            adapter=adapter,
            api_base=str(params.get("api_base") or "https://api.anthropic.com/v1").rstrip("/"),
            endpoint="/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": str(params.get("api_version") or "2023-06-01"),
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    if provider in {"azure", "azure_openai"}:
        return ChatUpstream(
            adapter=adapter,
            api_base=str(params.get("api_base", default_openai_base_url)).rstrip("/"),
            endpoint="/chat/completions",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
    if is_stream:
        raise InvalidRequestError(message="Gemini streaming is not supported yet")
    return ChatUpstream(
        adapter=adapter,
        api_base=str(
            params.get("api_base") or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/"),
        endpoint=f"/models/{resolve_upstream_model(params)}:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
