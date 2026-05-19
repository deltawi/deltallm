from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
HELM = shutil.which("helm")


def _render(*args: str) -> list[dict[str, Any]]:
    if HELM is None:
        pytest.skip("helm is not installed")
    command = [
        HELM,
        "template",
        "deltallm",
        "./helm",
        "--set",
        "secret.values.masterKey=sk-testmasterkey1234567890A1",
        "--set",
        "secret.values.saltKey=test-salt-key-1234567890",
        *args,
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def _render_error(*args: str) -> str:
    if HELM is None:
        pytest.skip("helm is not installed")
    command = [
        HELM,
        "template",
        "deltallm",
        "./helm",
        "--set",
        "secret.values.masterKey=sk-testmasterkey1234567890A1",
        "--set",
        "secret.values.saltKey=test-salt-key-1234567890",
        *args,
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    assert result.returncode != 0
    return result.stderr


def _by_kind_and_name(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(f"missing {kind}/{name}")


def _config_yaml(config_map: dict[str, Any]) -> dict[str, Any]:
    return yaml.safe_load(config_map["data"]["config.yaml"])


def _deployment_checksum(deployment: dict[str, Any]) -> str:
    return deployment["spec"]["template"]["metadata"]["annotations"]["checksum/config"]


def _selector_matches(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return all(labels.get(key) == value for key, value in selector.items())


def _deployment_by_pod_component(docs: list[dict[str, Any]], component: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        labels = doc["spec"]["template"]["metadata"]["labels"]
        if labels.get("app.kubernetes.io/component") == component:
            return doc
    raise AssertionError(f"missing Deployment with pod component {component}")


def _service_by_component(docs: list[dict[str, Any]], component: str | None) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") != "Service":
            continue
        labels = doc.get("metadata", {}).get("labels", {})
        if labels.get("app.kubernetes.io/component") == component:
            return doc
    raise AssertionError(f"missing Service with component {component}")


def test_helm_schema_allows_active_batch_scheduler_flag() -> None:
    schema = yaml.safe_load((REPO_ROOT / "helm" / "values.schema.json").read_text())
    scheduler_enabled = schema["properties"]["config"]["properties"]["general_settings"]["properties"][
        "embeddings_batch_scheduler_enabled"
    ]

    assert scheduler_enabled == {"type": "boolean"}


def test_helm_schema_allows_tenant_fair_share_settings() -> None:
    schema = yaml.safe_load((REPO_ROOT / "helm" / "values.schema.json").read_text())
    general_settings = schema["properties"]["config"]["properties"]["general_settings"]["properties"]

    assert general_settings["embeddings_batch_tenant_fair_share_enabled"] == {"type": "boolean"}
    assert general_settings["embeddings_batch_scheduler_base_quantum_work_units"]["minimum"] == 1
    assert general_settings["embeddings_batch_scheduler_max_active_flows_per_decision"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 1000,
    }
    assert general_settings["embeddings_batch_scheduler_max_candidate_jobs_per_flow"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 1000,
    }
    assert general_settings["embeddings_batch_tenant_scope_preference"] == {"type": "string"}
    assert general_settings["embeddings_batch_tenant_fair_share_disabled_model_groups"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert general_settings["embeddings_batch_scheduler_mode"]["enum"] == [
        "fifo_v1",
        "slice_v1",
        "model_capacity_v1",
        "fair_share_v1",
        "smart_v1",
    ]
    assert general_settings["embeddings_batch_scheduler_shadow_mode"]["enum"] == [
        "none",
        "fifo_v1",
        "slice_v1",
        "model_capacity_v1",
        "fair_share_v1",
        "smart_v1",
    ]
    assert general_settings["embeddings_batch_stale_lease_sweeper_enabled"] == {"type": "boolean"}
    assert general_settings["embeddings_batch_stale_lease_sweeper_page_size"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 1000,
    }
    assert general_settings["embeddings_batch_stale_lease_sweeper_max_rows_per_run"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 5000,
    }
    assert general_settings["embeddings_batch_size_aware_scheduling_enabled"] == {"type": "boolean"}
    assert general_settings["embeddings_batch_aging_seconds_per_work_unit"]["minimum"] == 1
    assert general_settings["embeddings_batch_max_age_credit_work_units"]["minimum"] == 0
    assert general_settings["embeddings_batch_min_large_job_claim_interval_seconds"]["minimum"] == 0
    assert general_settings["embeddings_batch_small_job_fast_lane_enabled"] == {"type": "boolean"}
    assert general_settings["embeddings_batch_small_job_max_work_units"]["minimum"] == 1


def test_helm_rejects_scheduler_without_work_slice_claiming() -> None:
    error = _render_error(
        "--set",
        "config.general_settings.embeddings_batch_scheduler_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    assert "embeddings_batch_scheduler_claim_mode=work_slice" in error


def test_helm_allows_scheduler_without_strict_model_homogeneity() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_scheduler_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_claim_mode=work_slice",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_scheduler_enabled"] is True
    assert api_general["embeddings_batch_scheduler_strict_model_homogeneity_enabled"] is False


def test_helm_rejects_fair_share_without_model_capacity() -> None:
    error = _render_error(
        "--set",
        "config.general_settings.embeddings_batch_tenant_fair_share_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_claim_mode=work_slice",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_strict_model_homogeneity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    assert "tenant fair-share scheduling requires embeddings_batch_model_capacity_enabled=true" in error


def test_helm_rejects_size_aware_without_fair_share_or_shadow() -> None:
    error = _render_error(
        "--set",
        "config.general_settings.embeddings_batch_size_aware_scheduling_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_model_capacity_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_claim_mode=work_slice",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_strict_model_homogeneity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    assert (
        "size-aware batch scheduling requires embeddings_batch_tenant_fair_share_enabled=true "
        "or embeddings_batch_scheduler_shadow_enabled=true"
    ) in error


def test_helm_rejects_model_capacity_without_work_slice_claiming() -> None:
    error = _render_error(
        "--set",
        "config.general_settings.embeddings_batch_model_capacity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    assert "embeddings_batch_scheduler_claim_mode=work_slice" in error


def test_helm_allows_model_capacity_without_strict_model_homogeneity() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_model_capacity_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_claim_mode=work_slice",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_model_capacity_enabled"] is True
    assert api_general["embeddings_batch_scheduler_strict_model_homogeneity_enabled"] is False


def test_helm_allows_model_capacity_with_scheduler_prerequisites() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_model_capacity_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_claim_mode=work_slice",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_strict_model_homogeneity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_model_capacity_enabled"] is True


def test_helm_allows_explicit_smart_scheduler_mode_without_legacy_flags() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_scheduler_mode=smart_v1",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_shadow_mode=none",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_strict_model_homogeneity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_scheduler_mode"] == "smart_v1"
    assert api_general["embeddings_batch_scheduler_shadow_mode"] == "none"


def test_helm_treats_shadow_fair_share_as_effective_fair_share() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_scheduler_mode=model_capacity_v1",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_shadow_mode=fair_share_v1",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_strict_model_homogeneity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_scheduler_mode"] == "model_capacity_v1"
    assert api_general["embeddings_batch_scheduler_shadow_mode"] == "fair_share_v1"


def test_helm_explicit_fifo_mode_rolls_back_legacy_scheduler_flags() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_scheduler_mode=fifo_v1",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_shadow_mode=none",
        "--set",
        "config.general_settings.embeddings_batch_model_capacity_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_tenant_fair_share_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_size_aware_scheduling_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_scheduler_mode"] == "fifo_v1"


def test_helm_allows_fair_share_with_scheduler_prerequisites() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_tenant_fair_share_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_model_capacity_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_claim_mode=work_slice",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_strict_model_homogeneity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_tenant_fair_share_enabled"] is True
    assert api_general["embeddings_batch_model_capacity_enabled"] is True


def test_helm_allows_size_aware_with_fair_share_prerequisites() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_size_aware_scheduling_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_tenant_fair_share_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_model_capacity_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_claim_mode=work_slice",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_strict_model_homogeneity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_size_aware_scheduling_enabled"] is True


def test_helm_allows_size_aware_shadow_with_scheduler_prerequisites() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_size_aware_scheduling_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_shadow_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_model_capacity_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_claim_mode=work_slice",
        "--set",
        "config.general_settings.embeddings_batch_scheduler_strict_model_homogeneity_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_general = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))[
        "general_settings"
    ]
    assert api_general["embeddings_batch_size_aware_scheduling_enabled"] is True
    assert api_general["embeddings_batch_scheduler_shadow_enabled"] is True


def test_default_service_selector_remains_upgrade_safe() -> None:
    service = _by_kind_and_name(_render("--show-only", "templates/service.yaml"), "Service", "deltallm")

    assert service["spec"]["selector"] == {
        "app.kubernetes.io/name": "deltallm",
        "app.kubernetes.io/instance": "deltallm",
    }


def test_default_does_not_render_worker_metrics_service() -> None:
    docs = _render("--set", "prometheus.serviceMonitor.enabled=true")

    assert not any(doc.get("metadata", {}).get("name") == "deltallm-batch-worker" for doc in docs)


def test_split_mode_separates_api_and_worker_configs() -> None:
    docs = _render("--set", "batchWorker.enabled=true", "--show-only", "templates/configmap.yaml")

    api_config = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))
    worker_config = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-batch-worker-config"))

    api_general = api_config["general_settings"]
    assert api_general["embeddings_batch_worker_enabled"] is False
    assert api_general["embeddings_batch_completion_outbox_worker_enabled"] is False
    assert api_general["embeddings_batch_gc_enabled"] is False
    assert api_general["embeddings_batch_create_session_cleanup_enabled"] is False
    assert api_general["embeddings_batch_scheduler_backfill_enabled"] is False
    assert api_general["embeddings_batch_stale_lease_sweeper_enabled"] is False
    assert api_general["embeddings_batch_scheduler_claim_mode"] == "job_fifo"

    worker_general = worker_config["general_settings"]
    assert worker_general["embeddings_batch_worker_enabled"] is True
    assert worker_general["embeddings_batch_completion_outbox_worker_enabled"] is True
    assert worker_general["embeddings_batch_gc_enabled"] is True
    assert worker_general["embeddings_batch_create_session_cleanup_enabled"] is True
    assert worker_general["embeddings_batch_scheduler_backfill_enabled"] is True
    assert worker_general["embeddings_batch_stale_lease_sweeper_enabled"] is True
    assert worker_general["embeddings_batch_scheduler_claim_mode"] == "job_fifo"
    assert worker_general["embeddings_batch_worker_concurrency"] == 2
    assert worker_general["embeddings_batch_item_claim_limit"] == 10


def test_split_mode_long_name_override_keeps_worker_out_of_api_service() -> None:
    docs = _render(
        "--set",
        f"nameOverride={'a' * 63}",
        "--set",
        "batchWorker.enabled=true",
        "--show-only",
        "templates/deployment.yaml",
        "--show-only",
        "templates/batch-worker-deployment.yaml",
        "--show-only",
        "templates/service.yaml",
        "--show-only",
        "templates/batch-worker-service.yaml",
    )

    api_deployment = _deployment_by_pod_component(docs, "api")
    worker_deployment = _deployment_by_pod_component(docs, "batch-worker")
    api_service = _service_by_component(docs, None)
    worker_service = _service_by_component(docs, "batch-worker")
    worker_labels = worker_deployment["spec"]["template"]["metadata"]["labels"]

    assert api_deployment["spec"]["selector"]["matchLabels"] != worker_deployment["spec"]["selector"]["matchLabels"]
    assert not _selector_matches(api_service["spec"]["selector"], worker_labels)
    assert _selector_matches(worker_service["spec"]["selector"], worker_labels)


def test_split_mode_long_fullname_override_keeps_worker_resource_names_distinct() -> None:
    docs = _render(
        "--set",
        f"fullnameOverride={'a' * 63}",
        "--set",
        "batchWorker.enabled=true",
        "--show-only",
        "templates/configmap.yaml",
        "--show-only",
        "templates/deployment.yaml",
        "--show-only",
        "templates/batch-worker-deployment.yaml",
        "--show-only",
        "templates/service.yaml",
        "--show-only",
        "templates/batch-worker-service.yaml",
    )

    api_deployment = _deployment_by_pod_component(docs, "api")
    worker_deployment = _deployment_by_pod_component(docs, "batch-worker")
    api_service = _service_by_component(docs, None)
    worker_service = _service_by_component(docs, "batch-worker")
    api_config = _by_kind_and_name(docs, "ConfigMap", f"{api_deployment['metadata']['name']}-config")
    worker_config = _by_kind_and_name(docs, "ConfigMap", f"{worker_deployment['metadata']['name']}-config")

    assert api_deployment["metadata"]["name"] != worker_deployment["metadata"]["name"]
    assert api_service["metadata"]["name"] != worker_service["metadata"]["name"]
    assert api_config["metadata"]["name"] != worker_config["metadata"]["name"]
    assert len(worker_deployment["metadata"]["name"]) <= 63
    assert worker_deployment["metadata"]["name"].endswith("-batch-worker")


def test_shared_mode_allows_enabled_batching_with_local_storage() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_config = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))
    assert api_config["general_settings"]["embeddings_batch_enabled"] is True
    assert api_config["general_settings"]["embeddings_batch_storage_backend"] == "local"


