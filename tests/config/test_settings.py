from __future__ import annotations

import base64

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


def test_batch_advisory_lock_mode_validation() -> None:
    cfg = AppConfig.model_validate(
        {"general_settings": {"embeddings_batch_advisory_lock_mode": "canonical"}}
    )
    assert cfg.general_settings.embeddings_batch_advisory_lock_mode == "canonical"

    with pytest.raises(ValueError, match="embeddings_batch_advisory_lock_mode"):
        AppConfig.model_validate(
            {"general_settings": {"embeddings_batch_advisory_lock_mode": "legacy"}}
        )


def test_batch_webhook_defaults_are_safe() -> None:
    settings = GeneralSettings()

    assert settings.batch_webhook_enabled is False
    assert settings.batch_webhook_worker_enabled is True
    assert settings.batch_webhook_observability_enabled is True
    assert settings.batch_webhook_observability_refresh_interval_seconds == 15.0
    assert settings.batch_webhook_encryption_key is None
    assert settings.batch_webhook_allowed_ports == [443]
    assert settings.batch_webhook_allowed_private_cidrs == []
    assert settings.batch_webhook_allow_http is False
    assert settings.batch_webhook_delivery_retention_days == 30
    assert settings.batch_webhook_cleanup_max_rows_per_run == 10_000


def test_batch_webhook_enabled_requires_valid_encryption_key() -> None:
    with pytest.raises(ValueError, match="requires batch_webhook_encryption_key"):
        GeneralSettings(batch_webhook_enabled=True)

    with pytest.raises(ValueError, match="decode to exactly 32 bytes"):
        GeneralSettings(
            batch_webhook_enabled=True,
            batch_webhook_encryption_key=base64.urlsafe_b64encode(b"too-short").decode(),
        )

    encoded_key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
    settings = GeneralSettings(
        batch_webhook_enabled=True,
        batch_webhook_encryption_key=encoded_key,
        batch_webhook_allowed_ports=[443, 8443, 443],
        batch_webhook_allowed_private_cidrs=["10.0.0.1/8", "fd00::1/8"],
    )

    assert settings.batch_webhook_encryption_key is not None
    assert settings.batch_webhook_encryption_key.get_secret_value() == encoded_key
    assert settings.batch_webhook_allowed_ports == [443, 8443]
    assert settings.batch_webhook_allowed_private_cidrs == ["10.0.0.0/8", "fd00::/8"]


def test_batch_webhook_encryption_key_validation_hides_sensitive_input() -> None:
    sensitive_key = "sensitive-invalid-encryption-key"
    with pytest.raises(ValueError) as exc_info:
        GeneralSettings(
            batch_webhook_enabled=True,
            batch_webhook_encryption_key=sensitive_key,
        )

    assert sensitive_key not in str(exc_info.value)
    assert sensitive_key not in repr(exc_info.value)


def test_batch_webhook_rejects_invalid_delivery_settings() -> None:
    with pytest.raises(ValueError, match="retry_max_seconds"):
        GeneralSettings(
            batch_webhook_retry_initial_seconds=10,
            batch_webhook_retry_max_seconds=5,
        )

    with pytest.raises(ValueError, match="lease_seconds"):
        GeneralSettings(batch_webhook_lease_seconds=10, batch_webhook_timeout_seconds=10)

    with pytest.raises(ValueError, match="valid IPv4 or IPv6 CIDRs"):
        GeneralSettings(batch_webhook_allowed_private_cidrs=["not-a-network"])

    encoded_key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
    with pytest.raises(ValueError, match="at least one allowed webhook port"):
        GeneralSettings(
            batch_webhook_enabled=True,
            batch_webhook_encryption_key=encoded_key,
            batch_webhook_allowed_ports=[],
        )


def test_self_registration_defaults_are_disabled() -> None:
    settings = GeneralSettings()

    assert settings.self_registration.enabled is False
    assert settings.self_registration.allowed_domains == []
    assert settings.self_registration.default_org.id is None
    assert settings.self_registration.default_team.id is None
    assert settings.self_registration.default_team.role == "team_developer"


def test_self_registration_enabled_config_normalizes_values() -> None:
    cfg = AppConfig.model_validate(
        {
            "general_settings": {
                "self_registration": {
                    "enabled": True,
                    "mode": "sso_allowed_domain",
                    "allowed_domains": ["Example.COM", "example.com", "Engineering.Internal"],
                    "default_org": {
                        "id": " org-sandbox ",
                        "name": " Developer Sandbox ",
                        "max_budget": 100,
                        "soft_budget": 80,
                        "rpm_limit": 300,
                    },
                    "default_team": {
                        "id": " team-self-serve ",
                        "alias": " Self Serve ",
                        "role": "team_developer",
                        "self_service_max_keys_per_user": 2,
                        "self_service_budget_ceiling": 5,
                        "self_service_require_expiry": True,
                        "self_service_max_expiry_days": 14,
                    },
                    "default_user": {
                        "max_budget": 10,
                        "soft_budget": 8,
                        "rpm_limit": 30,
                        "tpm_limit": 50_000,
                    },
                }
            }
        }
    )

    registration = cfg.general_settings.self_registration
    assert registration.enabled is True
    assert registration.allowed_domains == ["example.com", "engineering.internal"]
    assert registration.default_org.id == "org-sandbox"
    assert registration.default_org.name == "Developer Sandbox"
    assert registration.default_team.id == "team-self-serve"
    assert registration.default_team.alias == "Self Serve"
    assert registration.default_team.self_service_budget_ceiling == 5
    assert registration.default_user.max_budget == 10


