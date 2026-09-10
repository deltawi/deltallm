from __future__ import annotations

import pytest

from tests.helm.test_batch_worker_split import HELM_CHART_DIR, _render, _render_error
from tests.helm.test_tier_policy_settings import _config_maps, _general_settings

pytestmark = pytest.mark.helm


@pytest.mark.parametrize("values", ["values.yaml", "values-eval.yaml", "values-production.yaml"])
def test_provider_discovery_safe_defaults_render_in_all_profiles(values):
    documents = _render("-f", str(HELM_CHART_DIR / values))
    configs = [
        config for config in _config_maps(documents) if "config.yaml" in config.get("data", {})
    ]
    assert configs
    for config in configs:
        settings = _general_settings(config)
        assert settings["provider_discovery_allow_http"] is False
        assert settings["provider_discovery_allowed_ports"] == [443]
        assert settings["provider_discovery_allowed_private_cidrs"] == []


@pytest.mark.parametrize(
    "setting,value",
    [
        ("provider_discovery_allow_http", '"true"'),
        ("provider_discovery_allowed_ports", "[]"),
        ("provider_discovery_allowed_ports", "[443,443]"),
        ("provider_discovery_allowed_ports", "[0]"),
        ("provider_discovery_allowed_ports", "[65536]"),
        ("provider_discovery_allowed_private_cidrs", '[""]'),
    ],
)
def test_provider_discovery_invalid_values_fail_helm(setting, value):
    error = _render_error("--set-json", f"config.general_settings.{setting}={value}")
    assert setting in error
