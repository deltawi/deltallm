"""Bounded per-pod and dependency evidence for the PR9 acceptance campaign."""

import asyncio
from dataclasses import asdict
import hashlib
import json

import httpx

from src.deployment_capacity_report import CapacityReport
from tests.performance.gateway_concurrency_dependencies import local_dependencies
from tests.performance.gateway_concurrency_metrics import select_metrics
from tests.performance.lifecycle_cluster import LifecycleCluster
from tests.performance.capacity_monitoring import custom_metrics

API_SELECTOR = "app.kubernetes.io/instance=gateway,app.kubernetes.io/component=api"
API_DEPLOYMENT = "gateway-deltallm"


def api_pods(cluster: LifecycleCluster, *, ready_only: bool = True) -> list[dict]:
    result = json.loads(cluster.kubectl("get", "pods", "-l", API_SELECTOR, "-o", "json").stdout)
    pods = [pod for pod in result["items"] if not pod["metadata"].get("deletionTimestamp")]
    if len(pods) > 32:
        raise RuntimeError("Capacity experiment exceeded its pod bound")
    if ready_only:
        pods = [
            pod
            for pod in pods
            if any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in pod["status"].get("conditions", [])
            )
        ]
    return pods


async def wait_pods(cluster: LifecycleCluster, count: int, *, timeout: float = 180) -> list[dict]:
    async with asyncio.timeout(timeout):
        while True:
            pods = await asyncio.to_thread(api_pods, cluster)
            if len(pods) == count:
                return pods
            await asyncio.sleep(2)


async def sample(cluster: LifecycleCluster, label: str, *, observe_processes: bool = True) -> dict:
    pods = await asyncio.to_thread(api_pods, cluster)
    values = []
    for pod in pods:
        name = pod["metadata"]["name"]
        with cluster.forward("pod/" + name, 4000) as port:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{port}/metrics")
                response.raise_for_status()
                metrics = [asdict(value) for value in select_metrics(response.text, buckets=False)]
        process = None
        if observe_processes:
            raw = await asyncio.to_thread(
                cluster.kubectl,
                "exec",
                name,
                "--",
                "python",
                "-m",
                "src.deployment_capacity_observation",
            )
            process = json.loads(raw.stdout)
        values.append(
            {
                "pod": name,
                "node": pod["spec"]["nodeName"],
                "image_id": pod["status"]["containerStatuses"][0]["imageID"],
                "metrics": metrics,
                "process": process,
            }
        )
    async with local_dependencies() as dependencies:
        db = await dependencies.database.query_raw(
            "SELECT count(*)::int AS connections FROM pg_stat_activity"
        )
        redis = await dependencies.redis.info("clients")
    result = {
        "pods": values,
        "postgresql_connections": db[0]["connections"],
        "redis_connections": redis["connected_clients"],
    }
    (cluster.output / f"{label}-samples.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


async def preflight(cluster: LifecycleCluster) -> CapacityReport:
    payload = json.loads(
        cluster.kubectl(
            "get", "configmap", "gateway-deltallm-dependency-capacity", "-o", "json"
        ).stdout
    )
    report = CapacityReport.model_validate_json(payload["data"]["report.json"])
    config = json.loads(
        cluster.kubectl("get", "configmap", "gateway-deltallm-config", "-o", "json").stdout
    )
    config_sha = hashlib.sha256(config["data"]["config.yaml"].encode()).hexdigest()
    nodes = json.loads(cluster.kubectl("get", "nodes", "-o", "json").stdout)["items"]
    ready_nodes = [
        node
        for node in nodes
        if any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"])
    ]
    assert ready_nodes and len(ready_nodes) == len(nodes)
    schedulable_nodes = [
        node
        for node in ready_nodes
        if not any(
            taint.get("effect") in {"NoSchedule", "NoExecute"}
            for taint in node["spec"].get("taints", [])
        )
    ]
    assert len(schedulable_nodes) == len(nodes)
    pods = api_pods(cluster)
    assert pods
    for pod in pods:
        resources = pod["spec"]["containers"][0]["resources"]
        assert resources["requests"] == {"cpu": "1", "memory": "2Gi"}
        assert resources["limits"] == {"cpu": "2", "memory": "4Gi"}
    async with asyncio.timeout(120):
        while True:
            try:
                metrics = await asyncio.to_thread(custom_metrics, cluster)
            except RuntimeError:
                metrics = {"items": []}
            if {item["describedObject"]["name"] for item in metrics["items"]} == {
                pod["metadata"]["name"] for pod in pods
            }:
                break
            await asyncio.sleep(3)
    async with local_dependencies() as dependencies:
        maximum = await dependencies.database.query_raw("SHOW max_connections")
        assert int(maximum[0]["max_connections"]) >= report.postgresql_connections
        config = await dependencies.redis.config_get("maxclients", "maxmemory", "maxmemory-policy")
        assert int(config["maxclients"]) >= report.redis_connections
        assert int(config["maxmemory"]) == 128 * 1024 * 1024
        assert config["maxmemory-policy"] == "noeviction"
        models = await dependencies.database.query_raw(
            "SELECT deployment_id FROM deltallm_modeldeployment LIMIT 1025"
        )
        assert len(models) <= 1024
        declared = {
            model for domain in report.provider_domains.values() for model in domain.model_ids
        }
        assert {row["deployment_id"] for row in models} <= declared
    (cluster.output / "capacity-report.json").write_text(
        report.model_dump_json(by_alias=True, indent=2) + "\n"
    )
    (cluster.output / "preflight.json").write_text(
        json.dumps(
            {
                "config_sha256": config_sha,
                "report_sha256": hashlib.sha256(
                    payload["data"]["report.json"].encode()
                ).hexdigest(),
                "ready_nodes": [
                    {
                        "name": node["metadata"]["name"],
                        "allocatable": {
                            key: node["status"]["allocatable"][key]
                            for key in ("cpu", "memory", "pods")
                        },
                    }
                    for node in ready_nodes
                ],
                "schedulable_nodes": [node["metadata"]["name"] for node in schedulable_nodes],
                "postgresql_max_connections": int(maximum[0]["max_connections"]),
                "redis_maxclients": int(config["maxclients"]),
                "ready_api_pods": [pod["metadata"]["name"] for pod in pods],
                "custom_metrics_pods": [
                    item["describedObject"]["name"] for item in metrics["items"]
                ],
                "provider_quota_enforcement": "workload-envelope",
            },
            indent=2,
        )
        + "\n"
    )
    return report
