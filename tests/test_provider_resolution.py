from __future__ import annotations

import pytest

from src.config_runtime.models import ModelHotReloadManager
from src.providers.resolution import (
    is_openai_compatible_provider,
    provider_supports_stream_usage_request,
    provider_from_model,
    provider_supports_mode,
    resolve_provider,
    resolve_provider_required_chat_output_tokens,
    resolve_upstream_model,
)


def test_resolve_provider_prefers_explicit_provider() -> None:
    params = {"provider": "anthropic", "model": "openai/gpt-4o-mini"}
    assert resolve_provider(params) == "anthropic"


def test_resolve_provider_falls_back_to_model_prefix() -> None:
    params = {"model": "openai/gpt-4o-mini"}
    assert resolve_provider(params) == "openai"
    assert provider_from_model("anthropic/claude-sonnet-4") == "anthropic"


def test_resolve_provider_required_chat_output_tokens_matches_anthropic_payload() -> None:
    params = {
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4",
        "max_tokens": 4096,
    }

    assert resolve_provider_required_chat_output_tokens(params, 512) == 512
    assert resolve_provider_required_chat_output_tokens(params, None) == 4096
    assert (
        resolve_provider_required_chat_output_tokens(
            {"provider": "anthropic", "model": "anthropic/claude-sonnet-4"},
            None,
        )
        == 1024
    )
    assert (
        resolve_provider_required_chat_output_tokens(
            {"provider": "openai", "model": "openai/gpt-4o-mini"},
            None,
        )
        is None
    )


def test_provider_supports_mode_unknown_is_permissive() -> None:
    assert provider_supports_mode("custom-gateway", "chat") is True


def test_elevenlabs_supports_audio_modes_only() -> None:
    assert provider_supports_mode("elevenlabs", "audio_speech") is True
    assert provider_supports_mode("elevenlabs", "audio_transcription") is True
    assert provider_supports_mode("elevenlabs", "chat") is False
    assert provider_supports_mode("elevenlabs", "embedding") is False
    assert provider_supports_mode("elevenlabs", "image_generation") is False
    assert provider_supports_mode("elevenlabs", "rerank") is False


def test_vllm_rerank_capability_matches_supported_provider_endpoint() -> None:
    assert provider_supports_mode("vllm", "rerank") is True


def test_resolve_upstream_model_preserves_slash_prefixed_ids_for_groq() -> None:
    params = {"provider": "groq", "model": "openai/gpt-oss-120b"}
    assert resolve_upstream_model(params) == "openai/gpt-oss-120b"


def test_resolve_upstream_model_strips_openai_prefix_for_openai() -> None:
    params = {"provider": "openai", "model": "openai/gpt-4o-mini"}
    assert resolve_upstream_model(params) == "gpt-4o-mini"


def test_resolve_upstream_model_strips_anthropic_prefix_for_anthropic() -> None:
    params = {"provider": "anthropic", "model": "anthropic/claude-sonnet-4-20250514"}
    assert resolve_upstream_model(params) == "claude-sonnet-4-20250514"


def test_resolve_upstream_model_strips_elevenlabs_prefix_for_elevenlabs() -> None:
    params = {"provider": "elevenlabs", "model": "elevenlabs/eleven_multilingual_v2"}
    assert resolve_upstream_model(params) == "eleven_multilingual_v2"


def test_openai_compatible_registry_contains_common_gateways() -> None:
    assert is_openai_compatible_provider("openrouter") is True
    assert is_openai_compatible_provider("groq") is True
    assert is_openai_compatible_provider("anthropic") is False
    assert is_openai_compatible_provider("elevenlabs") is False


def test_stream_usage_request_capability_includes_vllm_but_not_openrouter() -> None:
    assert provider_supports_stream_usage_request("openai") is True
    assert provider_supports_stream_usage_request("vllm") is True
    assert provider_supports_stream_usage_request("openrouter") is False


def test_model_validation_rejects_unsupported_provider_mode_combo() -> None:
    with pytest.raises(ValueError, match="does not support mode"):
        ModelHotReloadManager._validate_model_config(
            {
                "model_name": "embed-only-test",
                "deltallm_params": {
                    "provider": "anthropic",
                    "model": "anthropic/claude-sonnet-4-20250514",
                    "api_key": "x",
                },
                "model_info": {"mode": "embedding"},
            }
        )
