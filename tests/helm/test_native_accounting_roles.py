"""Native profiles count only dependencies instantiated by each fixed role."""

import pytest
import yaml

from src.bootstrap.capacity_contract import DeploymentCapacityContract
from src.config import AppConfig, Settings, resolve_database_settings
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


def arguments(*changes):
    return (
        "-f",
        str(HELM_CHART_DIR / "values-production.yaml"),
        "-f",
        str(HELM_CHART_DIR / "values-accounting-native.yaml"),
        "--set",
        "autoscaling.enabled=false",
        "--set",
        "replicaCount=1",
        "--set",
        "dependencyCapacity.postgresqlMaxConnections=1000",
        *changes,
    )


def test_native_roles_use_one_minimal_deployment_and_real_pool_inventory(tmp_path):
    documents = _render(*arguments())
    report = CapacityReport.model_validate_json(
        _by_kind_and_name(documents, "ConfigMap", "deltallm-dependency-capacity")["data"][
            "report.json"
        ]
    )
    report_path = tmp_path / "report.json"
    report_path.write_text(report.model_dump_json(by_alias=True))
    assert report.roles["api"].pools.accounting_http == 16
    for role, component in (
        ("accountingWorker", "accounting-worker"),
        ("accountingRequest", "accounting-request"),
    ):
        deployment = _deployment_by_pod_component(documents, component)
        name = deployment["metadata"]["name"]
        raw = _config_yaml(_by_kind_and_name(documents, "ConfigMap", name + "-config"))
        assert raw["model_list"] == [] and raw["deltallm_settings"] == {}
        raw["general_settings"]["batch_webhook_encryption_key"] = None
        raw["general_settings"]["accounting_rpc_signing_secret"] = None
        raw["general_settings"]["deployment_capacity_path"] = str(report_path)
        config = AppConfig.model_validate(raw)
        contract = DeploymentCapacityContract.load(config, Settings())
        assert contract is not None
        expected_pool = 8 if role == "accountingWorker" else 2
        assert config.general_settings.db_pool_size == expected_pool
        contract.validate_minimal(config, Settings(), database=expected_pool)
        allocation = report.roles[role]
        assert allocation.pools.postgresql == expected_pool
        assert allocation.file_descriptors.engine_processes == 0
        assert allocation.pools.redis_critical == allocation.pools.redis_cache == 0
        assert allocation.pools.upstream_http == allocation.pools.control_http == 0
        assert allocation.pools.accounting_http == 0
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item for item in container["env"]}
        assert env["DELTALLM_DB_POOL_SIZE"]["value"] == str(expected_pool)
        database = resolve_database_settings(
            config,
            Settings(
                database_url="postgresql://fixture:fixture@fixture/db",
                db_pool_size=int(env["DELTALLM_DB_POOL_SIZE"]["value"]),
            ),
        )
        assert database is not None and database.pool_size == expected_pool
        assert not {"REDIS_URL", "DELTALLM_MASTER_KEY", "DELTALLM_SALT_KEY"} & env.keys()
        assert ("DELTALLM_ACCOUNTING_RPC_SIGNING_SECRET" in env) is (role == "accountingRequest")
        _by_kind_and_name(documents, "NetworkPolicy", name)
        _by_kind_and_name(documents, "PodDisruptionBudget", name)
    service = _by_kind_and_name(documents, "Service", "deltallm-accounting-request")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"]["app.kubernetes.io/component"] == "accounting-request"