def test_shared_mode_rejects_s3_storage_without_bucket() -> None:
    error = _render_error(
        "--set",
        "config.general_settings.embeddings_batch_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_storage_backend=s3",
        "--show-only",
        "templates/configmap.yaml",
    )

    assert "embeddings_batch_s3_bucket is required" in error


def test_shared_mode_allows_enabled_batching_with_s3_storage() -> None:
    docs = _render(
        "--set",
        "config.general_settings.embeddings_batch_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_storage_backend=s3",
        "--set",
        "config.general_settings.embeddings_batch_s3_bucket=deltallm-batch-artifacts",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_config = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))
    assert api_config["general_settings"]["embeddings_batch_enabled"] is True
    assert api_config["general_settings"]["embeddings_batch_storage_backend"] == "s3"
    assert api_config["general_settings"]["embeddings_batch_s3_bucket"] == "deltallm-batch-artifacts"


def test_split_mode_rejects_enabled_batching_with_local_storage() -> None:
    error = _render_error(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    assert "requires shared batch artifact storage" in error


def test_split_mode_allows_enabled_batching_with_s3_storage() -> None:
    docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_storage_backend=s3",
        "--set",
        "config.general_settings.embeddings_batch_s3_bucket=deltallm-batch-artifacts",
        "--show-only",
        "templates/configmap.yaml",
    )

    worker_config = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-batch-worker-config"))
    assert worker_config["general_settings"]["embeddings_batch_enabled"] is True
    assert worker_config["general_settings"]["embeddings_batch_storage_backend"] == "s3"


