"""The destructive acceptance helper must stay inside its owned kind fixture."""

import json
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from tests.performance.lifecycle_cluster import LifecycleCluster


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
