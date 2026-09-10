from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import GeneralSettings


@pytest.mark.parametrize(
    "settings",
    [
        {"provider_discovery_allow_http": "true"},
        {"provider_discovery_allowed_ports": []},
        {"provider_discovery_allowed_ports": [443, 443]},
        {"provider_discovery_allowed_ports": [0]},
        {"provider_discovery_allowed_ports": [65536]},
        {"provider_discovery_allowed_ports": [True]},
        {"provider_discovery_allowed_ports": ["443"]},
        {"provider_discovery_allowed_ports": list(range(1, 66))},
        {"provider_discovery_allowed_private_cidrs": ["invalid"]},
        {"provider_discovery_allowed_private_cidrs": ["10.0.0.0/8"] * 129},
    ],
)
def test_invalid_provider_discovery_settings_fail_startup(settings):
    with pytest.raises(ValidationError):
        GeneralSettings.model_validate(settings)


def test_provider_and_webhook_settings_remain_independent():
    settings = GeneralSettings(
        provider_discovery_allowed_private_cidrs=["10.2.3.4/8", "fd00::1/64"]
    )
    assert settings.provider_discovery_allowed_private_cidrs == ["10.0.0.0/8", "fd00::/64"]
    assert settings.batch_webhook_allowed_private_cidrs == []
    assert settings.batch_webhook_allow_http is False
