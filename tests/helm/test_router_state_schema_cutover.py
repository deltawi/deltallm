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
SECRET_ARGS = (
    "--set",
    "secret.values.masterKey=sk-testmasterkey1234567890A1",
    "--set",
    "secret.values.saltKey=test-salt-key-1234567890",
)


def _template(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if HELM is None:
        pytest.skip("helm is not installed")
    return subprocess.run(
        [
            HELM,
            "template",
            "deltallm",
            str(HELM_CHART_DIR),
            *SECRET_ARGS,
            *args,
        ],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def test_router_state_schema_cutover_values_are_strictly_typed() -> None:
    schema = yaml.safe_load((HELM_CHART_DIR / "values.schema.json").read_text())

    assert schema["properties"]["strategy"]["properties"]["type"] == {
        "type": "string",
        "enum": ["RollingUpdate", "Recreate"],
    }
    assert schema["properties"]["routerStateSchemaCutover"] == {
        "type": "object",
        "additionalProperties": False,
        "required": ["enabled", "acknowledged"],
        "properties": {
            "enabled": {"type": "boolean"},
            "acknowledged": {"type": "boolean"},
        },
    }


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
def test_fresh_install_keeps_normal_rolling_strategy() -> None:
    result = _template("--show-only", "templates/deployment.yaml")
    deployment = yaml.safe_load(result.stdout)

    assert deployment["spec"]["strategy"] == {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
    }


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
def test_configmap_marks_the_installed_router_state_schema() -> None:
    result = _template("--show-only", "templates/configmap.yaml")
    configmaps = [
        document
        for document in yaml.safe_load_all(result.stdout)
        if isinstance(document, dict) and document.get("kind") == "ConfigMap"
    ]

    assert configmaps[0]["metadata"]["annotations"]["deltallm.ai/router-state-schema"] == "v1"


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
def test_upgrade_requires_explicit_router_state_cutover_acknowledgement() -> None:
    result = _template("--is-upgrade", check=False)

    assert result.returncode != 0
    assert "routerStateSchemaCutover.acknowledged=true" in result.stderr


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
def test_acknowledged_cutover_rejects_rolling_update() -> None:
    result = _template(
        "--is-upgrade",
        "--set",
        "routerStateSchemaCutover.acknowledged=true",
        check=False,
    )

    assert result.returncode != 0
    assert "requires strategy.type=Recreate" in result.stderr


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
def test_acknowledged_cutover_recreates_api_and_batch_worker() -> None:
    result = _template(
        "--is-upgrade",
        "--set",
        "routerStateSchemaCutover.acknowledged=true",
        "--set",
        "strategy.type=Recreate",
        "--set",
        "batchWorker.enabled=true",
    )
    documents: list[dict[str, Any]] = [
        document
        for document in yaml.safe_load_all(result.stdout)
        if isinstance(document, dict) and document.get("kind") == "Deployment"
    ]

    assert len(documents) == 2
    assert {document["spec"]["strategy"]["type"] for document in documents} == {"Recreate"}
    assert all("rollingUpdate" not in document["spec"]["strategy"] for document in documents)
