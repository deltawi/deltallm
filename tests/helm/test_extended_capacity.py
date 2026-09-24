import json

import pytest
import yaml
from pydantic import SecretStr

from src.bootstrap.capacity_contract import DeploymentCapacityContract
from src.bootstrap.dependency_capacity import DependencyAllocationSnapshot
from src.capacity_runtime_policy import CapacityRuntimePolicy
from src.config import AppConfig, Settings
from src.deployment_capacity_report import CapacityReport
from src.deployment_capacity_settings import DeploymentCapacitySettings
from tests.helm.test_batch_worker_split import HELM_CHART_DIR, _render, _render_error
from tests.helm.test_dependency_capacity import capacity

pytestmark = pytest.mark.helm


def report(*args):
    return CapacityReport.model_validate_json(capacity(_render(*args))["report.json"])


@pytest.mark.parametrize("overlay", ["values.yaml", "values-eval.yaml", "values-production.yaml"])
def test_report_is_typed_and_preserves_role_and_pool_inventory(overlay):
    result = report("-f", str(HELM_CHART_DIR / overlay))
    assert result.schema_version == 1
    api = result.roles["api"]
    assert api.pools.upstream_http == 500
    assert api.pools.control_http == 100
    assert api.pools.redis_critical == 64
    assert api.pools.redis_cache == 32
    assert api.file_descriptors.python > 500 + 100 + 96
    assert result.provider_domains["local-mock"].enforcement == "workload-envelope"


@pytest.mark.parametrize(
    "setting,message",
    [
        ("fileDescriptors.processLimit=1", "Python file descriptor"),
        ("fileDescriptors.engineLimit=1", "Prisma file descriptor"),
        ("fileDescriptors.nodeLimit=1", "Node file descriptor"),
        ("fileDescriptors.maxPodsPerNode=2", "Node placement allowance"),
        ("auxiliaryHttpConnectionsPerProcess=19", "auxiliary HTTP"),
        ("providerDomains.local-mock.rpm=9099", "RPM allocation"),
        ("providerDomains.local-mock.tpm=1", "TPM allocation"),
        ("providerDomains.local-mock.concurrency=1", "concurrent transport"),
        ("providerDomains.local-mock=null", "providerDomains"),
        ("proxyPoolsPerClient=-1", "proxyPoolsPerClient"),
        ("unexpectedAllocation=10", "unexpectedAllocation"),
    ],
)
def test_each_extended_budget_rejects_unsafe_or_unknown_inputs(setting, message):
    error = _render_error(
        "--set",
        "dependencyCapacity.extended.enabled=true",
        "--set",
        "dependencyCapacity.extended." + setting,
    )
    assert message in error


def test_provider_exact_boundary_and_proxy_multiplicity():
    result = report(
        "--set",
        "dependencyCapacity.extended.enabled=true",
        "--set",
        "dependencyCapacity.extended.providerDomains.local-mock.rpm=9100",
        "--set",
        "dependencyCapacity.extended.proxyPoolsPerClient=2",
    )
    assert result.provider_domains["local-mock"].rpm == 9100
    assert result.provider_domains["local-mock"].concurrency == 5 * 1500 + 10
    assert result.roles["api"].pools.upstream_http == 1500
    assert result.roles["api"].pools.control_http == 300


def test_split_redis_validates_each_physical_domain():
    args = (
        "--set",
        "dependencyCapacity.extended.cacheRedis.separate=true",
        "--set",
        "dependencyCapacity.redisMaxClients=384",
        "--set",
        "dependencyCapacity.extended.cacheRedis.maxClients=224",
    )
    assert report(*args).redis_critical_connections == 320
    assert "cache Redis" in _render_error(
        *args, "--set", "dependencyCapacity.extended.cacheRedis.maxClients=223"
    )
    assert "Peak Redis" in _render_error(*args, "--set", "dependencyCapacity.redisMaxClients=383")


def test_migration_and_other_clients_count_in_addition_to_operating_reserve():
    result = report(
        "--set",
        "dependencyCapacity.extended.postgresqlMigrationConnections=3",
        "--set",
        "dependencyCapacity.extended.postgresqlOtherConnections=7",
        "--set",
        "dependencyCapacity.extended.redisOtherClients=9",
    )
    assert result.postgresql_connections == 160 + 3 + 7
    assert result.redis_connections == 544 + 9


