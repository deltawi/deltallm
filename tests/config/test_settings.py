from __future__ import annotations

import pytest

from src.config import (
    AppConfig,
    ChatBatchingConfig,
    DeltaLLMParams,
    GeneralSettings,
    ModelDeployment,
    ModelInfo,
    RouteGroupConfig,
    Settings,
    resolve_app_config_with_secrets,
    resolve_database_settings,
    resolve_salt_key,
)
from src.upstream_http import (
    build_control_request_timeout,
    build_health_check_request_timeout,
    build_upstream_http_limits,
    build_upstream_http_timeout,
    build_upstream_request_timeout,
    configured_timeout_seconds,
)


def test_master_key_validation_accepts_strong_values():
    strong = "StrongMasterKey2026SecureTokenABCD1234"
    cfg = AppConfig.model_validate({"general_settings": {"master_key": strong}})
    settings = Settings.model_validate({"master_key": strong})
    assert cfg.general_settings.master_key == strong
    assert settings.master_key == strong


def test_master_key_validation_rejects_short_or_weak_values():
    with pytest.raises(ValueError, match="at least 32"):
        AppConfig.model_validate({"general_settings": {"master_key": "short-master-key"}})
    with pytest.raises(ValueError, match="letters and digits"):
        Settings.model_validate({"master_key": "OnlyLettersMasterKeyWithoutDigitsLongEnough"})


def test_resolve_app_config_with_secrets_wraps_secret_resolution_errors():
    class BrokenResolver:
        def resolve_tree(self, value):
            del value
            raise RuntimeError("secret backend exploded")

    with pytest.raises(ValueError, match="Failed to resolve configuration secrets"):
        resolve_app_config_with_secrets({"general_settings": {"master_key": "StrongMasterKey2026SecureTokenABCD1234"}}, secret_resolver=BrokenResolver())


def test_resolve_app_config_with_secrets_wraps_validation_errors():
    class PassthroughResolver:
        def resolve_tree(self, value):
            return value

    with pytest.raises(ValueError, match="Resolved configuration is invalid"):
        resolve_app_config_with_secrets({"general_settings": {"cache_ttl": "not-an-int"}}, secret_resolver=PassthroughResolver())


def test_resolve_salt_key_uses_general_settings_value(monkeypatch):
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("DELTALLM_SALT_KEY", raising=False)
    cfg = AppConfig.model_validate({"general_settings": {"salt_key": "cfg-salt-123"}})
    settings = Settings.model_validate({"salt_key": "env-salt-123"})
    assert resolve_salt_key(cfg, settings) == "cfg-salt-123"


def test_resolve_salt_key_falls_back_to_environment_settings(monkeypatch):
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("DELTALLM_SALT_KEY", raising=False)
    cfg = AppConfig.model_validate({"general_settings": {}})
    settings = Settings.model_validate({"salt_key": "env-salt-123"})
    assert resolve_salt_key(cfg, settings) == "env-salt-123"


def test_resolve_salt_key_rejects_missing_values(monkeypatch):
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("DELTALLM_SALT_KEY", raising=False)
    cfg = AppConfig.model_validate({"general_settings": {}})
    settings = Settings()
    with pytest.raises(ValueError, match="Salt key is required"):
        resolve_salt_key(cfg, settings)


def test_resolve_salt_key_rejects_change_me_default(monkeypatch):
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("DELTALLM_SALT_KEY", raising=False)
    cfg = AppConfig.model_validate({"general_settings": {"salt_key": "change-me"}})
    settings = Settings.model_validate({})
    with pytest.raises(ValueError, match="Insecure salt key"):
        resolve_salt_key(cfg, settings)


def test_resolve_database_settings_prefers_env_over_config(monkeypatch):
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = AppConfig.model_validate(
        {
            "general_settings": {
                "database_url": "postgresql://cfg-user:cfg-pass@cfg-host:5432/cfg-db?schema=public",
                "db_pool_size": 7,
                "db_pool_timeout": 14,
            }
        }
    )
    settings = Settings.model_validate(
        {
            "database_url": "postgresql://env-user:env-pass@env-host:5432/env-db?sslmode=require",
            "db_pool_size": 11,
            "db_pool_timeout": 22,
        }
    )

    resolved = resolve_database_settings(cfg, settings)

    assert resolved is not None
    assert resolved.pool_size == 11
    assert resolved.pool_timeout == 22
    assert resolved.url == (
        "postgresql://env-user:env-pass@env-host:5432/env-db"
        "?sslmode=require&connection_limit=11&pool_timeout=22"
    )


def test_resolve_database_settings_uses_database_url_env_fallback(monkeypatch):
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime:secret@db:5432/deltallm?schema=public")
    cfg = AppConfig.model_validate({"general_settings": {"db_pool_size": 9, "db_pool_timeout": 12}})
    settings = Settings.model_validate({})

    resolved = resolve_database_settings(cfg, settings)

    assert resolved is not None
    assert resolved.pool_size == 9
    assert resolved.pool_timeout == 12
    assert resolved.url == (
        "postgresql://runtime:secret@db:5432/deltallm"
        "?schema=public&connection_limit=9&pool_timeout=12"
    )


