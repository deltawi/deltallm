from __future__ import annotations

from collections.abc import Mapping

from src.config import ModelDeployment, ModelMode
from src.metrics.prometheus import sanitize_label

# Providers that are generally expected to expose OpenAI-compatible APIs.
OPENAI_COMPATIBLE_PROVIDERS = {
    "openai",
    "openrouter",
    "groq",
    "together",
    "fireworks",
    "deepinfra",
    "perplexity",
    "vllm",
    "lmstudio",
    "ollama",
    "azure",
    "azure_openai",
}

PROVIDER_MODEL_PREFIXES_TO_STRIP: dict[str, tuple[str, ...]] = {
    "openai": ("openai/",),
    "anthropic": ("anthropic/",),
    "azure": ("azure/", "azure_openai/"),
    "azure_openai": ("azure/", "azure_openai/"),
    "gemini": ("gemini/",),
    "bedrock": ("bedrock/",),
}

PROVIDER_CAPABILITIES: dict[str, set[ModelMode]] = {
    "openai": {"chat", "embedding", "image_generation", "audio_speech", "audio_transcription"},
    "anthropic": {"chat"},
    "azure": {"chat", "embedding", "image_generation", "audio_speech", "audio_transcription"},
    "azure_openai": {"chat", "embedding", "image_generation", "audio_speech", "audio_transcription"},
    "openrouter": {"chat", "embedding", "image_generation"},
    "groq": {"chat", "embedding", "audio_speech", "audio_transcription"},
    "together": {"chat", "embedding", "image_generation"},
    "fireworks": {"chat", "embedding", "image_generation"},
    "deepinfra": {"chat", "embedding", "image_generation"},
    "perplexity": {"chat"},
    "gemini": {"chat"},
    "bedrock": {"chat"},
    "vllm": {"chat", "embedding", "image_generation", "audio_speech", "audio_transcription"},
    "lmstudio": {"chat", "embedding"},
    "ollama": {"chat", "embedding"},
}

PROVIDER_PRESETS: dict[str, dict[str, str | None]] = {
    "openai": {"provider": "openai", "api_base": "https://api.openai.com/v1", "compat": "openai"},
    "anthropic": {"provider": "anthropic", "api_base": "https://api.anthropic.com/v1", "compat": "anthropic"},
    "azure_openai": {"provider": "azure_openai", "api_base": "https://{resource}.openai.azure.com/openai/v1", "compat": "openai"},
    "openrouter": {"provider": "openrouter", "api_base": "https://openrouter.ai/api/v1", "compat": "openai"},
    "groq": {"provider": "groq", "api_base": "https://api.groq.com/openai/v1", "compat": "openai"},
    "together": {"provider": "together", "api_base": "https://api.together.xyz/v1", "compat": "openai"},
    "fireworks": {"provider": "fireworks", "api_base": "https://api.fireworks.ai/inference/v1", "compat": "openai"},
    "deepinfra": {"provider": "deepinfra", "api_base": "https://api.deepinfra.com/v1/openai", "compat": "openai"},
    "perplexity": {"provider": "perplexity", "api_base": "https://api.perplexity.ai", "compat": "openai"},
    "gemini": {"provider": "gemini", "api_base": "https://generativelanguage.googleapis.com/v1beta", "compat": "native"},
    "bedrock": {"provider": "bedrock", "api_base": "https://bedrock-runtime.{region}.amazonaws.com", "compat": "native"},
    "vllm": {"provider": "vllm", "api_base": None, "compat": "openai"},
    "lmstudio": {"provider": "lmstudio", "api_base": None, "compat": "openai"},
    "ollama": {"provider": "ollama", "api_base": None, "compat": "openai"},
}


def provider_from_model(model: str | None) -> str:
    value = (model or "").strip()
    if "/" in value:
        return sanitize_label(value.split("/", 1)[0]).lower()
    return "unknown"


def resolve_provider(params: Mapping[str, object] | None) -> str:
    if not params:
        return "unknown"

    explicit = params.get("provider")
    if explicit is not None and str(explicit).strip():
        return sanitize_label(str(explicit)).lower()

    return provider_from_model(str(params.get("model") or ""))


def resolve_upstream_model(params: Mapping[str, object] | None, fallback_model: str | None = None) -> str:
    if not params:
        return (fallback_model or "").strip()

    upstream_model = str(params.get("model") or fallback_model or "").strip()
    if not upstream_model:
        return ""

    provider = resolve_provider(params)
    lowered = upstream_model.lower()
    for prefix in PROVIDER_MODEL_PREFIXES_TO_STRIP.get(provider, ()):
        if lowered.startswith(prefix):
            return upstream_model[len(prefix):]
    return upstream_model


def provider_supports_mode(provider: str, mode: ModelMode) -> bool:
    caps = PROVIDER_CAPABILITIES.get((provider or "").strip().lower())
    if caps is None:
        # Unknown providers are allowed by default so existing deployments keep working.
        return True
    return mode in caps


def is_openai_compatible_provider(provider: str) -> bool:
    return (provider or "").strip().lower() in OPENAI_COMPATIBLE_PROVIDERS


def provider_presets() -> list[dict[str, str | None | list[str]]]:
    items: list[dict[str, str | None | list[str]]] = []
    for key in sorted(PROVIDER_PRESETS.keys()):
        preset = PROVIDER_PRESETS[key]
        items.append(
            {
                **preset,
                "supported_modes": sorted(PROVIDER_CAPABILITIES.get(key, set())),
            }
        )
    return items


def validate_provider_mode_compatibility(config: dict[str, object]) -> None:
    deployment = ModelDeployment.model_validate(config)
    mode = deployment.model_info.mode if deployment.model_info else "chat"
    provider = resolve_provider(deployment.deltallm_params.model_dump(exclude_none=True))
    if not provider_supports_mode(provider, mode):
        raise ValueError(f"Provider '{provider}' does not support mode '{mode}'")