@pytest.mark.parametrize("replicas", [2, 3, 4])
def test_experiment_uses_one_profile_and_varies_only_fixed_replicas(replicas):
    docs = _render(
        "-f",
        str(HELM_CHART_DIR / "values-production.yaml"),
        "-f",
        str(HELM_CHART_DIR / "values-capacity-experiment.yaml"),
        "--set",
        f"replicaCount={replicas}",
    )
    deployment = next(doc for doc in docs if doc["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == replicas
    assert deployment["spec"]["template"]["spec"]["containers"][0]["resources"] == {
        "requests": {"cpu": "1", "memory": "2Gi"},
        "limits": {"cpu": "2", "memory": "4Gi"},
    }
    result = CapacityReport.model_validate_json(capacity(docs)["report.json"])
    assert result.roles["api"].peak_processes == replicas * 3 + 1


def test_runtime_bootstrap_checks_the_actual_rendered_contract(tmp_path, monkeypatch):
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    docs = _render("-f", str(HELM_CHART_DIR / "values-production.yaml"))
    contract = tmp_path / "report.json"
    contract.write_text(capacity(docs)["report.json"])
    raw = next(doc["data"]["config.yaml"] for doc in docs if "config.yaml" in doc.get("data", {}))
    config = yaml.safe_load(raw)
    config["general_settings"]["deployment_capacity_path"] = str(contract)
    config["general_settings"]["batch_webhook_encryption_key"] = None
    app_config = AppConfig.model_validate(config)
    settings = Settings(database_url="postgresql://fixture:fixture@fixture/db")
    snapshot = DependencyAllocationSnapshot.build(app_config, settings)
    snapshot.validate_deployment(app_config, settings)
    app_config.general_settings.upstream_http_max_connections = 501
    with pytest.raises(RuntimeError, match="policy requires a restart"):
        snapshot.validate_deployment(app_config, settings)
    app_config.general_settings.upstream_http_max_connections = 500
    monkeypatch.setenv("HTTP_PROXY", "http://fixture.invalid:8080")
    with pytest.raises(RuntimeError, match="Proxy transport count"):
        snapshot.validate_deployment(app_config, settings)
    assert json.loads(contract.read_text())["extended"] is True


def test_report_reader_rejects_unbounded_unknown_or_missing_data_without_leaking_it(tmp_path):
    valid = json.loads(
        capacity(_render("-f", str(HELM_CHART_DIR / "values-production.yaml")))["report.json"]
    )
    path = tmp_path / "operator-secret-name.json"
    for broken in (
        {**valid, "unexpectedOperatorInput": "sensitive-value"},
        {**valid, "roles": {}},
        {**valid, "peakProcesses": -1},
    ):
        path.write_text(json.dumps(broken))
        with pytest.raises(RuntimeError, match="missing or invalid") as error:
            CapacityReport.read(path)
        assert "sensitive-value" not in str(error.value)
        assert "operator-secret-name" not in str(error.value)
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(RuntimeError, match="missing or invalid"):
        CapacityReport.read(path)


def test_role_override_uses_the_same_pool_schema_and_budget():
    assert "maximum" in _render_error("--set", "api.config.general_settings.db_pool_size=10001")
    assert "PostgreSQL" in _render_error("--set", "api.config.general_settings.db_pool_size=1000")


def test_redis_db_number_and_omitted_default_port_do_not_claim_a_split_server():
    allocation = report("--set", "dependencyCapacity.extended.cacheRedis.separate=true")
    config = AppConfig()
    config.general_settings.redis_bulk_url = SecretStr("redis://redis:6379/1")
    settings = Settings(redis_url="redis://redis/0")
    contract = DeploymentCapacityContract(
        DeploymentCapacitySettings(),
        allocation,
        CapacityRuntimePolicy.from_general(config.general_settings),
    )
    with pytest.raises(RuntimeError, match="endpoint mapping"):
        contract._validate_redis_domain(config, settings)