def test_native_pool_override_sets_config_environment_and_capacity_together():
    documents = _render(
        *arguments(
            "--set", "accountingWorker.config.general_settings.accounting_hot_path_db_pool_size=12"
        )
    )
    deployment = _deployment_by_pod_component(documents, "accounting-worker")
    raw = _config_yaml(
        _by_kind_and_name(documents, "ConfigMap", "deltallm-accounting-worker-config")
    )
    assert raw["general_settings"]["db_pool_size"] == 12
    env = {
        item["name"]: item
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["DELTALLM_DB_POOL_SIZE"]["value"] == "12"
    report = CapacityReport.model_validate_json(
        _by_kind_and_name(documents, "ConfigMap", "deltallm-dependency-capacity")["data"][
            "report.json"
        ]
    )
    assert report.roles["accountingWorker"].pools.postgresql == 12


@pytest.mark.parametrize(
    "override",
    ["accounting_hot_path_db_pool_size=7", "accounting_projection_max_concurrent_partitions=5"],
)
def test_native_worker_rejects_lanes_without_control_connection_reserve(override):
    assert "two reserved database connections" in _render_error(
        *arguments("--set", "accountingWorker.config.general_settings." + override)
    )


def test_full_native_production_counts_every_peak_role_without_larger_limits():
    documents = _render(
        "-f",
        str(HELM_CHART_DIR / "values-production.yaml"),
        "-f",
        str(HELM_CHART_DIR / "values-accounting-native.yaml"),
        "--set",
        "prometheus.serviceMonitor.namespace=monitoring",
    )
    report = CapacityReport.model_validate_json(
        _by_kind_and_name(documents, "ConfigMap", "deltallm-dependency-capacity")["data"][
            "report.json"
        ]
    )
    assert report.roles["api"].peak_processes == 37
    assert report.roles["accountingRequest"].peak_processes == 4
    assert report.roles["accountingWorker"].peak_processes == 4
    assert report.file_descriptors.max_pods_per_node == 45
    assert report.file_descriptors.node_peak <= report.file_descriptors.node_limit == 1048576
    assert report.postgresql_connections <= report.postgresql_maximum == 2000
    for component in ("accounting-request", "accounting-worker"):
        name = "deltallm-" + component
        monitor = _by_kind_and_name(documents, "ServiceMonitor", name)
        service = _by_kind_and_name(documents, "Service", name)
        assert monitor["spec"]["namespaceSelector"]["matchNames"] == ["default"]
        assert monitor["spec"]["selector"]["matchLabels"] == service["spec"]["selector"]
        assert monitor["spec"]["endpoints"][0]["relabelings"][0]["targetLabel"] == "deltallm_role"


def test_native_role_is_selected_without_extended_capacity():
    documents = _render(
        *arguments(
            "--set",
            "managedLifecycle.production=false",
            "--set",
            "dependencyCapacity.extended.enabled=false",
        )
    )
    for name, role in (
        ("deltallm-accounting-request-config", "accountingRequest"),
        ("deltallm-accounting-worker-config", "accountingWorker"),
    ):
        general = _config_yaml(_by_kind_and_name(documents, "ConfigMap", name))["general_settings"]
        assert general["deployment_capacity_role"] == role
        assert general["accounting_execution_mode"] == "local_journal"
        assert "deployment_capacity_path" not in general


@pytest.mark.parametrize(
    "change,message",
    [
        ("accountingRequest.enabled=false", "both isolated roles"),
        ("accountingWorker.enabled=false", "both isolated roles"),
        ("runtime.accounting.existingSecret.name=", "existingSecret.name"),
        (
            "accountingRequest.config.general_settings.accounting_protocol_generation=2",
            "same native mode and generation",
        ),
        ("accountingRequest.command[0]=unsafe", "managed lifecycle"),
        ("dependencyCapacity.accountingRequestProcessesPerPod=2", "managed lifecycle"),
        ("dependencyCapacity.postgresqlMaxConnections=1", "PostgreSQL connection budget"),
        ("networkPolicy.enabled=false", "network protection"),
    ],
)
def test_incomplete_or_unsafe_native_deployment_fails_closed(change, message):
    assert message in _render_error(*arguments("--set", change))


def test_request_replicas_and_surge_have_exact_database_cost():
    def report(replicas):
        docs = _render(*arguments("--set", f"accountingRequest.replicaCount={replicas}"))
        return CapacityReport.model_validate_json(
            _by_kind_and_name(docs, "ConfigMap", "deltallm-dependency-capacity")["data"][
                "report.json"
            ]
        )

    one, three = report(1), report(3)
    # Production reserves two retiring generations and one absolute surge pod.
    assert one.roles["accountingRequest"].peak_processes == 4
    assert three.roles["accountingRequest"].peak_processes == 10
    assert three.postgresql_connections - one.postgresql_connections == 12
    assert three.redis_connections == one.redis_connections


def test_native_request_schema_is_strict():
    assert "unknown" in _render_error(*arguments("--set", "accountingRequest.unknown=1"))
    schema = yaml.safe_load((HELM_CHART_DIR / "values.schema.json").read_text())
    assert schema["properties"]["accountingRequest"]["additionalProperties"] is False


def test_minimal_roles_do_not_inherit_gateway_secret_bundles():
    documents = _render(
        *arguments(
            "--set",
            "envFrom[0].secretRef.name=gateway-provider-credentials",
            "--set",
            "accountingRequest.envFrom[0].configMapRef.name=request-role-runtime",
        )
    )
    api = _deployment_by_pod_component(documents, "api")["spec"]["template"]["spec"]["containers"][
        0
    ]
    request = _deployment_by_pod_component(documents, "accounting-request")["spec"]["template"][
        "spec"
    ]["containers"][0]
    projection = _deployment_by_pod_component(documents, "accounting-worker")["spec"]["template"][
        "spec"
    ]["containers"][0]
    assert api["envFrom"] == [{"secretRef": {"name": "gateway-provider-credentials"}}]
    assert request["envFrom"] == [{"configMapRef": {"name": "request-role-runtime"}}]
    assert "envFrom" not in projection


def test_native_worker_hpa_uses_both_terminal_and_reporting_pressure():
    documents = _render(
        *arguments(
            "--set",
            "accountingWorker.autoscaling.enabled=true",
            "--set",
            "accountingWorker.autoscaling.oldestEventAge.enabled=true",
            "--set",
            "accountingWorker.autoscaling.maxReplicas=2",
        )
    )
    hpa = _by_kind_and_name(documents, "HorizontalPodAutoscaler", "deltallm-accounting-worker")
    age = [value for value in hpa["spec"]["metrics"] if value["type"] == "Pods"]
    assert age[0]["pods"]["metric"]["name"] == "deltallm_accounting_native_oldest_work_age_seconds"
    assert "native terminal/reporting age metric" in _render_error(
        *arguments(
            "--set",
            "accountingWorker.autoscaling.enabled=true",
            "--set",
            "accountingWorker.autoscaling.oldestEventAge.enabled=true",
            "--set",
            "accountingWorker.autoscaling.oldestEventAge.metricName=legacy_age",
        )
    )
