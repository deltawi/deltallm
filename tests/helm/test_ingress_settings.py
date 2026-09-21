from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

HELM = shutil.which("helm")
CHART = Path(__file__).resolve().parents[2] / "deploy/kubernetes/helm"
pytestmark = [pytest.mark.helm, pytest.mark.skipif(HELM is None, reason="helm not installed")]


def render(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            HELM,
            "template",
            "--set",
            "image.tag=pr8-test-release",
            "deltallm",
            str(CHART),
            "--set",
            "secret.values.masterKey=sk-testmasterkey1234567890A1",
            "--set",
            "secret.values.saltKey=test-salt-key-1234567890",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("name", "invalid"),
    [
        ("max_active", "0"),
        ("max_waiters", "-1"),
        ("queue_timeout_ms", "0"),
        ("max_body_bytes", "0"),
        ("max_buffered_bytes", "0"),
        ("body_timeout_seconds", "0"),
        ("health_max_active", "0"),
        ("control_max_active", "0"),
        ("control_max_buffered_bytes", "0"),
    ],
)
def test_chart_rejects_invalid_ingress_budget(name: str, invalid: str) -> None:
    result = render("--set", f"config.general_settings.gateway_ingress_{name}={invalid}")
    assert result.returncode != 0
    assert f"gateway_ingress_{name}" in result.stderr


@pytest.mark.parametrize("overlay", [None, "values-eval.yaml", "values-production.yaml"])
def test_rendered_config_has_explicit_ingress_budgets(overlay: str | None) -> None:
    args = ["-f", str(CHART / overlay)] if overlay else []
    result = render(*args, "--set", "config.general_settings.gateway_ingress_enabled=true")
    assert result.returncode == 0, result.stderr
    configs = [
        item
        for item in yaml.safe_load_all(result.stdout)
        if item and item.get("kind") == "ConfigMap"
    ]
    config = next(
        yaml.safe_load(item["data"]["config.yaml"])
        for item in configs
        if "config.yaml" in item.get("data", {})
    )
    general = config["general_settings"]
    assert general["gateway_ingress_enabled"] is True
    assert general["gateway_ingress_max_active"] == 100
    assert general["gateway_ingress_max_waiters"] == 0
    assert general["gateway_ingress_max_buffered_bytes"] == 67108864
    assert general["gateway_ingress_control_max_active"] == 16
    assert general["auth_fallback_max_active"] == 8


@pytest.mark.parametrize(
    "field",
    [
        "max_active",
        "max_waiters",
        "queue_timeout_ms",
        "timeout_seconds",
        "cache_timeout_seconds",
        "cache_max_bytes",
    ],
)
def test_chart_rejects_invalid_auth_fallback_limits(field) -> None:
    result = render("--set", f"config.general_settings.auth_fallback_{field}=-1")
    assert result.returncode != 0
    assert "auth_fallback_" + field in result.stderr
