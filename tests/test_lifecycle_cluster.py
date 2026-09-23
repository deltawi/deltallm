"""The destructive acceptance helper must stay inside its owned kind fixture."""

import json
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from tests.performance.lifecycle_cluster import LifecycleCluster
from tests.performance import lifecycle_recovery


def test_event_timeline_fields_cannot_be_overwritten(tmp_path):
    cluster = LifecycleCluster(tmp_path)
    try:
        cluster.event("started", duration_seconds=1.5)
        assert cluster.events[0]["event"] == "started"
        assert cluster.events[0]["seconds"] >= 0
        assert cluster.events[0]["duration_seconds"] == 1.5
        with pytest.raises(ValueError, match="reserved fields"):
            cluster.event("invalid", seconds=1.5)
    finally:
        cluster.directory.cleanup()


@pytest.fixture
def owned_pod(tmp_path, monkeypatch):
    cluster = LifecycleCluster(tmp_path)
    document = {
        "metadata": {
            "namespace": "lifecycle",
            "labels": {"app.kubernetes.io/instance": "gateway"},
        },
        "spec": {"nodeName": cluster.name + "-control-plane"},
        "status": {"containerStatuses": [{"containerID": "containerd://" + "a" * 64}]},
    }
    monkeypatch.setattr(
        cluster, "kubectl", lambda *args: CompletedProcess(args, 0, json.dumps(document))
    )
    runtime = Mock()
    monkeypatch.setattr(cluster, "run", runtime)
    try:
        yield cluster, document, runtime
    finally:
        cluster.directory.cleanup()


def test_abrupt_loss_signals_the_selected_container_through_its_parent_runtime(owned_pod):
    cluster, _, runtime = owned_pod
    assert cluster.kill_container("fixture-pod") == "containerd://" + "a" * 64
    runtime.assert_called_once_with(
        "docker",
        "exec",
        cluster.name + "-control-plane",
        "ctr",
        "--namespace",
        "k8s.io",
        "tasks",
        "kill",
        "--signal",
        "SIGKILL",
        "a" * 64,
        timeout=15,
    )


@pytest.mark.parametrize("invalid", ["namespace", "release", "node", "multiple", "identity"])
def test_abrupt_loss_rejects_unowned_or_ambiguous_targets(owned_pod, invalid):
    cluster, document, runtime = owned_pod
    if invalid == "namespace":
        document["metadata"]["namespace"] = "user-workload"
    elif invalid == "release":
        document["metadata"]["labels"]["app.kubernetes.io/instance"] = "user-workload"
    elif invalid == "node":
        document["spec"]["nodeName"] = "user-control-plane"
    elif invalid == "multiple":
        document["status"]["containerStatuses"] *= 2
    else:
        document["status"]["containerStatuses"][0]["containerID"] = "containerd://--all"
    with pytest.raises(ValueError, match="Pod loss requires"):
        cluster.kill_container("fixture-pod")
    runtime.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("active_withdraws", [False, True])
async def test_retiring_pod_cannot_prove_dependency_readiness_recovery(
    tmp_path, monkeypatch, active_withdraws
):
    sample = 0

    async def advance(_):
        nonlocal sample
        sample += 1

    def kubectl(*args):
        if args[0] == "exec":
            return CompletedProcess(args, 0, "OK")
        active_ready = not active_withdraws or sample in (0, 3)
        pods = [
            {
                "metadata": {"name": name},
                "status": {"conditions": [{"type": "Ready", "status": str(active_ready)}]},
            }
            for name in ("api-a", "api-b")
        ]
        if sample < 3:
            pods.append(
                {
                    "metadata": {"name": "retiring-api", "deletionTimestamp": "fixture"},
                    "status": {"conditions": [{"type": "Ready", "status": str(sample == 0)}]},
                }
            )
        return CompletedProcess(args, 0, json.dumps({"items": pods}))

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503 if request.url.path.endswith("readiness") and sample < 2 else 200
            )
        )
    )
    monkeypatch.setattr(lifecycle_recovery.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(lifecycle_recovery, "monotonic", lambda: sample * 20)
    monkeypatch.setattr(lifecycle_recovery.asyncio, "sleep", advance)
    cluster = SimpleNamespace(kubectl=kubectl, output=tmp_path, event=Mock())
    if active_withdraws:
        await lifecycle_recovery.readiness_recovery(cluster, ["http://api-a", "http://api-b"])
        cluster.event.assert_called_once_with("dependency_and_probe_hysteresis_recovered")
    else:
        with pytest.raises(TimeoutError, match="readiness did not recover"):
            await lifecycle_recovery.readiness_recovery(cluster, ["http://api-a", "http://api-b"])
        cluster.event.assert_not_called()
