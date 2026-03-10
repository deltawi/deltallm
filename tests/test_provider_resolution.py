from __future__ import annotations

import pytest

from src.config_runtime.models import ModelHotReloadManager
from src.providers.resolution import (
    is_openai_compatible_provider,
    provider_from_model,
    provider_supports_mode,
    resolve_provider,
    resolve_upstream_model,
)


def test_resolve_provider_prefers_explicit_provider() -> None:
    params = {"provider": "anthropic", "model": "openai/gpt-4o-mini"}
    assert resolve_provider(params) == "anthropic"


def test_resolve_provider_falls_back_to_model_prefix() -> None:
    params = {"model": "openai/gpt-4o-mini"}
    assert resolve_provider(params) == "openai"
    assert provider_from_model("anthropic/claude-sonnet-4") == "anthropic"


def test_provider_supports_mode_unknown_is_permissive() -> None:
    assert provider_supports_mode("custom-gateway", "chat") is True


def test_resolve_upstream_model_preserves_slash_prefixed_ids_for_groq() -> None:
    params = {"provider": "groq", "model": "openai/gpt-oss-120b"}
    assert resolve_upstream_model(params) == "openai/gpt-oss-120b"


def test_resolve_upstream_model_strips_openai_prefix_for_openai() -> None:
    params = {"provider": "openai", "model": "openai/gpt-4o-mini"}
    assert resolve_upstream_model(params) == "gpt-4o-mini"


def test_resolve_upstream_model_strips_anthropic_prefix_for_anthropic() -> None:
    params = {"provider": "anthropic", "model": "anthropic/claude-sonnet-4-20250514"}
    assert resolve_upstream_model(params) == "claude-sonnet-4-20250514"


def test_openai_compatible_registry_contains_common_gateways() -> None:
    assert is_openai_compatible_provider("openrouter") is True
    assert is_openai_compatible_provider("groq") is True
    assert is_openai_compatible_provider("anthropic") is False


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
