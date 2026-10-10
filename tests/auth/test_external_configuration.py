from __future__ import annotations

import json
import pytest
from pydantic import ValidationError

from src.auth.external_config import ExternalAuthSettings
from src.config import GeneralSettings, Settings
from src.models.external_auth import ExternalAssertionRequest, ExternalInferenceKeyRequest
from src.startup_settings import startup_setting
from src.ui.config import UIMountSettings


def test_environment_mount_and_yaml_override_use_the_same_startup_owner(monkeypatch):
    monkeypatch.setenv(
        "DELTALLM_UI_MOUNT", json.dumps({"mount_path": "/gateway", "external_console": True})
    )
    monkeypatch.setenv("DELTALLM_EXTERNAL_AUTH", json.dumps({"child_lifetime_seconds": 180}))
    environment = Settings()
    implicit = GeneralSettings()
    assert (
        startup_setting(implicit, environment, "ui_mount", UIMountSettings()).mount_path
        == "/gateway"
    )
    assert (
        startup_setting(
            implicit, environment, "external_auth", ExternalAuthSettings()
        ).child_lifetime_seconds
        == 180
    )
    explicit = GeneralSettings(ui_mount={}, external_auth={})
    assert startup_setting(explicit, environment, "ui_mount", UIMountSettings()).mount_path == ""
    assert (
        startup_setting(
            explicit, environment, "external_auth", ExternalAuthSettings()
        ).child_lifetime_seconds
        == 300
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://console.example.com/",
        "http://console.example.com",
        "https://user@console.example.com",
        "https://console.example.com?query=1",
    ],
)
def test_browser_origins_must_be_exact_https_origins(value):
    with pytest.raises(ValidationError):
        ExternalAuthSettings(allowed_origins=(value,))


@pytest.mark.parametrize("value", ["0.0.0.0/0", "::/0", "192.0.2.1/24", "*", "bad"])
def test_proxy_trust_cannot_be_global_or_ambiguous(value):
    with pytest.raises(ValidationError):
        ExternalAuthSettings(trusted_proxy_cidrs=(value,))


def test_sensitive_request_values_are_hidden_in_representations_and_errors():
    for model, field in [
        (ExternalAssertionRequest, "assertion"),
        (ExternalInferenceKeyRequest, "api_key"),
    ]:
        secret = "sensitive-test-value"
        assert secret not in repr(model.model_validate({field: secret}))
        with pytest.raises(ValidationError) as invalid:
            model.model_validate({field: secret, "forbidden": secret})
        assert secret not in str(invalid.value)
