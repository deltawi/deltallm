"""Qualify one clean native-accounting image on a disposable pinned kind cluster."""

from __future__ import annotations
import argparse
import asyncio
from contextlib import ExitStack
import json
import os
import subprocess
from pathlib import Path
import yaml

from scripts.prepare_capacity_adapter import MonitoringDependencies
from tests.performance.capacity_fixture import (
    capacity_values,
    capacity_release,
    install_capacity_dependencies,
)
from tests.performance.capacity_monitoring import install_resource_metrics
from tests.performance.capacity_samples import api_pods
from tests.performance.cluster_load_generator import (
    run_cluster_generator,
    CLUSTER_GENERATOR_PROOF_JOB,
)
from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.gateway_concurrency_diagnostics import KubernetesResourceRecorder
from tests.performance.lifecycle_cluster import LifecycleCluster, LOAD_KEY, MASTER_KEY, SALT_KEY
from tests.performance.lifecycle_fixtures import CHART
from tests.performance.native_qualification_economics import (
    accounting_snapshot,
    reconcile_native,
    wait_native_drain,
    native_storage_snapshot,
)
from tests.performance.run_accounting_diagnostic import (
    activate_accounting,
    candidate_manifest,
    ready_pods,
    server_manifest,
)
from tests.performance.run_capacity_acceptance import prime_database, wait_edge
from tests.performance.run_gateway_concurrency import measure, generator_evidence_failures
from tests.performance.gateway_concurrency_manifest import read_manifest
from scripts.measure_gateway_load import summarize
from tests.performance.native_qualification_resources import (
    capture_cpu_counters,
    cpu_counter_deltas,
)
from tests.performance.qualification_image_archive import platform_manifest_digest

RATES = (50, 100, 200, 500)
REQUEST_SELECTOR = (
    "app.kubernetes.io/instance=gateway,app.kubernetes.io/component=accounting-request"
)
PROJECTION_SELECTOR = (
    "app.kubernetes.io/instance=gateway,app.kubernetes.io/component=accounting-worker"
)
FIXTURE_IMAGES = (
    (
        "postgres:15",
        "postgres@sha256:724292da1f2e50bdccfc3302ce75bbba7f4a6076701b588cc795fcac65683550",
    ),
    ("redis:7", "redis@sha256:c6eabf748fc7a61dbb5a705c78bcf3d6377b1127a97d0ce965c11c44ba46896f"),
)


def merge_values(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_values(base[key], value)
        else:
            base[key] = value
    return base


def qualification_values(cluster: LifecycleCluster, image: str) -> Path:
    path = capacity_values(cluster, image)
    values = yaml.safe_load(path.read_text())
    merge_values(values, yaml.safe_load((CHART / "values-accounting-native.yaml").read_text()))
    values.update(
        replicaCount=4,
        resources={
            "requests": {"cpu": "1", "memory": "256Mi"},
            "limits": {"cpu": "2", "memory": "1Gi"},
        },
        autoscaling={"enabled": False},
        batchWorker={"enabled": False},
        topologySpreadConstraints=[
            {
                "maxSkew": 1,
                "topologyKey": "kubernetes.io/hostname",
                "whenUnsatisfiable": "DoNotSchedule",
                "labelSelector": {"matchLabels": {"app.kubernetes.io/component": "api"}},
            }
        ],
    )
    role_resources = {
        "requests": {"cpu": "100m", "memory": "256Mi"},
        "limits": {"cpu": "2", "memory": "1Gi"},
    }
    values["accountingRequest"].update(
        replicaCount=2,
        resources=role_resources,
        config={"general_settings": {"accounting_microbatch_max_size": 32}},
    )
    values["accountingWorker"].update(resources=role_resources)
    values["config"]["general_settings"].update(
        accounting_projection_batch_size=256,
        accounting_projection_max_concurrent_partitions=4,
        accounting_statement_timeout_ms=250,
        accounting_grant_target_operations=32,
        accounting_grant_ttl_seconds=30,
        accounting_max_provider_attempts=3,
        spend_operation_intents_enabled=False,
        prompt_negative_cache_enabled=True,
        prompt_negative_l1_ttl_seconds=30,
        gateway_ingress_max_active=256,
        gateway_preflight_global_max_parallel=150,
        gateway_preflight_org_max_parallel=150,
        redis_critical_max_waiters=64,
        redis_cache_max_waiters=0,
        redis_bulk_max_waiters=0,
    )
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
                "metadata": {"name": "deltallm-accounting-rpc"},
                "stringData": {
                    "accounting-rpc-signing-secret": "issue320-kind-only-signing-key-000000000000"
                },
            }
        ]
    )
    path.write_text(yaml.safe_dump(values))
    (cluster.output / "fixture-values.yaml").write_text(path.read_text())
    profile = Path(cluster.directory.name) / "qualification-config.yaml"
    profile.write_text(yaml.safe_dump(values["config"]))
    os.environ["DELTALLM_CONFIG_PATH"] = str(profile)
    return path


