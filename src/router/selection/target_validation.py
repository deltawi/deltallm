from collections.abc import Mapping

from src.providers.resolution import (
    is_openai_compatible_provider,
    resolve_provider,
    resolve_upstream_model,
)


def qualify_chat_target(parameters: Mapping[str, object]) -> None:
    model = resolve_upstream_model(parameters)
    if not isinstance(model, str) or not 1 <= len(model.strip()) <= 256:
        raise ValueError("selector members require a bounded concrete provider model")
    provider = resolve_provider(parameters)
    if not is_openai_compatible_provider(provider) and provider not in {
        "azure",
        "azure_openai",
        "anthropic",
        "gemini",
        "bedrock",
    }:
        raise ValueError("selector members require a supported chat provider")
