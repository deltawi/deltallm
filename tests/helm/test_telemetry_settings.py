from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_CHART_DIR = REPO_ROOT / "deploy" / "kubernetes" / "helm"
HELM = shutil.which("helm")


def test_telemetry_values_are_declared_in_helm_schema() -> None:
    schema = yaml.safe_load((HELM_CHART_DIR / "values.schema.json").read_text())
    properties = schema["properties"]
    general = properties["config"]["properties"]["general_settings"]["properties"]

    assert properties["terminationGracePeriodSeconds"] == {
        "type": "integer",
        "minimum": 1,
    }
    for name in (
        "prompt_singleflight_max_keys",
        "prompt_singleflight_timeout_seconds",
        "email_worker_batch_size",
        "email_worker_delivery_lease_seconds",
        "email_worker_audit_lease_seconds",
        "email_worker_startup_timeout_seconds",
        "email_worker_shutdown_drain_timeout_seconds",
        "telemetry_db_pool_size",
        "telemetry_db_pool_timeout_seconds",
        "telemetry_worker_startup_timeout_seconds",
        "telemetry_shutdown_drain_timeout_seconds",
        "spend_ingestion_mode",
        "spend_ingestion_worker_enabled",
        "spend_ingestion_max_pending_events",
        "spend_ingestion_overload_policy",
        "spend_ingestion_fallback_max_concurrency",
        "spend_ingestion_fallback_max_waiters",
        "spend_ingestion_fallback_queue_timeout_ms",
        "spend_ingestion_fallback_execution_timeout_seconds",
        "audit_ingestion_mode",
        "audit_ingestion_worker_enabled",
        "audit_ingestion_max_pending_events",
        "audit_ingestion_required_reserve",
    ):
        assert name in general


def test_production_profile_uses_durable_required_telemetry() -> None:
    values = yaml.safe_load((HELM_CHART_DIR / "values-production.yaml").read_text())
    general = values["config"]["general_settings"]

    assert general["spend_ingestion_mode"] == "outbox"
    assert general["audit_ingestion_mode"] == "outbox"


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
def test_telemetry_shutdown_deadline_must_fit_pod_termination_grace() -> None:
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
            "--set",
            "terminationGracePeriodSeconds=20",
            "--set",
            "config.general_settings.telemetry_shutdown_drain_timeout_seconds=20",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert (
        "terminationGracePeriodSeconds must be greater than "
        "telemetry_shutdown_drain_timeout_seconds"
    ) in result.stderr


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
def test_email_shutdown_deadline_must_fit_pod_termination_grace() -> None:
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
            "--set",
            "terminationGracePeriodSeconds=20",
            "--set",
            "config.general_settings.telemetry_shutdown_drain_timeout_seconds=5",
            "--set",
            "config.general_settings.email_enabled=true",
            "--set",
            "config.general_settings.email_worker_enabled=true",
            "--set",
            "config.general_settings.email_worker_shutdown_drain_timeout_seconds=20",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert (
        "terminationGracePeriodSeconds must be greater than "
        "email_worker_shutdown_drain_timeout_seconds"
    ) in result.stderr


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
def test_batch_worker_shutdown_deadline_must_fit_pod_termination_grace() -> None:
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
            "--set",
            "batchWorker.enabled=true",
            "--set",
            "batchWorker.config.general_settings.telemetry_shutdown_drain_timeout_seconds=30",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert (
        "terminationGracePeriodSeconds must be greater than the batch worker "
        "telemetry_shutdown_drain_timeout_seconds"
    ) in result.stderr
