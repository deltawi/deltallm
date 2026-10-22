"""LiteLLM provider prefixes and their DeltaLLM equivalents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

BaseRule = Literal["keep", "azure_v1", "append_v1", "required_append_v1"]


@dataclass(frozen=True, slots=True)
class ProviderTarget:
    """How one LiteLLM provider prefix becomes a DeltaLLM deployment.

    `provider=None` writes a generic OpenAI-compatible deployment without a provider
    field. DeltaLLM reads an unprefixed model with no provider as OpenAI-compatible.
    """

    provider: str | None
    api_base: str | None = None
    base_rule: BaseRule = "keep"
    note: str | None = None
    # LiteLLM reads this variable when api_key is missing. DeltaLLM needs it explicitly.
    default_key_env: str | None = None
    # Self-hosted servers often need no key. A stand-in stops the deployment from
    # inheriting the gateway's OpenAI key (DeltaLLM sends a key on every call).
    keyless: bool = False


_GENERIC_NOTE = (
    "DeltaLLM has no named preset for this provider. It is imported as a generic "
    "OpenAI-compatible deployment. Test it before you send traffic."
)

PROVIDER_TARGETS: dict[str, ProviderTarget] = {
    "openai": ProviderTarget("openai", default_key_env="OPENAI_API_KEY"),
    "azure": ProviderTarget("azure_openai", base_rule="azure_v1", default_key_env="AZURE_API_KEY"),
    "anthropic": ProviderTarget("anthropic", default_key_env="ANTHROPIC_API_KEY"),
    "bedrock": ProviderTarget("bedrock"),
    "gemini": ProviderTarget("gemini", default_key_env="GEMINI_API_KEY"),
    "groq": ProviderTarget("groq", default_key_env="GROQ_API_KEY"),
    "together_ai": ProviderTarget("together", default_key_env="TOGETHERAI_API_KEY"),
    "fireworks_ai": ProviderTarget("fireworks", default_key_env="FIREWORKS_AI_API_KEY"),
    "deepinfra": ProviderTarget("deepinfra", default_key_env="DEEPINFRA_API_KEY"),
    "perplexity": ProviderTarget("perplexity", default_key_env="PERPLEXITYAI_API_KEY"),
    "openrouter": ProviderTarget("openrouter", default_key_env="OPENROUTER_API_KEY"),
    "mistral": ProviderTarget("mistral", default_key_env="MISTRAL_API_KEY"),
    "deepseek": ProviderTarget("deepseek", default_key_env="DEEPSEEK_API_KEY"),
    "dashscope": ProviderTarget("qwen", default_key_env="DASHSCOPE_API_KEY"),
    "minimax": ProviderTarget("minimax", default_key_env="MINIMAX_API_KEY"),
    "zai": ProviderTarget("zai", default_key_env="ZAI_API_KEY"),
    "elevenlabs": ProviderTarget("elevenlabs", default_key_env="ELEVENLABS_API_KEY"),
    "hosted_vllm": ProviderTarget("vllm", base_rule="required_append_v1", keyless=True),
    "vllm": ProviderTarget(
        "vllm",
        base_rule="required_append_v1",
        note="LiteLLM `vllm/` runs vLLM in process. DeltaLLM calls a vLLM server at api_base.",
        keyless=True,
    ),
    "lm_studio": ProviderTarget("lmstudio", base_rule="required_append_v1", keyless=True),
    "ollama": ProviderTarget(
        "ollama", api_base="http://localhost:11434", base_rule="append_v1", keyless=True
    ),
    "ollama_chat": ProviderTarget(
        "ollama",
        api_base="http://localhost:11434",
        base_rule="append_v1",
        keyless=True,
    ),
    "xai": ProviderTarget(
        None, api_base="https://api.x.ai/v1", note=_GENERIC_NOTE, default_key_env="XAI_API_KEY"
    ),
    "cerebras": ProviderTarget(
        None,
        api_base="https://api.cerebras.ai/v1",
        note=_GENERIC_NOTE,
        default_key_env="CEREBRAS_API_KEY",
    ),
    "sambanova": ProviderTarget(
        None,
        api_base="https://api.sambanova.ai/v1",
        note=_GENERIC_NOTE,
        default_key_env="SAMBANOVA_API_KEY",
    ),
    "nvidia_nim": ProviderTarget(
        None,
        api_base="https://integrate.api.nvidia.com/v1",
        note=_GENERIC_NOTE,
        default_key_env="NVIDIA_NIM_API_KEY",
    ),
    "moonshot": ProviderTarget(
        None,
        api_base="https://api.moonshot.ai/v1",
        note=_GENERIC_NOTE,
        default_key_env="MOONSHOT_API_KEY",
    ),
    "azure_ai": ProviderTarget(None, note=_GENERIC_NOTE),
    "litellm_proxy": ProviderTarget(None, note=_GENERIC_NOTE),
    "custom_openai": ProviderTarget(None, note=_GENERIC_NOTE),
}

# Prefixes that DeltaLLM cannot serve today. The value says why, in the report.
UNSUPPORTED_PROVIDERS: dict[str, str] = {
    "vertex_ai": "Vertex AI is not supported yet. Use `gemini/` with an API key, or Bedrock.",
    "vertex_ai_beta": "Vertex AI is not supported yet.",
    "text-completion-openai": "legacy text completions are not supported.",
    "azure_text": "legacy text completions are not supported.",
    "cohere": "Cohere is not supported yet.",
    "cohere_chat": "Cohere is not supported yet.",
    "huggingface": "Hugging Face Inference is not supported yet.",
    "sagemaker": "SageMaker is not supported yet.",
    "sagemaker_chat": "SageMaker is not supported yet.",
    "watsonx": "watsonx is not supported yet.",
    "replicate": "Replicate is not supported yet.",
    "databricks": "Databricks is not supported yet.",
    "voyage": "Voyage AI is not supported yet.",
    "jina_ai": "Jina AI is not supported yet.",
    "cloudflare": "Cloudflare Workers AI is not supported yet.",
    "ai21": "AI21 is not supported yet.",
    "nlp_cloud": "NLP Cloud is not supported yet.",
    "aleph_alpha": "Aleph Alpha is not supported yet.",
    "petals": "Petals is not supported.",
    "palm": "PaLM is retired.",
}

# LiteLLM infers the provider of an unprefixed model name. DeltaLLM needs it explicitly.
_INFERRED_PREFIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "gpt-",
            "o1",
            "o3",
            "o4",
            "chatgpt-",
            "text-embedding-",
            "dall-e-",
            "gpt-image-",
            "whisper-",
            "tts-",
            "omni-moderation",
        ),
        "openai",
    ),
    (("claude-",), "anthropic"),
)


def split_litellm_model(model: str, custom_provider: str | None) -> tuple[str | None, str]:
    """Return the LiteLLM provider prefix and the upstream model ID."""

    value = model.strip()
    if custom_provider:
        prefix = custom_provider.strip().lower()
        if value.lower().startswith(f"{prefix}/"):
            value = value[len(prefix) + 1 :]
        return prefix, value
    if "/" in value:
        prefix, rest = value.split("/", 1)
        if prefix.lower() in PROVIDER_TARGETS or prefix.lower() in UNSUPPORTED_PROVIDERS:
            return prefix.lower(), rest
    lowered = value.lower()
    for prefixes, provider in _INFERRED_PREFIXES:
        if lowered.startswith(prefixes):
            return provider, value
    return None, value


def normalize_api_base(target: ProviderTarget, api_base: str | None) -> tuple[str | None, bool]:
    """Apply the provider's base URL rule. Return the base and whether it changed.

    Env references (`os.environ/NAME`) are kept unchanged: their value is unknown here.
    """

    base = api_base or target.api_base
    if base is None or base.startswith("os.environ/") or target.base_rule == "keep":
        return base, base != api_base
    parts = urlsplit(base.strip())
    path = parts.path.rstrip("/")
    if target.base_rule == "azure_v1":
        if "/openai/deployments/" in f"{path}/" or path.endswith("/openai/deployments"):
            path = path.split("/openai/deployments", 1)[0] + "/openai/v1"
        elif path.endswith("/openai"):
            path = f"{path}/v1"
        elif not path:
            path = "/openai/v1"
    elif not path:
        path = "/v1"
    normalized = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return normalized, normalized != api_base