def preload_images(cluster: LifecycleCluster) -> str:
    monitoring = MonitoringDependencies.read()
    metrics_image = None
    for index, (target, source) in enumerate(
        (*FIXTURE_IMAGES, (monitoring.metrics_server_image, monitoring.metrics_server_image))
    ):
        cluster.run("docker", "pull", source, timeout=300)
        alias = target.partition("@")[0]
        if alias != source:
            cluster.run("docker", "tag", source, alias, timeout=30)
        archive = Path(cluster.directory.name) / f"dependency-{index}.tar"
        platform = cluster.run(
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Os}}/{{.Architecture}}",
            alias,
            timeout=30,
        ).stdout.strip()
        if platform not in {"linux/arm64", "linux/amd64"}:
            raise RuntimeError("Fixture image platform is not supported")
        cluster.run(
            "docker",
            "image",
            "save",
            "--platform",
            platform,
            "-o",
            str(archive),
            alias,
            timeout=300,
        )
        cluster.run(
            cluster.kind, "load", "image-archive", str(archive), "--name", cluster.name, timeout=300
        )
        if target == monitoring.metrics_server_image:
            digest = platform_manifest_digest(archive)
            metrics_image = alias.rsplit(":", 1)[0] + "@" + digest
            for node in sorted(cluster.node_names):
                cluster.run(
                    "docker",
                    "exec",
                    node,
                    "ctr",
                    "-n",
                    "k8s.io",
                    "images",
                    "tag",
                    alias,
                    metrics_image,
                    timeout=30,
                )
            cluster.event(
                "metrics_image_imported",
                pinned_source=source,
                platform=platform,
                platform_image=metrics_image,
            )
    if metrics_image is None:
        raise RuntimeError("Pinned resource metrics image was not imported")
    return metrics_image


def pin_api_services(cluster: LifecycleCluster, pods: list[str]) -> list[str]:
    if len(pods) != 4:
        raise RuntimeError("Qualification requires exactly four ready API processes")
    documents, endpoints = [], []
    for index, pod in enumerate(sorted(pods)):
        cluster.kubectl(
            "label", "pod", pod, f"qualification.deltallm.ai/index={index}", "--overwrite"
        )
        name = f"qualification-api-{index}"
        documents.append(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name},
                "spec": {
                    "selector": {"qualification.deltallm.ai/index": str(index)},
                    "ports": [{"port": 4000, "targetPort": "http"}],
                },
            }
        )
        endpoints.append(f"http://{name}:4000/v1/chat/completions")
    cluster.apply(documents)
    return endpoints


