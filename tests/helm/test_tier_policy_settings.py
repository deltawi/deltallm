from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_CHART_DIR = REPO_ROOT / "deploy" / "kubernetes" / "helm"
HELM = shutil.which("helm")

TIER_POLICY_DEFAULTS = {
    "tier_policy_mode": "disabled",
    "tier_policy_missing_service_mode": "fail_open",
    "tier_policy_refresh_interval_seconds": 300,
    "tier_policy_refresh_jitter_seconds": 1,
    "tier_policy_transition_grace_seconds": 0.05,
    "tier_policy_refresh_retry_delay_seconds": 5,
    "tier_capacity_fair_share_enabled": False,
    "tier_capacity_fair_share_active_ttl_seconds": 10,
}


def _render(*args: str) -> list[dict[str, Any]]:
    if HELM is None:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        [
            HELM,
            "template",
            "deltallm",
            str(HELM_CHART_DIR),
            "--set",
            "secret.values.masterKey=sk-testmasterkey1234567890A1",
            "--set",
            "secret.values.saltKey=test-salt-key-1234567890",
            *args,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if isinstance(document, dict)]


def _config_maps(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [document for document in documents if document.get("kind") == "ConfigMap"]


def _general_settings(config_map: dict[str, Any]) -> dict[str, Any]:
    rendered = yaml.safe_load(config_map["data"]["config.yaml"])
    return rendered["general_settings"]


@pytest.mark.parametrize("values_file", ["values.yaml", "values-production.yaml"])
def test_tier_policy_defaults_are_safe_and_complete(values_file: str) -> None:
    values = yaml.safe_load((HELM_CHART_DIR / values_file).read_text())
    general = values["config"]["general_settings"]

    assert {key: general[key] for key in TIER_POLICY_DEFAULTS} == TIER_POLICY_DEFAULTS


def test_tier_policy_schema_matches_runtime_constraints() -> None:
    schema = yaml.safe_load((HELM_CHART_DIR / "values.schema.json").read_text())
    general = schema["properties"]["config"]["properties"]["general_settings"]["properties"]

    assert general["tier_policy_mode"] == {
        "type": "string",
        "enum": ["disabled", "shadow", "enforce"],
    }
    assert general["tier_policy_missing_service_mode"] == {
        "type": "string",
        "enum": ["fail_open", "fail_closed"],
    }
    assert general["tier_policy_refresh_interval_seconds"] == {
        "type": "number",
        "exclusiveMinimum": 0,
    }
    assert general["tier_policy_refresh_jitter_seconds"] == {
        "type": "number",
        "minimum": 0,
    }
    assert general["tier_policy_transition_grace_seconds"] == {
        "type": "number",
        "minimum": 0,
    }
    assert general["tier_policy_refresh_retry_delay_seconds"] == {
        "type": "number",
        "exclusiveMinimum": 0,
    }
    assert general["tier_capacity_fair_share_enabled"] == {"type": "boolean"}
    assert general["tier_capacity_fair_share_active_ttl_seconds"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 300,
    }


def test_tier_policy_overrides_render_for_api_and_split_worker() -> None:
    documents = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "config.general_settings.tier_policy_mode=enforce",
        "--set",
        "config.general_settings.tier_policy_missing_service_mode=fail_closed",
        "--set",
        "config.general_settings.tier_policy_refresh_interval_seconds=42",
        "--set",
        "config.general_settings.tier_policy_refresh_jitter_seconds=0",
        "--set-json",
        "config.general_settings.tier_policy_transition_grace_seconds=0.2",
        "--set",
        "config.general_settings.tier_policy_refresh_retry_delay_seconds=3",
        "--set",
        "config.general_settings.tier_capacity_fair_share_enabled=true",
        "--set",
        "config.general_settings.tier_capacity_fair_share_active_ttl_seconds=30",
        "--show-only",
        "templates/configmap.yaml",
    )

    config_maps = _config_maps(documents)
    assert len(config_maps) == 2
    for config_map in config_maps:
        general = _general_settings(config_map)
        assert general["tier_policy_mode"] == "enforce"
        assert general["tier_policy_missing_service_mode"] == "fail_closed"
        assert general["tier_policy_refresh_interval_seconds"] == 42
        assert general["tier_policy_refresh_jitter_seconds"] == 0
        assert general["tier_policy_transition_grace_seconds"] == 0.2
        assert general["tier_policy_refresh_retry_delay_seconds"] == 3
        assert general["tier_capacity_fair_share_enabled"] is True
        assert general["tier_capacity_fair_share_active_ttl_seconds"] == 30
