"""The destructive acceptance helper must stay inside its owned kind fixture."""

from contextlib import contextmanager
import json
from pathlib import Path
from subprocess import CompletedProcess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import yaml

from tests.performance.lifecycle_cluster import LifecycleCluster
from tests.performance.capacity_fixture import install_direct_api
from tests.performance.lifecycle_fixtures import chart_values
from tests.performance import lifecycle_recovery
from tests.performance import run_capacity_acceptance


@pytest.mark.parametrize("migration_fails", [False, True])
def test_capacity_runner_migrates_candidate_before_installing_unchanged_baseline(
    tmp_path, monkeypatch, migration_fails
):
    candidate = "deltallm-capacity:candidate"
    baseline = "deltallm-capacity:baseline"
    cluster = LifecycleCluster(tmp_path / "evidence", purpose="pr9-capacity", nodes=2)
    phases = []

    @contextmanager
    def owned(image):
        assert image == candidate
        yield cluster

    def prime(target, values):
        assert target is cluster
        phases.append(("migrate", yaml.safe_load(values.read_text())["image"]["tag"]))
        if migration_fails:
            raise RuntimeError("candidate migration failed")

    def release(target, values):
        assert target is cluster
        phases.append(("release", yaml.safe_load(values.read_text())["image"]["tag"]))

    monkeypatch.setattr(run_capacity_acceptance, "LifecycleCluster", lambda *args, **kw: cluster)
    monkeypatch.setattr(cluster, "owned", owned)
    monkeypatch.setattr(cluster, "run", Mock())
    monkeypatch.setattr(run_capacity_acceptance, "source_manifest", lambda *args: {})
    monkeypatch.setattr(run_capacity_acceptance, "install_capacity_dependencies", Mock())
    monkeypatch.setattr(run_capacity_acceptance, "install_monitoring", Mock())
    monkeypatch.setattr(run_capacity_acceptance, "prime_database", prime)
    monkeypatch.setattr(run_capacity_acceptance, "capacity_release", release)
    edge = Mock()
    exercise = AsyncMock()
    monkeypatch.setattr(run_capacity_acceptance, "install_edge", edge)
    monkeypatch.setattr(run_capacity_acceptance, "exercise", exercise)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capacity",
            "--image",
            candidate,
            "--baseline-image",
            baseline,
            "--baseline-manifest",
            str(tmp_path / "baseline.json"),
            "--output",
            str(cluster.output),
        ],
    )
    try:
        if migration_fails:
            with pytest.raises(RuntimeError, match="candidate migration failed"):
                run_capacity_acceptance.main()
            assert phases == [("migrate", "candidate")]
            edge.assert_not_called()
            exercise.assert_not_called()
            assert not cluster.events
        else:
            run_capacity_acceptance.main()
            assert phases == [("migrate", "candidate"), ("release", "baseline")]
            edge.assert_called_once_with(cluster)
            exercise.assert_awaited_once_with(
                cluster, Path(cluster.directory.name) / "capacity-values.yaml", candidate, baseline
            )
            assert cluster.events[-1]["event"] == "capacity_acceptance_completed"
    finally:
        cluster.directory.cleanup()


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


def test_pr8_lifecycle_fixture_does_not_inherit_pr9_capacity_path(tmp_path):
    cluster = LifecycleCluster(tmp_path)
    try:
        values = yaml.safe_load(chart_values(cluster, "deltallm:test").read_text())
        assert values["managedLifecycle"]["production"] is False
        assert values["dependencyCapacity"]["extended"]["enabled"] is False
        assert values["config"]["general_settings"]["gateway_ingress_enabled"] is False
    finally:
        cluster.directory.cleanup()


def test_kind_nodeport_mapping_is_bounded_and_loopback_only(tmp_path, monkeypatch):
    cluster = LifecycleCluster(tmp_path, nodes=2, port_mappings={30080: 59440})
    commands = []

    def run(*command, **options):
        commands.append((command, options))
        if command[-1] == "version":
            return CompletedProcess(command, 0, "kind v0.31.0")
        return CompletedProcess(command, 0, "")

    monkeypatch.setattr(cluster, "run", run)
    monkeypatch.setattr(cluster, "kubectl", lambda *args, **options: CompletedProcess(args, 0, ""))
    try:
        with cluster.owned("deltallm:test"):
            config_path = next(
                option
                for command, _ in commands
                for index, option in enumerate(command)
                if index and command[index - 1] == "--config"
            )
            config = yaml.safe_load(Path(config_path).read_text())
            assert config["nodes"][0]["extraPortMappings"] == [
                {
                    "containerPort": 30080,
                    "hostPort": 59440,
                    "listenAddress": "127.0.0.1",
                    "protocol": "TCP",
                }
            ]
    finally:
        cluster.directory.cleanup()


@pytest.mark.parametrize("mappings", ({29999: 59440}, {30080: 80}, {30080: 59440, 30081: 59440}))
def test_kind_nodeport_mapping_rejects_unsafe_values(tmp_path, mappings):
    with pytest.raises(ValueError, match="port mappings"):
        LifecycleCluster(tmp_path, port_mappings=mappings)


def test_direct_api_nodeport_has_no_synthetic_proxy_queue():
    cluster = SimpleNamespace(apply=Mock())

    install_direct_api(cluster, node_port=30080)

    (documents,) = cluster.apply.call_args.args
    service = documents[0]
    assert service["metadata"]["name"] == "capacity-api-direct"
    assert service["spec"]["type"] == "NodePort"
    assert service["spec"]["ports"][0]["nodePort"] == 30080
    assert service["spec"]["selector"]["app.kubernetes.io/component"] == "api"


@pytest.mark.parametrize("node_port", [29999, 32768])
def test_direct_api_nodeport_rejects_unsafe_values(node_port):
    with pytest.raises(ValueError, match="outside the Kubernetes range"):
        install_direct_api(SimpleNamespace(apply=Mock()), node_port=node_port)


@pytest.mark.asyncio
async def test_edge_readiness_retries_early_transport_disconnects(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get(self, url):
            self.calls += 1
            if self.calls == 1:
                raise httpx.RemoteProtocolError("edge is not ready")
            return httpx.Response(503 if self.calls == 2 else 200)

    client = Client()
    monkeypatch.setattr(run_capacity_acceptance.httpx, "AsyncClient", lambda **kwargs: client)

    async def no_sleep(seconds):
        assert seconds == 2

    monkeypatch.setattr(run_capacity_acceptance.asyncio, "sleep", no_sleep)
    await run_capacity_acceptance.wait_edge("http://127.0.0.1:59440")
    assert client.calls == 3


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