def test_resolve_database_settings_returns_none_without_database_url(monkeypatch):
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = AppConfig.model_validate({"general_settings": {}})
    settings = Settings.model_validate({})

    assert resolve_database_settings(cfg, settings) is None


def test_settings_load_database_pool_overrides_from_environment(monkeypatch):
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.setenv("DELTALLM_DB_POOL_SIZE", "13")
    monkeypatch.setenv("DELTALLM_DB_POOL_TIMEOUT", "21")

    settings = Settings()

    assert settings.db_pool_size == 13
    assert settings.db_pool_timeout == 21


def test_general_settings_upstream_http_defaults_build_httpx_config():
    settings = GeneralSettings()

    timeout = build_upstream_http_timeout(settings)
    limits = build_upstream_http_limits(settings)

    assert timeout.connect == 10.0
    assert timeout.read == 300.0
    assert timeout.write == 30.0
    assert timeout.pool == 10.0
    assert limits.max_connections == 500
    assert limits.max_keepalive_connections == 100
    assert limits.keepalive_expiry == 60.0


def test_general_settings_accepts_custom_upstream_http_values():
    settings = GeneralSettings.model_validate(
        {
            "upstream_http_connect_timeout_seconds": 6,
            "upstream_http_read_timeout_seconds": 120,
            "upstream_http_write_timeout_seconds": 25,
            "upstream_http_pool_timeout_seconds": 3,
            "upstream_http_max_connections": 80,
            "upstream_http_max_keepalive_connections": 20,
            "upstream_http_keepalive_expiry_seconds": 15,
        }
    )

    timeout = build_upstream_http_timeout(settings)
    limits = build_upstream_http_limits(settings)

    assert timeout.connect == 6.0
    assert timeout.read == 120.0
    assert timeout.write == 25.0
    assert timeout.pool == 3.0
    assert limits.max_connections == 80
    assert limits.max_keepalive_connections == 20
    assert limits.keepalive_expiry == 15.0


def test_general_settings_rejects_invalid_upstream_http_pool_limits():
    with pytest.raises(ValueError, match="upstream_http_max_keepalive_connections"):
        GeneralSettings.model_validate(
            {
                "upstream_http_max_connections": 10,
                "upstream_http_max_keepalive_connections": 11,
            }
        )


@pytest.mark.parametrize(
    "field",
    [
        "upstream_http_connect_timeout_seconds",
        "upstream_http_read_timeout_seconds",
        "upstream_http_write_timeout_seconds",
        "upstream_http_pool_timeout_seconds",
        "upstream_http_max_connections",
    ],
)
def test_general_settings_rejects_non_positive_upstream_http_values(field: str):
    with pytest.raises(ValueError):
        GeneralSettings.model_validate({field: 0})


def test_upstream_request_timeout_preserves_pool_timeout_with_deployment_override():
    settings = GeneralSettings.model_validate(
        {
            "upstream_http_connect_timeout_seconds": 4,
            "upstream_http_write_timeout_seconds": 9,
            "upstream_http_pool_timeout_seconds": 2,
        }
    )

    timeout = build_upstream_request_timeout(settings, 180)

    assert timeout.connect == 4.0
    assert timeout.read == 180.0
    assert timeout.write == 9.0
    assert timeout.pool == 2.0


def test_upstream_request_timeout_uses_general_fallback_without_override():
    settings = GeneralSettings.model_validate(
        {
            "upstream_http_connect_timeout_seconds": 7,
            "upstream_http_read_timeout_seconds": 84,
            "upstream_http_write_timeout_seconds": 11,
            "upstream_http_pool_timeout_seconds": 2,
        }
    )

    timeout = build_upstream_request_timeout(settings, None)

    assert timeout.connect == 7.0
    assert timeout.read == 84.0
    assert timeout.write == 11.0
    assert timeout.pool == 2.0


def test_config_model_without_timeout_does_not_create_deployment_override():
    deployment = ModelDeployment.model_validate(
        {
            "model_name": "gpt-4o-mini",
            "deltallm_params": {
                "model": "openai/gpt-4o-mini",
                "api_key": "provider-key",
            },
        }
    )

    params = deployment.deltallm_params.model_dump(exclude_none=True)

    assert "timeout" not in params


def test_health_check_request_timeout_caps_pool_below_wrapper_timeout():
    settings = GeneralSettings.model_validate(
        {
            "upstream_http_pool_timeout_seconds": 30,
        }
    )

    timeout = build_health_check_request_timeout(
        settings,
        read_timeout_seconds=10,
        health_check_timeout_seconds=5,
    )

    assert timeout.read == 10.0
    assert timeout.pool == 4.0


