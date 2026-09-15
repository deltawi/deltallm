from __future__ import annotations

import pytest
import yaml

from tests.helm.test_ingress_settings import CHART, render

pytestmark = pytest.mark.helm


@pytest.mark.parametrize(
    "field,value",
    [
        ("budget_enforcement_query_mode", "unknown"),
        ("budget_enforcement_query_timeout_seconds", "0"),
        ("budget_enforcement_shadow_sample_rate", "2"),
        ("budget_alert_ttl_seconds", "1"),
    ],
)
def test_budget_setting_bounds(field, value):
    result = render("--set", f"config.general_settings.{field}={value}")
    assert result.returncode != 0
    assert field in result.stderr


def test_production_explicitly_selects_qualified_combined_budget_mode():
    overlay = yaml.safe_load((CHART / "values-production.yaml").read_text())
    assert overlay["config"]["general_settings"]["budget_enforcement_query_mode"] == "combined"
    result = render("-f", str(CHART / "values-production.yaml"))
    assert result.returncode == 0, result.stderr
    config = next(
        yaml.safe_load(item["data"]["config.yaml"])
        for item in yaml.safe_load_all(result.stdout)
        if item and "config.yaml" in item.get("data", {})
    )
    assert config["general_settings"]["budget_enforcement_query_mode"] == "combined"
