from __future__ import annotations

import pytest

from src.config import AppConfig, Settings, resolve_app_config_with_secrets, resolve_salt_key


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