def test_self_registration_enabled_requires_default_org_and_team() -> None:
    with pytest.raises(ValueError, match="default_org.id"):
        AppConfig.model_validate(
            {
                "general_settings": {
                    "self_registration": {
                        "enabled": True,
                        "allowed_domains": ["example.com"],
                        "default_team": {"id": "team-self-serve"},
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="default_team.id"):
        AppConfig.model_validate(
            {
                "general_settings": {
                    "self_registration": {
                        "enabled": True,
                        "allowed_domains": ["example.com"],
                        "default_org": {"id": "org-sandbox"},
                    }
                }
            }
        )


def test_self_registration_sso_allowed_domain_requires_domains() -> None:
    with pytest.raises(ValueError, match="allowed_domains"):
        AppConfig.model_validate(
            {
                "general_settings": {
                    "self_registration": {
                        "enabled": True,
                        "mode": "sso_allowed_domain",
                        "default_org": {"id": "org-sandbox"},
                        "default_team": {"id": "team-self-serve"},
                    }
                }
            }
        )


def test_self_registration_rejects_invalid_domains_and_limits() -> None:
    with pytest.raises(ValueError, match="bare email domains"):
        AppConfig.model_validate(
            {
                "general_settings": {
                    "self_registration": {
                        "enabled": True,
                        "allowed_domains": ["@example.com"],
                        "default_org": {"id": "org-sandbox"},
                        "default_team": {"id": "team-self-serve"},
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="greater than 0"):
        AppConfig.model_validate(
            {
                "general_settings": {
                    "self_registration": {
                        "enabled": True,
                        "allowed_domains": ["example.com"],
                        "default_org": {"id": "org-sandbox", "rpm_limit": 0},
                        "default_team": {"id": "team-self-serve"},
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="soft_budget"):
        AppConfig.model_validate(
            {
                "general_settings": {
                    "self_registration": {
                        "enabled": True,
                        "allowed_domains": ["example.com"],
                        "default_org": {
                            "id": "org-sandbox",
                            "max_budget": 10,
                            "soft_budget": 12,
                        },
                        "default_team": {"id": "team-self-serve"},
                    }
                }
            }
        )


def test_resolve_app_config_with_secrets_wraps_secret_resolution_errors():
    class BrokenResolver:
        def resolve_tree(self, value):
            del value
            raise RuntimeError("secret backend exploded")

    with pytest.raises(ValueError, match="Failed to resolve configuration secrets") as exc_info:
        resolve_app_config_with_secrets({"general_settings": {"master_key": "StrongMasterKey2026SecureTokenABCD1234"}}, secret_resolver=BrokenResolver())
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_resolve_app_config_with_secrets_wraps_validation_errors():
    class PassthroughResolver:
        def resolve_tree(self, value):
            return value

    sensitive_key = "sensitive-invalid-encryption-key"
    with pytest.raises(ValueError, match="Resolved configuration is invalid") as exc_info:
        resolve_app_config_with_secrets(
            {"general_settings": {"batch_webhook_encryption_key": sensitive_key}},
            secret_resolver=PassthroughResolver(),
        )
    assert sensitive_key not in str(exc_info.value)
    assert sensitive_key not in repr(exc_info.value)
    assert "general_settings.batch_webhook_encryption_key" in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_resolve_app_config_with_secrets_preserves_safe_validation_diagnostics():
    class PassthroughResolver:
        def resolve_tree(self, value):
            return value

    with pytest.raises(ValueError) as exc_info:
        resolve_app_config_with_secrets(
            {"general_settings": {"cache_ttl": "not-an-integer"}},
            secret_resolver=PassthroughResolver(),
        )

    rendered = str(exc_info.value)
    assert "general_settings.cache_ttl" in rendered
    assert "valid integer" in rendered
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


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


def test_general_settings_spend_reporting_safety_defaults() -> None:
    settings = GeneralSettings()

    assert settings.spend_reporting_max_concurrency == 2
    assert settings.spend_reporting_global_max_concurrency == 2
    assert settings.spend_reporting_queue_timeout_seconds == 10.0
    assert settings.spend_reporting_execution_timeout_seconds == 60.0
    assert settings.spend_reporting_redis_timeout_seconds == 0.5
    assert settings.spend_reporting_v2_enabled is False


@pytest.mark.parametrize(
    "field",
    [
        "spend_reporting_max_concurrency",
        "spend_reporting_global_max_concurrency",
        "spend_reporting_queue_timeout_seconds",
        "spend_reporting_execution_timeout_seconds",
        "spend_reporting_redis_timeout_seconds",
    ],
)
def test_general_settings_rejects_non_positive_spend_reporting_limits(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        GeneralSettings.model_validate({field: 0})


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


def test_model_info_accepts_valid_batch_capacity():
    info = ModelInfo.model_validate(
        {
            "batch_capacity": {
                "max_in_flight": 4,
                "max_claim_work_units": 200,
                "capacity_fraction": 0.25,
            }
        }
    )

    assert info.batch_capacity is not None
    assert info.batch_capacity.max_in_flight == 4
    assert info.batch_capacity.max_claim_work_units == 200
    assert info.batch_capacity.capacity_fraction == 0.25


@pytest.mark.parametrize(
    "batch_capacity",
    [
        {"max_in_flight": 0},
        {"max_claim_work_units": 0},
        {"capacity_fraction": 0},
        {"capacity_fraction": 1.01},
        {"max_in_flight": True},
        {"max_claim_work_units": "4"},
    ],
)
def test_model_info_rejects_invalid_batch_capacity(batch_capacity: dict[str, object]):
    with pytest.raises(ValueError):
        ModelInfo.model_validate({"batch_capacity": batch_capacity})


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