def test_split_mode_rejects_s3_storage_without_bucket() -> None:
    error = _render_error(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_storage_backend=s3",
        "--show-only",
        "templates/configmap.yaml",
    )

    assert "embeddings_batch_s3_bucket is required" in error


def test_split_mode_rejects_role_override_that_clears_s3_bucket() -> None:
    error = _render_error(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_storage_backend=s3",
        "--set",
        "config.general_settings.embeddings_batch_s3_bucket=deltallm-batch-artifacts",
        "--set-string",
        "batchWorker.config.general_settings.embeddings_batch_s3_bucket=",
        "--show-only",
        "templates/configmap.yaml",
    )

    assert "embeddings_batch_s3_bucket is required" in error


def test_split_mode_can_explicitly_allow_unsafe_local_storage() -> None:
    docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "batchWorker.allowUnsafeLocalStorage=true",
        "--set",
        "config.general_settings.embeddings_batch_enabled=true",
        "--show-only",
        "templates/configmap.yaml",
    )

    worker_config = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-batch-worker-config"))
    assert worker_config["general_settings"]["embeddings_batch_storage_backend"] == "local"


def test_split_mode_worker_tuning_uses_shared_config_unless_role_overridden() -> None:
    shared_docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_worker_concurrency=8",
        "--set",
        "config.general_settings.embeddings_batch_item_claim_limit=40",
        "--show-only",
        "templates/configmap.yaml",
    )
    role_override_docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "config.general_settings.embeddings_batch_worker_concurrency=8",
        "--set",
        "batchWorker.config.general_settings.embeddings_batch_worker_concurrency=3",
        "--set",
        "batchWorker.config.general_settings.embeddings_batch_scheduler_backfill_enabled=false",
        "--set",
        "batchWorker.config.general_settings.embeddings_batch_stale_lease_sweeper_enabled=false",
        "--show-only",
        "templates/configmap.yaml",
    )

    shared_worker_general = _config_yaml(
        _by_kind_and_name(shared_docs, "ConfigMap", "deltallm-batch-worker-config")
    )["general_settings"]
    role_worker_general = _config_yaml(
        _by_kind_and_name(role_override_docs, "ConfigMap", "deltallm-batch-worker-config")
    )["general_settings"]

    assert shared_worker_general["embeddings_batch_worker_concurrency"] == 8
    assert shared_worker_general["embeddings_batch_item_claim_limit"] == 40
    assert role_worker_general["embeddings_batch_worker_concurrency"] == 3
    assert role_worker_general["embeddings_batch_scheduler_backfill_enabled"] is False
    assert role_worker_general["embeddings_batch_stale_lease_sweeper_enabled"] is False


