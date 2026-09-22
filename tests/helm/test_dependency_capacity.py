import math

import pytest

from tests.helm.test_batch_worker_split import HELM_CHART_DIR, _render, _render_error

pytestmark = pytest.mark.helm


def capacity(documents):
    return next(
        doc["data"]
        for doc in documents
        if doc.get("kind") == "ConfigMap"
        and doc["metadata"]["name"].endswith("-dependency-capacity")
    )


@pytest.mark.parametrize("workers", [False, True])
def test_production_counts_all_pools_surge_retiring_pods_and_enabled_roles(workers):
    docs = _render(
        "-f",
        str(HELM_CHART_DIR / "values-production.yaml"),
        "--set",
        f"batchWorker.enabled={str(workers).lower()}",
    )
    result = capacity(docs)
    processes = 12 + 1 + 24 + ((2 + 1 + 4) if workers else 0)
    assert int(result["peak-processes"]) == processes
    assert (
        int(result["postgresql-connections-including-reserve"])
        == processes * (20 + 8 + 5 + 5) + 100
    )
    assert int(result["redis-critical-connections"]) == processes * 64
    assert int(result["redis-cache-connections"]) == processes * (16 + 16)
    assert int(result["redis-connections-including-reserve"]) == processes * 96 + 128
    for doc in docs:
        if doc.get("kind") == "Deployment":
            env = {
                entry["name"]: entry.get("value")
                for entry in doc["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            assert env["WEB_CONCURRENCY"] == env["UVICORN_WORKERS"] == "1"
            assert env["DELTALLM_DB_POOL_SIZE"] == "20"
            assert env["DELTALLM_TELEMETRY_DB_POOL_SIZE"] == "5"


def test_percentage_surge_rounds_up_and_worker_overrides_are_counted():
    docs = _render(
        "--set",
        "managedLifecycle.enabled=false",
        "--set",
        "replicaCount=3",
        "--set",
        "strategy.rollingUpdate.maxSurge=25%",
        "--set",
        "dependencyCapacity.apiProcessesPerPod=2",
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "batchWorker.replicaCount=1",
        "--set",
        "batchWorker.config.general_settings.db_pool_size=7",
    )
    api = (3 + math.ceil(3 * 0.25) + 3) * 2
    worker = 1 + math.ceil(1 * 0.25) + 1
    assert int(capacity(docs)["peak-processes"]) == api + worker
    assert (
        int(capacity(docs)["postgresql-connections-including-reserve"])
        == api * 28 + worker * 15 + 20
    )


@pytest.mark.parametrize(
    ("setting", "message"),
    [
        ("dependencyCapacity.postgresqlMaxConnections=1", "Peak PostgreSQL"),
        ("dependencyCapacity.redisMaxClients=1", "Peak Redis"),
        ("dependencyCapacity.retiringGenerations=0", "retiringGenerations"),
        ("config.general_settings.db_foreground_pool_size=0", "db_foreground_pool_size"),
        (
            "config.general_settings.telemetry_worker_db_pool_size=0",
            "telemetry_worker_db_pool_size",
        ),
        ("config.general_settings.db_lock_timeout_seconds=5", "lock <= statement"),
        ("config.general_settings.db_background_statement_timeout_seconds=30", "lock <= statement"),
    ],
)
def test_unsafe_capacity_and_deadline_profiles_fail_render(setting, message):
    assert message in _render_error("--set", setting)


@pytest.mark.parametrize(
    "name",
    [
        "WEB_CONCURRENCY",
        "UVICORN_WORKERS",
        "DELTALLM_DB_POOL_SIZE",
        "DELTALLM_TELEMETRY_DB_POOL_SIZE",
    ],
)
def test_environment_cannot_override_calculated_process_or_pool_count(name):
    assert "owned by dependencyCapacity" in _render_error(
        "--set", f"env[0].name={name}", "--set-string", "env[0].value=999"
    )


@pytest.mark.parametrize("role", ["api", "batchWorker"])
@pytest.mark.parametrize("value", ["0", "-1", "101"])
def test_role_pool_overrides_follow_runtime_bounds(role, value):
    assert "db_foreground_pool_size" in _render_error(
        "--set", f"{role}.config.general_settings.db_foreground_pool_size={value}"
    )


@pytest.mark.parametrize("flag", ["--workers", "-w", "--workers=9"])
def test_explicit_worker_arguments_cannot_bypass_process_budget(flag):
    assert "owned by dependencyCapacity" in _render_error("--set", f"args[0]={flag}")


def test_numeric_string_surge_remains_supported():
    docs = _render("--set-string", "strategy.rollingUpdate.maxSurge=2")
    assert int(capacity(docs)["peak-processes"]) == 2 * 2 + 2


def test_evaluation_pool_budget_matches_the_bundled_postgres_server():
    docs = _render("-f", str(HELM_CHART_DIR / "values-eval.yaml"))
    assert int(capacity(docs)["postgresql-connections-including-reserve"]) == 100
    assert int(capacity(docs)["peak-processes"]) == 3
    configuration = next(
        doc
        for doc in docs
        if doc.get("kind") == "ConfigMap" and "override.conf" in doc.get("data", {})
    )
    assert configuration["data"]["override.conf"].strip() == "max_connections = 100"


@pytest.mark.parametrize(
    "setting",
    [
        "config.general_settings.audit_ingestion_mode=outbox",
        "batchWorker.enabled=true",
        "replicaCount=2",
    ],
)
def test_evaluation_growth_requires_resizing_the_actual_dependency_budget(setting):
    assert "Peak PostgreSQL" in _render_error(
        "-f", str(HELM_CHART_DIR / "values-eval.yaml"), "--set", setting
    )
