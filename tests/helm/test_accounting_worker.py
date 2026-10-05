from __future__ import annotations

import json

import pytest
import yaml

from src.capacity_runtime_policy import CapacityRuntimePolicy
from src.config import AppConfig
from src.deployment_capacity_report import CapacityReport
from tests.helm.test_batch_worker_split import (
    HELM_CHART_DIR,
    _by_kind_and_name,
    _config_yaml,
    _deployment_by_pod_component,
    _render,
    _render_error,
)

pytestmark = pytest.mark.helm


def _accounting_render(*args: str):
    return _render(
        "-f",
        str(HELM_CHART_DIR / "values-production.yaml"),
        "--set",
        "config.general_settings.accounting_protocol_enabled=true",
        "--set",
        "config.general_settings.spend_operation_intents_enabled=false",
        "--set",
        "accountingWorker.enabled=true",
        "--set",
        "dependencyCapacity.extended.enabled=true",
        *args,
    )


def test_accounting_worker_has_isolated_runtime_and_capacity_role():
    documents = _accounting_render()
    api = _deployment_by_pod_component(documents, "api")
    worker = _deployment_by_pod_component(documents, "accounting-worker")
    api_config = _config_yaml(_by_kind_and_name(documents, "ConfigMap", "deltallm-config"))
    worker_config = _config_yaml(
        _by_kind_and_name(documents, "ConfigMap", "deltallm-accounting-worker-config")
    )
    api_general = api_config["general_settings"]
    worker_general = worker_config["general_settings"]
    api_general["batch_webhook_encryption_key"] = None
    worker_general["batch_webhook_encryption_key"] = None
    validated_api = AppConfig.model_validate(api_config)
    validated_worker = AppConfig.model_validate(worker_config)

    assert api_general["accounting_protocol_enabled"] is True
    assert api_general["accounting_projection_worker_enabled"] is False
    assert api_general["spend_ingestion_worker_enabled"] is False
    assert api_general["audit_ingestion_worker_enabled"] is False
    assert worker_general["accounting_protocol_enabled"] is True
    assert worker_general["accounting_projection_worker_enabled"] is True
    assert worker_general["spend_ingestion_worker_enabled"] is True
    assert worker_general["audit_ingestion_worker_enabled"] is True
    assert worker_general["spend_ingestion_mode"] == "outbox"
    assert worker_general["audit_ingestion_mode"] == "outbox"
    assert validated_worker.general_settings.deployment_capacity_role == "accountingWorker"
    CapacityRuntimePolicy.from_general(validated_api.general_settings).validate_production(
        role="api", accounting_worker_present=True
    )
    CapacityRuntimePolicy.from_general(validated_worker.general_settings).validate_production(
        role="accountingWorker", accounting_worker_present=True
    )
    assert api["spec"]["selector"] != worker["spec"]["selector"]

    capacity = _by_kind_and_name(documents, "ConfigMap", "deltallm-dependency-capacity")["data"]
    report = CapacityReport.model_validate_json(capacity["report.json"])
    assert "accountingWorker" in report.roles
    assert report.roles["accountingWorker"].processes_per_pod == 1
    assert report.roles["api"].pools.postgresql == 33
    assert report.roles["accountingWorker"].pools.postgresql == 15
    assert report.roles["api"].file_descriptors.engine_processes == 3
    assert report.roles["accountingWorker"].file_descriptors.engine_processes == 4
    assert worker["spec"]["template"]["spec"]["containers"][0]["resources"] == {
        "requests": {"cpu": "250m", "memory": "1Gi"},
        "limits": {"cpu": "1000m", "memory": "2Gi"},
    }
    annotations = worker["spec"]["template"]["metadata"]["annotations"]
    assert annotations["prometheus.io/scrape"] == "true"
    assert annotations["prometheus.io/port"] == "4000"
    assert annotations["prometheus.io/path"] == "/metrics"


def test_accounting_evaluation_overlay_matches_bundled_postgres_capacity():
    documents = _render(
        "-f",
        str(HELM_CHART_DIR / "values-eval.yaml"),
        "-f",
        str(HELM_CHART_DIR / "values-accounting-eval.yaml"),
    )
    report = json.loads(
        _by_kind_and_name(documents, "ConfigMap", "deltallm-dependency-capacity")["data"][
            "report.json"
        ]
    )
    postgres = next(
        document
        for document in documents
        if document.get("kind") == "ConfigMap" and "override.conf" in document.get("data", {})
    )

    assert report["postgresqlConnections"] == 160
    assert report["postgresqlMaximum"] == 160
    assert postgres["data"]["override.conf"].strip() == "max_connections = 160"


def test_accounting_worker_generation_must_match_api_generation():
    error = _render_error(
        "--set",
        "config.general_settings.accounting_protocol_enabled=true",
        "--set",
        "config.general_settings.accounting_protocol_generation=7",
        "--set",
        "accountingWorker.enabled=true",
        "--set",
        "accountingWorker.config.general_settings.accounting_protocol_generation=8",
    )
    assert "same accounting_protocol_generation" in error


def test_accounting_protocol_requires_a_projection_owner():
    error = _render_error(
        "--set",
        "config.general_settings.accounting_protocol_enabled=true",
    )
    assert "requires an accounting projection worker" in error


