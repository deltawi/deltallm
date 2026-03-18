from __future__ import annotations

import pytest

from src.config import (
    AppConfig,
    Settings,
    resolve_app_config_with_secrets,
    resolve_database_settings,
    resolve_salt_key,
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
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = AppConfig.model_validate({"general_settings": {}})
    settings = Settings.model_validate({})

    assert resolve_database_settings(cfg, settings) is None


def test_settings_load_database_pool_overrides_from_environment(monkeypatch):
    monkeypatch.setenv("DELTALLM_DB_POOL_SIZE", "13")
    monkeypatch.setenv("DELTALLM_DB_POOL_TIMEOUT", "21")

    settings = Settings()

    assert settings.db_pool_size == 13
    assert settings.db_pool_timeout == 21