def test_production_default_does_not_disable_batch_workers_without_worker_deployment() -> None:
    docs = _render(
        "-f",
        "helm/values-production.yaml",
        "--set",
        "secret.existingSecret=deltallm-app-secrets",
        "--show-only",
        "templates/configmap.yaml",
    )

    api_config = _config_yaml(_by_kind_and_name(docs, "ConfigMap", "deltallm-config"))
    assert api_config["general_settings"]["embeddings_batch_worker_enabled"] is True
    assert api_config["general_settings"]["embeddings_batch_completion_outbox_worker_enabled"] is True
    assert api_config["general_settings"]["embeddings_batch_gc_enabled"] is True

    assert not any(doc.get("metadata", {}).get("name") == "deltallm-batch-worker-config" for doc in docs)


def test_split_mode_renders_worker_network_policy() -> None:
    docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "networkPolicy.enabled=true",
        "--show-only",
        "templates/networkpolicy.yaml",
        "--show-only",
        "templates/batch-worker-networkpolicy.yaml",
    )

    worker_policy = _by_kind_and_name(docs, "NetworkPolicy", "deltallm-batch-worker")
    assert worker_policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "deltallm-batch-worker",
        "app.kubernetes.io/instance": "deltallm",
        "app.kubernetes.io/component": "batch-worker",
    }