@pytest.mark.parametrize("role", ["config", "api.config", "accountingWorker.config"])
@pytest.mark.parametrize(
    "setting,adapter", [("realtime.enabled", "Realtime"), ("embeddings_batch_enabled", "batch")]
)
def test_v2_rejects_unmigrated_legacy_billing_roles(role: str, setting: str, adapter: str):
    error = _render_error(
        "--set",
        "config.general_settings.accounting_protocol_enabled=true",
        "--set",
        "accountingWorker.enabled=true",
        "--set",
        f"{role}.general_settings.{setting}=true",
    )
    assert f"Accounting v2 requires a shared {adapter} billing adapter" in error


@pytest.mark.parametrize("setting", ["realtime.enabled", "embeddings_batch_enabled"])
def test_disabled_accounting_role_does_not_change_legacy_feature_contracts(setting: str):
    documents = _render(
        "--set",
        f"config.general_settings.{setting}=true",
        "--set",
        "config.general_settings.spend_ingestion_mode=outbox",
    )
    config = _config_yaml(_by_kind_and_name(documents, "ConfigMap", "deltallm-config"))
    assert config["general_settings"]["accounting_protocol_enabled"] is False
    capacity = _by_kind_and_name(documents, "ConfigMap", "deltallm-dependency-capacity")["data"]
    assert "accountingWorker" not in json.loads(capacity["report.json"])["roles"]


@pytest.mark.parametrize(
    "setting",
    (
        "accountingWorker.command[0]=unsafe",
        "accountingWorker.args[0]=--unsafe",
        "dependencyCapacity.accountingWorkerProcessesPerPod=2",
    ),
)
def test_accounting_worker_cannot_bypass_managed_lifecycle(setting: str):
    error = _render_error(
        "-f",
        str(HELM_CHART_DIR / "values-production.yaml"),
        "--set",
        "config.general_settings.accounting_protocol_enabled=true",
        "--set",
        "config.general_settings.spend_operation_intents_enabled=false",
        "--set",
        "accountingWorker.enabled=true",
        "--set",
        setting,
    )
    assert "managed lifecycle" in error


def test_accounting_values_are_declared_in_helm_schema():
    schema = yaml.safe_load((HELM_CHART_DIR / "values.schema.json").read_text())
    properties = schema["properties"]
    general = properties["config"]["properties"]["general_settings"]["properties"]
    assert properties["accountingWorker"]["properties"]["enabled"] == {"type": "boolean"}
    assert set(general["deployment_capacity_role"]["enum"]) == {
        "api",
        "batchWorker",
        "accountingWorker",
    }
    for name in (
        "accounting_protocol_enabled",
        "accounting_protocol_generation",
        "accounting_microbatch_max_size",
        "accounting_microbatch_dwell_ms",
        "accounting_reservation_max_pending",
        "accounting_finalization_max_pending",
        "accounting_reservation_max_pending_bytes",
        "accounting_finalization_max_pending_bytes",
        "accounting_statement_timeout_ms",
        "accounting_reservation_ack_timeout_ms",
        "accounting_finalization_ack_timeout_ms",
        "accounting_hot_path_db_pool_size",
        "accounting_max_provider_attempts",
        "accounting_grants_enabled",
        "accounting_grant_target_operations",
        "accounting_grant_ttl_seconds",
        "accounting_projection_worker_enabled",
        "accounting_projection_batch_size",
        "accounting_projection_max_concurrent_partitions",
        "accounting_projection_lease_seconds",
        "accounting_projection_poll_interval_ms",
        "accounting_projection_maintenance_interval_ms",
    ):
        assert name in general


@pytest.mark.parametrize("queue", ["reservation", "finalization"])
def test_accounting_queue_byte_budget_renders_for_api_and_worker(queue):
    name = f"accounting_{queue}_max_pending_bytes"
    documents = _accounting_render("--set", f"config.general_settings.{name}=3145728")
    for config_name in ("deltallm-config", "deltallm-accounting-worker-config"):
        rendered = _config_yaml(_by_kind_and_name(documents, "ConfigMap", config_name))
        assert rendered["general_settings"][name] == 3145728


@pytest.mark.parametrize("queue", ["reservation", "finalization"])
@pytest.mark.parametrize("value", [1048575, 67108865])
def test_accounting_queue_byte_budget_schema_rejects_outside_limits(queue, value):
    error = _render_error(
        "--set", f"config.general_settings.accounting_{queue}_max_pending_bytes={value}"
    )
    assert f"accounting_{queue}_max_pending_bytes" in error


def test_accounting_worker_can_scale_on_projection_age():
    rendered = _accounting_render(
        "--set",
        "accountingWorker.enabled=true",
        "--set",
        "accountingWorker.autoscaling.enabled=true",
        "--set",
        "accountingWorker.autoscaling.oldestEventAge.enabled=true",
        "--set",
        "accountingWorker.autoscaling.maxReplicas=1",
        "--set",
        "prometheus.customMetrics.enabled=true",
    )
    hpa = next(
        document
        for document in rendered
        if document.get("kind") == "HorizontalPodAutoscaler"
        and "accounting-worker" in document["metadata"]["name"]
    )
    metrics = hpa["spec"]["metrics"]
    assert any(
        metric.get("pods", {}).get("metric", {}).get("name")
        == "deltallm_accounting_projection_oldest_event_age_seconds"
        for metric in metrics
    )