def test_control_request_timeout_preserves_control_pool_timeout():
    timeout = build_control_request_timeout(20)

    assert timeout.connect == 5.0
    assert timeout.read == 20.0
    assert timeout.write == 10.0
    assert timeout.pool == 5.0


def test_configured_timeout_seconds_only_uses_explicit_positive_values():
    assert configured_timeout_seconds(None) is None
    assert configured_timeout_seconds("") is None
    assert configured_timeout_seconds(0) is None
    assert configured_timeout_seconds("12.5") == 12.5


def test_model_info_accepts_valid_upstream_max_batch_inputs():
    assert ModelInfo.model_validate({}).upstream_max_batch_inputs is None
    assert ModelInfo.model_validate({"upstream_max_batch_inputs": 1}).upstream_max_batch_inputs == 1
    assert ModelInfo.model_validate({"upstream_max_batch_inputs": 8}).upstream_max_batch_inputs == 8


@pytest.mark.parametrize("value", [0, -1])
def test_model_info_rejects_non_positive_upstream_max_batch_inputs(value: int):
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        ModelInfo.model_validate({"upstream_max_batch_inputs": value})


def test_delta_llm_params_accepts_chat_batching_config():
    params = DeltaLLMParams.model_validate(
        {
            "provider": "vllm",
            "model": "vllm/llama-3.1-8b",
            "chat_batching": {
                "mode": "concurrent",
                "max_in_flight": 32,
            },
        }
    )

    assert params.chat_batching is not None
    assert params.chat_batching.mode == "concurrent"
    assert params.chat_batching.max_in_flight == 32
    assert params.model_dump(exclude_none=True)["chat_batching"] == {
        "mode": "concurrent",
        "max_in_flight": 32,
        "require_homogeneous_params": True,
    }


def test_chat_batching_config_accepts_sync_microbatch_with_limits():
    config = ChatBatchingConfig.model_validate(
        {
            "mode": "sync_microbatch",
            "upstream_max_batch_size": 8,
            "max_total_input_tokens": 32000,
            "require_homogeneous_params": True,
        }
    )

    assert config.mode == "sync_microbatch"
    assert config.upstream_max_batch_size == 8
    assert config.max_total_input_tokens == 32000


@pytest.mark.parametrize("mode", ["native_async_batch", "provider_native", "unknown"])
def test_chat_batching_config_rejects_unknown_modes(mode: str):
    with pytest.raises(ValueError):
        ChatBatchingConfig.model_validate({"mode": mode})


@pytest.mark.parametrize("upstream_max_batch_size", [None, 1])
def test_chat_batching_config_rejects_sync_microbatch_without_batch_size(upstream_max_batch_size: int | None):
    with pytest.raises(ValueError, match="upstream_max_batch_size"):
        ChatBatchingConfig.model_validate(
            {
                "mode": "sync_microbatch",
                "upstream_max_batch_size": upstream_max_batch_size,
            }
        )


def test_chat_batching_config_rejects_sync_microbatch_without_homogeneous_params():
    with pytest.raises(ValueError, match="require_homogeneous_params"):
        ChatBatchingConfig.model_validate(
            {
                "mode": "sync_microbatch",
                "upstream_max_batch_size": 8,
                "require_homogeneous_params": False,
            }
        )


@pytest.mark.parametrize(
    "field",
    ["max_in_flight", "upstream_max_batch_size", "max_total_input_tokens"],
)
def test_chat_batching_config_rejects_non_positive_limits(field: str):
    with pytest.raises(ValueError):
        ChatBatchingConfig.model_validate({field: 0})


def test_model_info_normalizes_access_groups():
    info = ModelInfo.model_validate({"access_groups": ["Beta", "support", "beta"]})

    assert info.access_groups == ["beta", "support"]
    assert info.model_dump()["access_groups"] == ["beta", "support"]


@pytest.mark.parametrize(
    "value",
    [
        "beta",
        [1],
        ["bad group"],
    ],
)
def test_model_info_rejects_invalid_access_groups(value: object):
    with pytest.raises(ValueError, match="access"):
        ModelInfo.model_validate({"access_groups": value})


def test_route_group_config_normalizes_access_groups():
    group = RouteGroupConfig.model_validate(
        {
            "key": "support-fast",
            "access_groups": ["Support", "support", "beta"],
            "members": [{"deployment_id": "dep-1"}],
        }
    )

    assert group.access_groups == ["beta", "support"]
    assert group.model_dump()["access_groups"] == ["beta", "support"]


@pytest.mark.parametrize(
    "value",
    [
        "support",
        [object()],
        ["bad group"],
    ],
)
def test_route_group_config_rejects_invalid_access_groups(value: object):
    with pytest.raises(ValueError, match="access"):
        RouteGroupConfig.model_validate({"key": "support-fast", "access_groups": value})
