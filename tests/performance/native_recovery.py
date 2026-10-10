"""Check process loss and a same-image Helm rollout on the owned native fixture."""

import asyncio
from contextlib import ExitStack
import json
from pathlib import Path

from tests.performance.capacity_fixture import capacity_release
from tests.performance.lifecycle_cluster import LifecycleCluster
from tests.performance.native_client_requests import verify_native_clients
from tests.performance.run_capacity_acceptance import wait_edge

ROLES = {"api": 4, "accounting-request": 2, "accounting-worker": 1}


def role_pods(cluster: LifecycleCluster) -> dict[str, list[dict]]:
    result = {}
    for role, count in ROLES.items():
        selector = f"app.kubernetes.io/instance=gateway,app.kubernetes.io/component={role}"
        documents = json.loads(cluster.kubectl("get", "pods", "-l", selector, "-o", "json").stdout)[
            "items"
        ]
        pods = [pod for pod in documents if not pod["metadata"].get("deletionTimestamp")]
        if len(pods) != count or any(
            not any(
                item["type"] == "Ready" and item["status"] == "True"
                for item in pod["status"].get("conditions", [])
            )
            for pod in pods
        ):
            raise RuntimeError(f"Native recovery requires {count} ready {role} pods")
        result[role] = pods
    return result


async def check_clients(cluster: LifecycleCluster, pods: dict, phase: str) -> None:
    output = cluster.output / phase
    output.mkdir()
    with ExitStack() as forwards:
        ports = {
            role: [
                forwards.enter_context(cluster.forward("pod/" + pod["metadata"]["name"], 4000))
                for pod in documents
            ]
            for role, documents in pods.items()
        }
        for port in sum(ports.values(), []):
            await wait_edge(f"http://127.0.0.1:{port}")
        await verify_native_clients(
            ports["api"], ports["accounting-request"] + ports["accounting-worker"], output
        )


async def verify_native_recovery(cluster: LifecycleCluster, values: Path) -> None:
    before = await asyncio.to_thread(role_pods, cluster)
    losses = []
    for role, pods in before.items():
        pod = pods[0]
        name = pod["metadata"]["name"]
        status = pod["status"]["containerStatuses"]
        if len(status) != 1:
            raise RuntimeError("Native process loss requires one container per pod")
        expected = status[0]["restartCount"] + 1
        container = await asyncio.to_thread(cluster.kill_container, name)
        await asyncio.to_thread(
            cluster.kubectl,
            "wait",
            f"--for=jsonpath={{.status.containerStatuses[0].restartCount}}={expected}",
            "pod/" + name,
            "--timeout=180s",
            timeout=190,
        )
        await asyncio.to_thread(
            cluster.kubectl,
            "wait",
            "--for=condition=Ready",
            "pod/" + name,
            "--timeout=180s",
            timeout=190,
        )
        losses.append(
            {"role": role, "pod": name, "container": container, "restart_count": expected}
        )
    restarted = await asyncio.to_thread(role_pods, cluster)
    await check_clients(cluster, restarted, "native-process-recovery")
    # Change only a fixture annotation. Keep the image and all runtime limits fixed.
    overrides = []
    for prefix in ("", "accountingRequest.", "accountingWorker."):
        overrides.extend(["--set-string", prefix + "podAnnotations.release-readiness=verified"])
    await asyncio.to_thread(capacity_release, cluster, values, *overrides)
    for role in ROLES:
        deployment = "deployment/gateway-deltallm" + ("" if role == "api" else "-" + role)
        await asyncio.to_thread(
            cluster.kubectl,
            "rollout",
            "status",
            deployment,
            "--timeout=180s",
            timeout=190,
        )
    rolled = await asyncio.to_thread(role_pods, cluster)
    old_uids = {pod["metadata"]["uid"] for pods in restarted.values() for pod in pods}
    new_uids = {pod["metadata"]["uid"] for pods in rolled.values() for pod in pods}
    if old_uids & new_uids:
        raise RuntimeError("The native Helm rollout did not replace every process")
    await check_clients(cluster, rolled, "native-rollout-recovery")
    (cluster.output / "native-recovery.json").write_text(
        json.dumps(
            {"passed": True, "process_losses": losses, "rollout_replaced_all_pods": True}, indent=2
        )
        + "\n"
    )
    cluster.event("native_recovery_completed", passed=True)
