import pytest

from tests.helm.test_batch_worker_split import _render, _render_error
from tests.helm.test_dependency_capacity import capacity

pytestmark = pytest.mark.helm

POOLER = (
    "--set",
    "runtime.databasePooler.enabled=true",
    "--set",
    "runtime.databasePooler.existingSecret.name=deltallm-pooler",
    "--set",
    "runtime.databasePooler.serverConnections=60",
    "--set",
    "runtime.databasePooler.maxClientConnections=2000",
)


def _env(document):
    return {
        entry["name"]: entry
        for container in document["spec"]["template"]["spec"]["containers"]
        for entry in container.get("env", [])
    }


def test_pooled_pod_pools_count_against_the_pooler_and_server_connections_once():
    direct = capacity(_render("--set", "batchWorker.enabled=true"))
    pooled = capacity(_render("--set", "batchWorker.enabled=true", *POOLER))
    clients = int(pooled["postgresql-pooler-client-connections"])
    assert int(direct["postgresql-pooler-client-connections"]) == 0
    assert clients > 0
    # The pod pools move to the pooler; PostgreSQL counts the pooler's 60.
    assert int(direct["postgresql-connections-including-reserve"]) - clients == (
        int(pooled["postgresql-connections-including-reserve"]) - 60
    )


def test_pooler_url_reaches_api_and_worker_pods_but_not_the_migration_job():
    docs = _render("--set", "batchWorker.enabled=true", *POOLER)
    deployments = [doc for doc in docs if doc.get("kind") == "Deployment"]
    assert deployments
    for deployment in deployments:
        env = _env(deployment)
        reference = env["DELTALLM_DATABASE_POOLER_URL"]["valueFrom"]["secretKeyRef"]
        assert reference == {"name": "deltallm-pooler", "key": "DELTALLM_DATABASE_POOLER_URL"}
        assert "DATABASE_URL" in env
    for job in (doc for doc in docs if doc.get("kind") == "Job"):
        assert "DELTALLM_DATABASE_POOLER_URL" not in _env(job)


def test_pooler_is_off_by_default():
    docs = _render()
    for deployment in (doc for doc in docs if doc.get("kind") == "Deployment"):
        assert "DELTALLM_DATABASE_POOLER_URL" not in _env(deployment)


@pytest.mark.parametrize(
    ("setting", "message"),
    [
        ("runtime.databasePooler.existingSecret.name=", "existingSecret.name is required"),
        ("runtime.databasePooler.serverConnections=0", "must be declared"),
        ("runtime.databasePooler.maxClientConnections=10", "pooler client connections"),
        (
            "batchWorker.config.general_settings.db_statement_timeout_seconds=1.5",
            "same database deadlines",
        ),
    ],
)
def test_unsafe_pooler_profiles_fail_render(setting, message):
    assert message in _render_error("--set", "batchWorker.enabled=true", *POOLER, "--set", setting)