def test_split_mode_renders_worker_metrics_service_monitor() -> None:
    docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "prometheus.serviceMonitor.enabled=true",
        "--set",
        "prometheus.serviceMonitor.interval=15s",
        "--set",
        "prometheus.serviceMonitor.scrapeTimeout=5s",
        "--show-only",
        "templates/batch-worker-service.yaml",
        "--show-only",
        "templates/batch-worker-servicemonitor.yaml",
    )

    selector_labels = {
        "app.kubernetes.io/name": "deltallm-batch-worker",
        "app.kubernetes.io/instance": "deltallm",
        "app.kubernetes.io/component": "batch-worker",
    }
    service = _by_kind_and_name(docs, "Service", "deltallm-batch-worker")
    service_monitor = _by_kind_and_name(docs, "ServiceMonitor", "deltallm-batch-worker")

    assert service["spec"]["selector"] == selector_labels
    assert service["spec"]["ports"][0]["targetPort"] == "http"

    service_labels = service["metadata"]["labels"]
    assert service_monitor["spec"]["selector"]["matchLabels"] == selector_labels
    assert "namespaceSelector" not in service_monitor["spec"]
    assert all(service_labels[key] == value for key, value in selector_labels.items())
    assert service_monitor["spec"]["endpoints"] == [
        {
            "port": "http",
            "path": "/metrics",
            "interval": "15s",
            "scrapeTimeout": "5s",
        }
    ]


