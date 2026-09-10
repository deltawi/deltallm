from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field, ValidationError

from src.providers.chat_profiles import ChatProviderProfile

from src.providers.discovery_runtime import (
    ProviderDiscoveryRuntime,
    validated_discovery_base,
)

MAX_DISCOVERY_MODELS = 500
QWEN_DISCOVERY_PAGE_SIZE = 100


class DiscoveredChatModel(BaseModel):
    id: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=512)
    status: str | None = None


class _OpenAIModelList(BaseModel):
    data: list[DiscoveredChatModel] = Field(max_length=MAX_DISCOVERY_MODELS)


class _QwenModel(BaseModel):
    model: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=512)
    capabilities: list[str] = Field(default_factory=list, max_length=64)


class _QwenOutput(BaseModel):
    total: int = Field(ge=0)
    models: list[_QwenModel] = Field(max_length=QWEN_DISCOVERY_PAGE_SIZE)


class _QwenModelList(BaseModel):
    output: _QwenOutput


@dataclass(frozen=True, slots=True)
class ChatModelDiscovery:
    models: tuple[DiscoveredChatModel, ...]
    truncated: bool = False


def chat_models_url(profile: ChatProviderProfile, api_base: str) -> str:
    base = validated_discovery_base(api_base)
    if profile.discovery == "catalog":
        raise NotImplementedError(
            "Live model discovery is unavailable; showing the curated catalog."
        )
    if profile.discovery == "qwen":
        suffix = "/compatible-mode/v1"
        if not base.endswith(suffix):
            raise NotImplementedError(
                "Live Qwen discovery requires an API base ending in /compatible-mode/v1."
            )
        return (
            f"{base[: -len(suffix)]}/api/v1/models"
            f"?capabilities=TG&page_no=1&page_size={QWEN_DISCOVERY_PAGE_SIZE}"
        )
    return f"{base}/models"


def parse_chat_models(profile: ChatProviderProfile, body: bytes) -> ChatModelDiscovery:
    try:
        if profile.discovery == "qwen":
            output = _QwenModelList.model_validate_json(body).output
            models = tuple(
                DiscoveredChatModel(id=item.model, name=item.name)
                for item in output.models
                if not item.capabilities or {"TG", "Reasoning"}.intersection(item.capabilities)
            )
            return ChatModelDiscovery(models, truncated=output.total > len(output.models))
        items = _OpenAIModelList.model_validate_json(body).data
        return ChatModelDiscovery(
            tuple(
                item for item in items if item.status not in {"offline", "deprecated", "disabled"}
            )
        )
    except (ValidationError, ValueError) as exc:
        # Validation errors can embed upstream body values, so never propagate
        # their text into the discovery warning or health response.
        raise ValueError("Provider model discovery returned an invalid model list") from exc


async def fetch_chat_models(
    runtime: ProviderDiscoveryRuntime,
    *,
    profile: ChatProviderProfile,
    api_base: str,
    api_key: str,
    timeout: httpx.Timeout,
) -> ChatModelDiscovery:
    url = chat_models_url(profile, api_base)
    body = await runtime.fetch(url, api_key=api_key, timeout=timeout)
    return parse_chat_models(profile, body)
