"""Accounting v2 (native local_journal) fixture shared by acceptance and soak runs.

Every run applies ``values-accounting-native.yaml`` on top of the strict
production capacity fixture, so CI exercises the configuration production
runs: native accounting request and projection roles, outbox spend and audit,
and the batch worker role on the same database.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import threading

import httpx
from prometheus_client.parser import text_string_to_metric_families
import yaml

from src.db.catalog.model_deployments import ModelDeploymentRecord, ModelDeploymentRepository
from tests.performance.capacity_fixture import capacity_values
from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.gateway_concurrency_fixture import MODEL, seed
from tests.performance.lifecycle_cluster import NAMESPACE, LifecycleCluster
from tests.performance.lifecycle_fixtures import CHART, PROVIDER_KEEPALIVE_SECONDS

EMBEDDING_MODEL = "embedding-fixture"
EMBEDDING_DEPLOYMENT = "fixed-embedding"
SIGNING_SECRET = "deltallm-accounting-rpc"
GATEWAY_SELECTOR = "app.kubernetes.io/instance=gateway"
ROLE_SELECTORS = {
    "api": GATEWAY_SELECTOR + ",app.kubernetes.io/component=api",
    "batch_worker": GATEWAY_SELECTOR + ",app.kubernetes.io/component=batch-worker",
    "accounting_request": GATEWAY_SELECTOR + ",app.kubernetes.io/component=accounting-request",
    "accounting_worker": GATEWAY_SELECTOR + ",app.kubernetes.io/component=accounting-worker",
}
BATCH_ITEM_LEASE_SECONDS = 120
# Three batch pods at 20 runners each.
BATCH_MODEL_MAX_IN_FLIGHT = 60
SMALL_ROLE_RESOURCES = {
    "requests": {"cpu": "100m", "memory": "256Mi"},
    "limits": {"cpu": "2", "memory": "1Gi"},
}


def merge_values(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_values(base[key], value)
        else:
            base[key] = value
    return base


def apply_native_overlay(cluster: LifecycleCluster, values: dict) -> dict:
    """Merge the shipped native bundle and the fixture-only secret and network rules."""
    merge_values(values, yaml.safe_load((CHART / "values-accounting-native.yaml").read_text()))
    values["networkPolicy"].update(
        ingress=[{"from": [{"podSelector": {}}], "ports": [{"port": 4000, "protocol": "TCP"}]}],
        egress=[
            {
                "to": [{"podSelector": {"matchLabels": {"fixture": name}}}],
                "ports": [{"port": port, "protocol": "TCP"}],
            }
            for name, port in (("postgres", 5432), ("redis", 6379), ("provider", 8000))
        ],
    )
    cluster.apply(
        [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": SIGNING_SECRET},
                "stringData": {
                    "accounting-rpc-signing-secret": "issue320-kind-only-signing-key-000000000000"
                },
            }
        ]
    )
    return values


def embedding_model() -> dict:
    return {
        "model_name": EMBEDDING_MODEL,
        "deployment_id": EMBEDDING_DEPLOYMENT,
        "deltallm_params": {
            "model": "openai/fixed-embedding",
            "api_key": "local-mock-only",
            "api_base": "http://provider:8000/v1",
        },
        "model_info": {
            "mode": "embedding",
            "max_tokens": 8192,
            "input_cost_per_token": 0.0000001,
            "output_cost_per_token": 0,
            "rpm_limit": 100000,
            "tpm_limit": 10000000,
        },
    }


def native_values(
    cluster: LifecycleCluster,
    image: str,
    *,
    api_replicas: int,
    batch_replicas: int,
    request_replicas: int = 2,
) -> Path:
    """Production-strict capacity values with native accounting and batch on one node.

    One kind node lets every role share the fixture's ReadWriteOnce ``artifacts``
    volume (created by ``install_dependencies``), the same single-node local
    storage the lifecycle fixture uses. Production
    multi-node batch uses S3; artifact storage is not what these runs measure.
    """
    if not 1 <= api_replicas <= 4 or not 0 <= batch_replicas <= 3 or not 1 <= request_replicas <= 2:
        raise ValueError("Native fixture topology is outside its bounded allocation")
    path = capacity_values(cluster, image)
    values = apply_native_overlay(cluster, yaml.safe_load(path.read_text()))
    values["config"]["model_list"].append(embedding_model())
    for model in values["config"]["model_list"]:
        # Strict model capacity (fail_open false, as in production) refuses to
        # claim for a model with no known limit, so each model declares one.
        model["model_info"]["batch_capacity"] = {"max_in_flight": BATCH_MODEL_MAX_IN_FLIGHT}
    # Helm merges this map into values-capacity-fixture.yaml; the list is replaced.
    extended = values.setdefault("dependencyCapacity", {}).setdefault("extended", {})
    extended["providerDomains"] = {
        "local-mock": {"modelIds": ["fixed-one-token", EMBEDDING_DEPLOYMENT]}
    }
    values.update(
        replicaCount=api_replicas,
        resources=SMALL_ROLE_RESOURCES,
        autoscaling={"enabled": False},
        extraVolumes=[{"name": "artifacts", "persistentVolumeClaim": {"claimName": "artifacts"}}],
        extraVolumeMounts=[{"name": "artifacts", "mountPath": "/artifacts"}],
    )
    values["batchWorker"] = {
        "enabled": batch_replicas > 0,
        "replicaCount": max(batch_replicas, 1),
        "allowUnsafeLocalStorage": True,
        "resources": SMALL_ROLE_RESOURCES,
    }
    values["accountingRequest"].update(
        replicaCount=request_replicas, resources=SMALL_ROLE_RESOURCES
    )
    values["accountingWorker"].update(resources=SMALL_ROLE_RESOURCES)
    values["config"]["general_settings"].update(
        # Production batch shape (2026-11-02 prod values), scaled to the fixture.
        embeddings_batch_enabled=batch_replicas > 0,
        embeddings_batch_storage_dir="/artifacts",
        embeddings_batch_scheduler_mode="smart_v1",
        embeddings_batch_scheduler_claim_mode="work_slice",
        embeddings_batch_model_capacity_enabled=True,
        embeddings_batch_worker_concurrency=20,
        embeddings_batch_item_claim_limit=50,
        embeddings_batch_work_claim_max_items=50,
        embeddings_batch_max_attempts=5,
        embeddings_batch_item_lease_seconds=BATCH_ITEM_LEASE_SECONDS,
        embeddings_batch_stale_lease_sweeper_interval_seconds=1,
        embeddings_batch_stale_lease_sweeper_failure_interval_seconds=1,
        # Close idle sockets before the fixed provider's five-second limit.
        upstream_http_keepalive_expiry_seconds=PROVIDER_KEEPALIVE_SECONDS - 1,
    )
    path.write_text(yaml.safe_dump(values))
    (cluster.output / "fixture-values.yaml").write_text(path.read_text())
    profile = Path(cluster.directory.name) / "native-config.yaml"
    profile.write_text(yaml.safe_dump(values["config"]))
    os.environ["DELTALLM_CONFIG_PATH"] = str(profile)
    return path


async def seed_native_fixture(values: Path) -> None:
    """Seed the load key, grant it the embedding model, and seed every deployment."""
    await seed()
    models = yaml.safe_load(values.read_text())["config"]["model_list"]
    async with local_database() as db:
        repository = ModelDeploymentRepository(db)
        for model in models:
            await repository.create(
                ModelDeploymentRecord(
                    deployment_id=model["deployment_id"],
                    model_name=model["model_name"],
                    deltallm_params=model["deltallm_params"],
                    model_info=model["model_info"],
                )
            )
        await db.deltallm_callabletargetbinding.create(
            data={
                "callable_key": EMBEDDING_MODEL,
                "scope_type": "organization",
                "scope_id": "concurrency-org",
                "enabled": True,
            }
        )
        granted = [MODEL, EMBEDDING_MODEL]
        await db.execute_raw(
            "UPDATE deltallm_teamtable SET models=$1::text[] WHERE team_id='concurrency-team'",
            granted,
        )
        await db.execute_raw(
            "UPDATE deltallm_usertable SET models=$1::text[] WHERE user_id='concurrency-user'",
            granted,
        )
        await db.execute_raw(
            "UPDATE deltallm_verificationtoken SET models=$1::text[] "
            "WHERE key_name='concurrency-fixture'",
            granted,
        )


def role_pods(cluster: LifecycleCluster, role: str) -> list[str]:
    payload = json.loads(
        cluster.kubectl("get", "pods", "-l", ROLE_SELECTORS[role], "-o", "json").stdout
    )
    pods = []
    for pod in payload["items"]:
        if pod["metadata"].get("deletionTimestamp"):
            continue
        if any(
            condition["type"] == "Ready" and condition["status"] == "True"
            for condition in pod["status"].get("conditions", [])
        ):
            pods.append(pod["metadata"]["name"])
    if len(pods) > 16:
        raise RuntimeError("Native fixture exceeded its pod bound")
    return sorted(pods)


# The in-cluster load generator shares the release label; only role pods count.
GATEWAY_ROLE_PODS = (
    GATEWAY_SELECTOR
    + ",app.kubernetes.io/component in (api,batch-worker,accounting-request,accounting-worker)"
)


def pod_restarts(cluster: LifecycleCluster) -> dict[str, int]:
    """Container restarts per gateway pod. A restart clears in-memory stores."""
    payload = json.loads(
        cluster.kubectl("get", "pods", "-l", GATEWAY_ROLE_PODS, "-o", "json").stdout
    )
    return {
        pod["metadata"]["name"]: sum(
            status.get("restartCount", 0) for status in pod["status"].get("containerStatuses", [])
        )
        for pod in payload["items"]
    }


def restarted_since(baseline: dict[str, int], current: dict[str, int]) -> list[str]:
    """Pods that restarted a container, or were replaced, since ``baseline``."""
    return sorted(
        pod for pod, count in current.items() if pod not in baseline or count > baseline[pod]
    )


def collect_role_logs(cluster: LifecycleCluster) -> None:
    """Bounded logs per role, previous containers included, and cluster events.

    The shared gateway log is cut at 5,000 lines, which busy batch pods fill on
    their own; per-role files keep the API pods visible. Previous container
    logs and events explain a restart. A failed diagnostic never replaces the
    original failure.
    """
    destination = cluster.output / "roles"
    destination.mkdir(parents=True, exist_ok=True)
    commands = {
        "events": ("get", "events", "--sort-by=.lastTimestamp", "-o", "wide"),
        **{
            f"{role}{suffix}": (
                "logs",
                "-l",
                selector,
                "--all-containers",
                "--prefix",
                "--tail=2000",
                "--max-log-requests=8",
                *flags,
            )
            for role, selector in ROLE_SELECTORS.items()
            for suffix, flags in (("", ()), ("-previous", ("--previous",)))
        },
    }
    for name, arguments in commands.items():
        try:
            result = cluster.kubectl(*arguments, "--request-timeout=10s", check=False, timeout=30)
            payload = (result.stdout + result.stderr)[-2 * 1024 * 1024 :]
        except Exception as error:
            payload = "Log collection failed: " + type(error).__name__ + "\n"
        (destination / f"{name}.log").write_text(payload)


FOLLOWED_ROLES = ("api", "accounting_request", "accounting_worker", "batch_worker")
FOLLOW_POLL_SECONDS = 2.0


@contextmanager
def follow_role_logs(cluster: LifecycleCluster, step: str, roles=FOLLOWED_ROLES):
    """Stream each pod's log to a file while a disruptive step runs.

    A rollout deletes pods, so their logs are gone before the step ends. New
    pods are picked up on the next poll; a follower that exits early (pod not
    running yet) is started again. The acceptance batches are small, so batch
    pods are followed too; the soak does not follow pods.
    """
    destination = cluster.output / "follow" / step
    destination.mkdir(parents=True, exist_ok=True)
    selector = ",".join(
        [
            GATEWAY_SELECTOR,
            "app.kubernetes.io/component in ("
            + ",".join(ROLE_SELECTORS[role].rsplit("=", 1)[1] for role in roles)
            + ")",
        ]
    )
    followers: dict[str, subprocess.Popen] = {}
    stop = threading.Event()

    def start(pod: str) -> None:
        log = (destination / f"{pod}.log").open("a")
        try:
            followers[pod] = subprocess.Popen(
                [
                    "kubectl",
                    "--kubeconfig",
                    cluster.kubeconfig,
                    "-n",
                    NAMESPACE,
                    "logs",
                    "--follow",
                    "--timestamps",
                    "--all-containers",
                    pod,
                ],
                stdout=log,
                stderr=log,
            )
        finally:
            log.close()

    def poll() -> None:
        while not stop.is_set():
            try:
                result = cluster.kubectl(
                    "get",
                    "pods",
                    "-l",
                    selector,
                    "-o",
                    "name",
                    "--request-timeout=5s",
                    check=False,
                    timeout=15,
                )
                for line in result.stdout.split():
                    pod = line.removeprefix("pod/")
                    running = followers.get(pod)
                    if (running is None or running.poll() is not None) and len(followers) < 32:
                        start(pod)
            except Exception:  # Diagnostics never fail the step.
                pass
            stop.wait(FOLLOW_POLL_SECONDS)

    thread = threading.Thread(target=poll, name=f"follow-{step}", daemon=True)
    thread.start()
    try:
        yield destination
    finally:
        stop.set()
        thread.join(timeout=20)
        for process in followers.values():
            process.terminate()
        for process in followers.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def parse_metrics(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Return samples by sample name; counters keep their ``_total`` suffix."""
    samples: dict[str, list[tuple[dict[str, str], float]]] = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name.startswith("deltallm_"):
                samples.setdefault(sample.name, []).append((dict(sample.labels), sample.value))
    return samples


async def scrape_roles(cluster: LifecycleCluster, roles: tuple[str, ...]) -> dict[str, dict]:
    """Scrape /metrics from every ready pod of each role through short port-forwards."""
    pods = {
        pod: role for role in roles for pod in await asyncio.to_thread(role_pods, cluster, role)
    }
    result: dict[str, dict] = {}
    with ExitStack() as forwards:
        ports = {
            pod: forwards.enter_context(cluster.forward("pod/" + pod, 4000)) for pod in sorted(pods)
        }
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            for pod, port in ports.items():
                response = await client.get(f"http://127.0.0.1:{port}/metrics")
                response.raise_for_status()
                if len(response.content) > 8 * 1024 * 1024:
                    raise RuntimeError("Pod metrics exceeded their bounded size")
                result[pod] = {"role": pods[pod], "samples": parse_metrics(response.text)}
    return result