def test_migration_job_default_uses_prisma_migrate_without_db_push_fallback() -> None:
    docs = _render(
        "--set",
        "migrationJob.enabled=true",
        "--show-only",
        "templates/migration-job.yaml",
    )

    job = _by_kind_and_name(docs, "Job", "deltallm-migrate")
    migrate_args = "\n".join(job["spec"]["template"]["spec"]["containers"][0]["args"])

    assert "prisma migrate deploy --schema=./prisma/schema.prisma" in migrate_args
    assert "prisma db push" not in migrate_args
    assert "--accept-data-loss" not in migrate_args


def test_split_mode_worker_service_monitor_selects_release_namespace_when_centralized() -> None:
    docs = _render(
        "--namespace",
        "deltallm",
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "prometheus.serviceMonitor.enabled=true",
        "--set",
        "prometheus.serviceMonitor.namespace=monitoring",
        "--show-only",
        "templates/batch-worker-servicemonitor.yaml",
    )

    service_monitor = _by_kind_and_name(docs, "ServiceMonitor", "deltallm-batch-worker")
    assert service_monitor["metadata"]["namespace"] == "monitoring"
    assert service_monitor["spec"]["namespaceSelector"] == {"matchNames": ["deltallm"]}


def test_role_specific_config_checksums_are_isolated() -> None:
    base_docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--show-only",
        "templates/deployment.yaml",
        "--show-only",
        "templates/batch-worker-deployment.yaml",
    )
    worker_override_docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "batchWorker.config.general_settings.embeddings_batch_worker_concurrency=9",
        "--show-only",
        "templates/deployment.yaml",
        "--show-only",
        "templates/batch-worker-deployment.yaml",
    )
    api_override_docs = _render(
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "api.config.general_settings.upstream_http_max_connections=777",
        "--show-only",
        "templates/deployment.yaml",
        "--show-only",
        "templates/batch-worker-deployment.yaml",
    )

    base_api = _deployment_checksum(_by_kind_and_name(base_docs, "Deployment", "deltallm"))
    base_worker = _deployment_checksum(_by_kind_and_name(base_docs, "Deployment", "deltallm-batch-worker"))

    assert _deployment_checksum(_by_kind_and_name(worker_override_docs, "Deployment", "deltallm")) == base_api
    assert (
        _deployment_checksum(_by_kind_and_name(worker_override_docs, "Deployment", "deltallm-batch-worker"))
        != base_worker
    )

    assert _deployment_checksum(_by_kind_and_name(api_override_docs, "Deployment", "deltallm")) != base_api
    assert (
        _deployment_checksum(_by_kind_and_name(api_override_docs, "Deployment", "deltallm-batch-worker"))
        == base_worker
    )