def latency_and_queue_gates(report: dict[str, object]) -> dict[str, object]:
    latency = report["latency_seconds"]
    points = report["client_in_flight"]
    middle = points[len(points) // 10 : max(len(points) // 10 + 1, len(points) * 9 // 10)]
    mean_x = sum(point["offset_seconds"] for point in middle) / len(middle)
    mean_y = sum(point["client_in_flight"] for point in middle) / len(middle)
    denominator = sum((point["offset_seconds"] - mean_x) ** 2 for point in middle)
    slope = (
        sum(
            (point["offset_seconds"] - mean_x) * (point["client_in_flight"] - mean_y)
            for point in middle
        )
        / denominator
        if denominator
        else 0
    )
    success_ratio = report["success_count"] / report["target_count"]
    return {
        "success_ratio": success_ratio,
        "success_passed": success_ratio >= 0.999,
        "p95_passed": latency["p95"] is not None and latency["p95"] <= 0.150,
        "p99_passed": latency["p99"] is not None and latency["p99"] <= 0.300,
        "in_flight_slope_per_second": slope,
        "queue_passed": slope <= 0.01,
        "queue_method": "least-squares slope over the middle 80 percent of the arrival window",
    }


async def generator_proof(cluster: LifecycleCluster, image: str) -> dict[str, object]:
    run = await run_cluster_generator(
        cluster,
        image,
        ["http://provider:8000/v1/chat/completions"] * 4,
        rate=1000,
        duration=10,
        output=cluster.output / "generator-proof",
        job_name=CLUSTER_GENERATOR_PROOF_JOB,
        model="fixed-one-token",
        bypass_cache=False,
    )
    report = summarize(run, target_rate=1000)
    failures = generator_evidence_failures(run, target_rate=1000)
    if report["success_count"] != run.target_count:
        failures.append("provider_baseline_not_all_successful")
    report["failures"], report["passed"] = failures, not failures
    (cluster.output / "generator-proof.json").write_text(json.dumps(report, indent=2) + "\n")
    if failures:
        raise RuntimeError("Generator capacity proof failed before gateway measurement")
    return report


async def run_stage(
    cluster: LifecycleCluster,
    image: str,
    endpoints: list[str],
    *,
    api_ports: list[int],
    worker_ports: list[int],
    manifest: Path,
    rate: int,
    duration: int,
    phase: str,
) -> dict[str, object]:
    stage = cluster.output / f"{phase}-{rate}rps"
    roles = {
        "api",
        "accounting_request",
        "accounting_worker",
        "load_generator",
        "postgresql",
        "redis",
        "provider",
    }
    async with local_database() as db:
        before = await accounting_snapshot(db)
    cpu_before = await asyncio.to_thread(capture_cpu_counters, cluster)
    resources = KubernetesResourceRecorder(cluster, stage / "resources.jsonl", required_roles=roles)
    report = await measure(
        argparse.Namespace(
            url=f"http://127.0.0.1:{api_ports[0]}/v1/chat/completions",
            metrics_url=[f"http://127.0.0.1:{port}/metrics" for port in api_ports],
            accounting_worker_metrics_url=[
                f"http://127.0.0.1:{port}/metrics" for port in worker_ports
            ],
            accounting_worker_source_roles=[
                "accounting_request",
                "accounting_request",
                "accounting_worker",
            ],
            rate=rate,
            duration=duration,
            label=phase,
            output_dir=stage,
            server_manifest=manifest,
            dependency_diagnostics=True,
            diagnostic_gate=True,
            raise_on_diagnostic_failure=False,
            compress_samples=True,
        ),
        resource_recorder=resources,
        workload_runner=lambda: run_cluster_generator(
            cluster,
            image,
            endpoints,
            rate=rate,
            duration=duration,
            output=stage / "generator",
        ),
    )
    cpu_after = await asyncio.to_thread(capture_cpu_counters, cluster)
    async with local_database() as db:
        drain = await wait_native_drain(db)
        economics = await reconcile_native(
            db,
            before=before,
            successes=report["success_count"],
            all_successful=report["success_count"] == report["target_count"],
        )
    gates = latency_and_queue_gates(report)
    throughput_passed = gates["success_passed"] and not report["diagnostic_failures"]
    report.update(
        phase=phase,
        cpu_throttling={
            "before": cpu_before,
            "after": cpu_after,
            "deltas": cpu_counter_deltas(cpu_before, cpu_after),
            "window": "includes warmup and artifact transfer; no reads inside arrivals",
        },
        request_path="four in-cluster per-pod services; one synchronized generator shard per API process",
        accounting_drain=drain,
        accounting_reconciliation=economics,
        latency_and_queue=gates,
        throughput_passed=throughput_passed,
        economic_passed=drain["passed"] and economics["passed"],
        latency_passed=gates["p95_passed"] and gates["p99_passed"] and gates["queue_passed"],
    )
    report["passed"] = (
        report["throughput_passed"] and report["economic_passed"] and report["latency_passed"]
    )
    (stage / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    cluster.event(
        "qualification_stage_completed",
        phase=phase,
        rate=rate,
        success_count=report["success_count"],
        passed=report["passed"],
    )
    if not economics["safe_budget_state"]:
        raise RuntimeError("Qualification found unsafe economic state; remaining load stopped")
    if not drain["passed"]:
        raise RuntimeError("Accounting did not drain; remaining load stopped")
    return report


async def exercise(
    cluster: LifecycleCluster, values: Path, image: str, *, short_seconds: int
) -> list[dict[str, object]]:
    with ExitStack() as forwards:
        postgres = forwards.enter_context(cluster.forward("service/postgres", 5432))
        redis = forwards.enter_context(cluster.forward("service/redis", 6379))
        database_url = (
            f"postgresql://postgres:fixture-only@127.0.0.1:{postgres}/deltallm_concurrency"
        )
        os.environ.update(
            DATABASE_URL=database_url,
            REDIS_URL=f"redis://127.0.0.1:{redis}/0",
            DELTALLM_LOAD_API_KEY=LOAD_KEY,
            DELTALLM_MASTER_KEY=MASTER_KEY,
            DELTALLM_SALT_KEY=SALT_KEY,
        )
        await activate_accounting(database_url)
        await asyncio.to_thread(capacity_release, cluster, values)
        api = [pod["metadata"]["name"] for pod in api_pods(cluster)]
        request = ready_pods(cluster, REQUEST_SELECTOR)
        projection = ready_pods(cluster, PROJECTION_SELECTOR)
        if len(request) != 2 or len(projection) != 1:
            raise RuntimeError("Qualification requires two request roles and one projection role")
        cluster.event(
            "qualification_topology",
            api_processes=4,
            request_processes=2,
            projection_processes=1,
            api_cpu_limit=2,
            api_memory_mib=1024,
            generator_cpu_limit=4,
            generator_memory_mib=1024,
        )
        endpoints = pin_api_services(cluster, api)
        api_ports = [forwards.enter_context(cluster.forward("pod/" + pod, 4000)) for pod in api]
        worker_ports = [
            forwards.enter_context(cluster.forward("pod/" + pod, 4000))
            for pod in request + projection
        ]
        for port in api_ports + worker_ports:
            await wait_edge(f"http://127.0.0.1:{port}")
        manifest = await server_manifest(cluster, image, api, request + projection, cluster.output)
        vm_cpus = int(cluster.run("docker", "info", "--format", "{{.NCPU}}", timeout=30).stdout)
        declared = read_manifest(manifest).model_copy(
            update={"memory_limit_mib": 1024, "host_cpu_count": vm_cpus}
        )
        manifest.write_text(declared.model_dump_json(indent=2) + "\n")
        proof = await generator_proof(cluster, image)
        async with local_database() as db:
            initial_drain = await wait_native_drain(db)
        if not initial_drain["passed"]:
            raise RuntimeError("Accounting startup did not reach an empty baseline")
        results = []
        for phase, duration in (("short", short_seconds), ("qualification", 600)):
            for rate in RATES:
                result = await run_stage(
                    cluster,
                    image,
                    endpoints,
                    api_ports=api_ports,
                    worker_ports=worker_ports,
                    manifest=manifest,
                    rate=rate,
                    duration=duration,
                    phase=phase,
                )
                results.append(result)
                (cluster.output / "results.json").write_text(
                    json.dumps({"generator_proof": proof, "runs": results}, indent=2) + "\n"
                )
            if phase == "short" and any(not result["passed"] for result in results):
                raise RuntimeError(
                    "Short qualification ladder failed; ten-minute series was not started"
                )
        async with local_database() as db:
            storage_before = await native_storage_snapshot(db)
        await asyncio.to_thread(
            cluster.kubectl,
            "exec",
            "deployment/postgres",
            "--",
            "env",
            "PGOPTIONS=-c statement_timeout=30000 -c lock_timeout=2000",
            "psql",
            "-U",
            "postgres",
            "-d",
            "deltallm_concurrency",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "VACUUM (ANALYZE) deltallm_accounting_events,deltallm_accounting_terminal_journal,"
            "deltallm_accounting_usage_facts_v2,deltallm_accounting_usage_rollups_v2,"
            "deltallm_billing_operations,deltallm_accounting_grants",
            timeout=40,
        )
        async with local_database() as db:
            storage_after = await native_storage_snapshot(db)
        (cluster.output / "storage-maintenance.json").write_text(
            json.dumps(
                {
                    "before": storage_before,
                    "after": storage_after,
                    "statement_timeout_seconds": 30,
                    "lock_timeout_seconds": 2,
                },
                default=str,
                indent=2,
            )
            + "\n"
        )
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--kind", default="kind")
    parser.add_argument("--short-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.output.exists() or not 10 <= args.short_seconds <= 60:
        parser.error("Use a fresh evidence directory and a short duration from 10 to 60 seconds")
    manifest = candidate_manifest(args.image, allow_dirty=False)
    active_containers = subprocess.check_output(
        ["docker", "ps", "-q"], text=True, timeout=30
    ).strip()
    if active_containers:
        raise RuntimeError(
            "Qualification requires a dedicated Docker environment with no running containers"
        )
    environment = json.loads(
        subprocess.check_output(
            [
                "docker",
                "info",
                "--format",
                '{"cpu_count":{{.NCPU}},"memory_bytes":{{.MemTotal}},"architecture":"{{.Architecture}}","kernel":"{{.KernelVersion}}","docker_version":"{{.ServerVersion}}","cgroup_version":"{{.CgroupVersion}}"}',
            ],
            text=True,
            timeout=30,
        )
    )
    manifest.update(
        purpose="native accounting fixed-image 50/100/200/500 RPS qualification",
        release_eligible=False,
        docker_environment=environment,
    )
    cluster = LifecycleCluster(args.output, kind=args.kind, purpose="native-qualification", nodes=2)
    (cluster.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with cluster.owned(args.image):
        metrics_image = preload_images(cluster)
        install_capacity_dependencies(cluster, args.image)
        install_resource_metrics(cluster, metrics_image)
        values = qualification_values(cluster, args.image)
        prime_database(cluster, values)
        results = asyncio.run(
            exercise(cluster, values, args.image, short_seconds=args.short_seconds)
        )
    manifest["qualification_passed"] = all(result["passed"] for result in results)
    manifest["release_eligible"] = manifest["qualification_passed"]
    (cluster.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if any(not result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
