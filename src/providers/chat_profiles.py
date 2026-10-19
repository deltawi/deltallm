from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from src.config import ModelMode


@dataclass(frozen=True, slots=True)
class ChatProviderProfile:
    provider: str
    api_base: str
    discovery: Literal["openai", "qwen", "catalog", "mistral"] = "openai"
    stream_usage: bool = True
    supported_modes: tuple[ModelMode, ...] = ("chat",)


# Protocol metadata only: client lifetimes, credentials, and retries retain their
# existing owners. Each profile declares its supported model modes.
CHAT_PROVIDER_PROFILES: Mapping[str, ChatProviderProfile] = MappingProxyType(
    {
        profile.provider: profile
        for profile in (
            ChatProviderProfile("deepseek", "https://api.deepseek.com"),
            ChatProviderProfile(
                "zai", "https://api.z.ai/api/paas/v4", discovery="catalog", stream_usage=False
            ),
            ChatProviderProfile(
                "qwen",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                discovery="qwen",
            ),
            ChatProviderProfile("tencent", "https://tokenhub-intl.tencentcloudmaas.com/v1"),
            ChatProviderProfile("minimax", "https://api.minimax.io/v1"),
            ChatProviderProfile(
                "mistral",
                "https://api.mistral.ai/v1",
                discovery="mistral",
                stream_usage=False,
                supported_modes=("chat", "embedding"),
            ),
        )
    }
)
