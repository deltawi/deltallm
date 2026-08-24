from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.models.organization_lifecycle import ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION


REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_CHART_DIR = REPO_ROOT / "deploy" / "kubernetes" / "helm"
HELM = shutil.which("helm")


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
    return [item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict)]


def _config(documents: list[dict[str, Any]], name: str) -> dict[str, Any]:
    config_map = next(
        item
        for item in documents
        if item.get("kind") == "ConfigMap" and item.get("metadata", {}).get("name") == name
    )
    return yaml.safe_load(config_map["data"]["config.yaml"])


def test_organization_deletion_defaults_and_schema_match_runtime() -> None:
    values = yaml.safe_load((HELM_CHART_DIR / "values.yaml").read_text())
    general = values["config"]["general_settings"]
    assert general["organization_deletion_requests_enabled"] is False
    assert general["organization_deletion_worker_enabled"] is True
    assert general["organization_deletion_recovery_window_hours"] == 168
    assert general["organization_lifecycle_auth_max_staleness_seconds"] == 3

    schema = yaml.safe_load((HELM_CHART_DIR / "values.schema.json").read_text())
    properties = schema["properties"]["config"]["properties"]["general_settings"]["properties"]
    assert properties["organization_deletion_requests_enabled"] == {"type": "boolean"}
    assert properties["organization_deletion_worker_enabled"] == {"type": "boolean"}
    assert properties["organization_deletion_worker_page_size"]["maximum"] == 1000
    assert properties["organization_lifecycle_auth_max_staleness_seconds"]["maximum"] == 60


def test_rollout_guidance_matches_lifecycle_protocol_constant() -> None:
    assert ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION == 2
    for path in (
        REPO_ROOT / "config.example.yaml",
        HELM_CHART_DIR / "values-production.yaml",
    ):
        guidance = path.read_text(encoding="utf-8")
        assert "protocol version (v2 for this rollout)" in guidance
        assert "protocol v1" not in guidance


def test_split_role_runs_deletion_worker_only_in_worker_pods() -> None:
    documents = _render(
        "--set",
        "batchWorker.enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )
    api_general = _config(documents, "deltallm-config")["general_settings"]
    worker_general = _config(documents, "deltallm-batch-worker-config")["general_settings"]

    assert api_general["organization_deletion_worker_enabled"] is False
    assert worker_general["organization_deletion_worker_enabled"] is True
    assert worker_general["organization_deletion_worker_page_size"] == 100
