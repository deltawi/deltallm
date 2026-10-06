"""Owned-cluster accounting-v2 causal diagnostic with complete bounded evidence."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
from time import perf_counter

import httpx
from prisma import Prisma
import yaml

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from scripts.prepare_accounting_v2 import prepare
from scripts.prepare_capacity_adapter import MonitoringDependencies
from tests.performance.capacity_fixture import (
    capacity_release,
    capacity_values,
    install_capacity_dependencies,
    install_direct_api,
)
from tests.performance.capacity_monitoring import install_resource_metrics
from tests.performance.capacity_samples import api_pods
from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.gateway_concurrency_diagnostics import KubernetesResourceRecorder
from tests.performance.gateway_concurrency_manifest import local_manifest
from tests.performance.gateway_source_identity import image_identity_program, source_sha256
from tests.performance.lifecycle_cluster import LifecycleCluster, LOAD_KEY, MASTER_KEY, SALT_KEY
from tests.performance.run_capacity_acceptance import prime_database, wait_edge
from tests.performance.run_gateway_concurrency import measure, valid_completion

ACCOUNTING_SELECTOR = (
    "app.kubernetes.io/instance=gateway,app.kubernetes.io/component=accounting-worker"
)


def image_source_sha256(cluster: LifecycleCluster, pod: str) -> str:
    program = image_identity_program()
    value = cluster.kubectl("exec", pod, "--", "python", "-c", program).stdout.strip()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("Application pod returned an invalid source fingerprint")
    return value


def accounting_values(
    cluster: LifecycleCluster,
    image: str,
    *,
    api_replicas: int,
) -> Path:
    path = capacity_values(cluster, image)
    values = yaml.safe_load(path.read_text())
    general = values["config"]["general_settings"]
    general.update(
        accounting_protocol_enabled=True,
        accounting_protocol_generation=1,
        accounting_microbatch_max_size=8,
        accounting_microbatch_dwell_ms=2,
        accounting_reservation_max_pending=4096,
        accounting_finalization_max_pending=8192,
        accounting_statement_timeout_ms=250,
        accounting_reservation_ack_timeout_ms=1000,
        accounting_finalization_ack_timeout_ms=2000,
        accounting_max_provider_attempts=3,
        accounting_grants_enabled=True,
        accounting_grant_target_operations=32,
        accounting_grant_ttl_seconds=30,
        accounting_projection_batch_size=64,
        accounting_projection_max_concurrent_partitions=4,
        accounting_projection_lease_seconds=30,
        accounting_projection_poll_interval_ms=50,
        accounting_projection_maintenance_interval_ms=1000,
        spend_operation_intents_enabled=False,
    )
    values.update(
        replicaCount=api_replicas,
        accountingWorker={
            "enabled": True,
            "replicaCount": 1,
            "autoscaling": {"enabled": False},
            "resources": {
                "requests": {"cpu": "500m", "memory": "1Gi"},
                "limits": {"cpu": "2", "memory": "2Gi"},
            },
        },
    )
    path.write_text(yaml.safe_dump(values))
    (cluster.output / "fixture-values.yaml").write_text(path.read_text())
    profile = Path(cluster.directory.name) / "accounting-profile.yaml"
    profile.write_text(yaml.safe_dump(values["config"]))
    os.environ["DELTALLM_CONFIG_PATH"] = str(profile)
    return path


def ready_pods(cluster: LifecycleCluster, selector: str) -> list[str]:
    payload = json.loads(cluster.kubectl("get", "pods", "-l", selector, "-o", "json").stdout)
    result = []
    for pod in payload["items"]:
        if pod["metadata"].get("deletionTimestamp"):
            continue
        if any(
            condition["type"] == "Ready" and condition["status"] == "True"
            for condition in pod["status"].get("conditions", [])
        ):
            result.append(pod["metadata"]["name"])
    return sorted(result)


async def activate_accounting(database_url: str) -> None:
    async with local_database() as database:
        await database.execute_raw("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
    await prepare(
        argparse.Namespace(
            database_url=database_url,
            generation=1,
            partitions=64,
            max_outstanding_per_partition=4096,
            activate=True,
        )
    )


async def accounting_state(database: Prisma) -> dict[str, int]:
    rows = await database.query_raw("""
        SELECT
          (SELECT count(*)::bigint FROM deltallm_accounting_events) AS accounting_events,
          (SELECT count(*)::bigint FROM deltallm_billing_operations
             WHERE accounting_generation=1
               AND accounting_state IN ('reserved','provisional')) AS unsettled_operations,
          (SELECT count(*)::bigint FROM deltallm_accounting_grants
             WHERE generation=1 AND state<>'closed') AS open_grants,
          (SELECT count(*)::bigint
             FROM deltallm_accounting_projection_checkpoints checkpoint
             JOIN deltallm_accounting_events event
               ON event.protocol_name=checkpoint.protocol_name
              AND event.generation=checkpoint.generation
              AND event.accounting_partition=checkpoint.accounting_partition
              AND event.sequence>checkpoint.last_sequence
              AND event.event_type IN ('finalized','reconciled')
            WHERE checkpoint.projection_name='legacy-spend-audit-v1'
              AND checkpoint.generation=1) AS projection_pending,
          (SELECT count(*)::bigint FROM deltallm_spend_ingestion_outbox
             WHERE status IN ('queued','retry','processing')) AS spend_pending,
          (SELECT count(*)::bigint FROM deltallm_audit_ingestion_outbox
             WHERE status IN ('queued','retry','processing')) AS audit_pending,
          (SELECT count(*)::bigint FROM deltallm_accounting_budget_windows
             WHERE committed_exact+reserved_exact+provisional_exact>limit_exact)
             AS overspent_windows
    """)
    if len(rows) != 1:
        raise RuntimeError("Accounting state query returned an invalid result")
    return {key: int(value) for key, value in rows[0].items()}


async def wait_accounting_drain(database: Prisma, *, timeout: float = 120) -> dict[str, object]:
    started = perf_counter()
    samples: list[dict[str, object]] = []
    while True:
        state = await accounting_state(database)
        samples.append({"offset_seconds": perf_counter() - started, **state})
        if all(
            state[key] == 0
            for key in (
                "unsettled_operations",
                "open_grants",
                "projection_pending",
                "spend_pending",
                "audit_pending",
                "overspent_windows",
            )
        ):
            return {
                "passed": True,
                "drain_seconds": perf_counter() - started,
                "samples": samples,
            }
        if perf_counter() - started >= timeout:
            return {"passed": False, "drain_seconds": timeout, "samples": samples}
        await asyncio.sleep(2)


async def preload_history(
    url: str,
    database: Prisma,
    output: Path,
    *,
    target_events: int,
) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    initial = (await accounting_state(database))["accounting_events"]
    batches: list[dict[str, object]] = []
    async with httpx.AsyncClient(
        timeout=10,
        limits=httpx.Limits(max_connections=1000, max_keepalive_connections=100),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        for batch in range(8):
            current = (await accounting_state(database))["accounting_events"]
            remaining = target_events - (current - initial)
            if remaining <= 0:
                break
            requests = min(10_000, max(1, (remaining + 1) // 2))

            async def request(index: int, request_id: str) -> RequestResult:
                del index
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {LOAD_KEY}", "x-request-id": request_id},
                    json={
                        "model": "concurrency-fixture",
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        "max_tokens": 1,
                        "stream": False,
                        "metadata": {"cache": False},
                    },
                )
                try:
                    payload = response.json() if len(response.content) <= 65536 else None
                except ValueError:
                    payload = None
                error = None
                if response.status_code != 200:
                    error = "preload_http_error"
                elif not valid_completion(payload):
                    error = "preload_invalid_response"
                return RequestResult(
                    response.status_code, error=error, bytes_received=len(response.content)
                )

            run = await run_constant_arrival(
                rate=200,
                duration_seconds=requests / 200,
                max_in_flight=1000,
                request=request,
            )
            summary = summarize(run, target_rate=200)
            write_results(run, summary, output / f"batch-{batch + 1}")
            if run.generator_dropped_count or any(
                sample.error is not None for sample in run.samples
            ):
                raise RuntimeError("History preload failed before reaching the event target")
            batches.append(summary)
        else:
            raise RuntimeError("History preload exceeded its bounded batch count")
    actual = (await accounting_state(database))["accounting_events"] - initial
    if actual < target_events:
        raise RuntimeError("History preload did not reach its accounting event target")
    drain = await wait_accounting_drain(database)
    result = {
        "target_events": target_events,
        "actual_events": actual,
        "batches": batches,
        "drain": drain,
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    if not drain["passed"]:
        raise RuntimeError("Preloaded accounting state did not drain")
    return result


async def server_manifest(
    cluster: LifecycleCluster,
    image: str,
    api: list[str],
    workers: list[str],
    output: Path,
) -> Path:
    manifest = await local_manifest(len(api), len(workers))
    image_info = json.loads(cluster.run("docker", "image", "inspect", image).stdout)[0]
    server_python = cluster.kubectl(
        "exec", api[0], "--", "python", "-c", "import platform;print(platform.python_version())"
    ).stdout.strip()
    local_source = source_sha256()
    image_source = image_source_sha256(cluster, api[0])
    if image_source != local_source:
        raise RuntimeError("Candidate checkout and application image source fingerprints differ")
    manifest = manifest.model_copy(
        update={
            "server_python": server_python,
            "server_source_sha256": image_source,
            "image_digest": image_info["Id"],
            "cpu_limit_cores": 2.0,
            "memory_limit_mib": 4096,
            "postgres_cpu_limit_cores": 2.0,
            "postgres_memory_limit_mib": 1024,
            "redis_cpu_limit_cores": 2.0,
            "redis_memory_limit_mib": 1024,
        }
    )
    path = output / "server-manifest.json"
    path.write_text(manifest.model_dump_json(indent=2) + "\n")
    return path


async def exercise(
    cluster: LifecycleCluster,
    values: Path,
    image: str,
    *,
    api_replicas: int,
    rate: float,
    duration: float,
    projection: str,
    preload_events: int,
    edge_host_port: int,
) -> dict[str, object]:
    with ExitStack() as forwards:
        postgres_port = forwards.enter_context(cluster.forward("service/postgres", 5432))
        redis_port = forwards.enter_context(cluster.forward("service/redis", 6379))
        database_url = (
            f"postgresql://postgres:fixture-only@127.0.0.1:{postgres_port}/deltallm_concurrency"
        )
        os.environ.update(
            DATABASE_URL=database_url,
            REDIS_URL=f"redis://127.0.0.1:{redis_port}/0",
            DELTALLM_LOAD_API_KEY=LOAD_KEY,
            DELTALLM_MASTER_KEY=MASTER_KEY,
            DELTALLM_SALT_KEY=SALT_KEY,
        )
        await activate_accounting(database_url)
        capacity_release(cluster, values)
        install_direct_api(cluster, node_port=30080)
        api_url = f"http://127.0.0.1:{edge_host_port}"
        await wait_edge(api_url)

        api = [pod["metadata"]["name"] for pod in api_pods(cluster)]
        if len(api) != api_replicas:
            raise RuntimeError("Accounting diagnostic did not reach the declared API replica count")
        workers = ready_pods(cluster, ACCOUNTING_SELECTOR)
        if len(workers) != 1:
            raise RuntimeError("Accounting diagnostic requires exactly one ready accounting worker")
        preload = None
        async with local_database() as database:
            if preload_events:
                preload = await preload_history(
                    api_url + "/v1/chat/completions",
                    database,
                    cluster.output / "preload",
                    target_events=preload_events,
                )
        if projection == "paused":
            cluster.kubectl(
                "scale", "deployment/gateway-deltallm-accounting-worker", "--replicas=0"
            )
            cluster.kubectl(
                "wait",
                "--for=delete",
                "pod",
                "-l",
                ACCOUNTING_SELECTOR,
                "--timeout=120s",
            )
            workers = []

        api_ports = [forwards.enter_context(cluster.forward("pod/" + pod, 4000)) for pod in api]
        worker_ports = [
            forwards.enter_context(cluster.forward("pod/" + pod, 4000)) for pod in workers
        ]
        manifest_path = await server_manifest(cluster, image, api, workers, cluster.output)
        stage = cluster.output / f"{projection}-{int(rate)}rps"
        resources = KubernetesResourceRecorder(
            cluster,
            stage / "resources.jsonl",
            required_roles={"api", "postgresql", "redis"}
            | ({"accounting_worker"} if workers else set()),
        )
        try:
            report = await measure(
                argparse.Namespace(
                    url=api_url + "/v1/chat/completions",
                    metrics_url=[f"http://127.0.0.1:{port}/metrics" for port in api_ports],
                    accounting_worker_metrics_url=[
                        f"http://127.0.0.1:{port}/metrics" for port in worker_ports
                    ],
                    rate=rate,
                    duration=duration,
                    label=projection,
                    output_dir=stage,
                    server_manifest=manifest_path,
                    dependency_diagnostics=True,
                    diagnostic_gate=True,
                    raise_on_diagnostic_failure=False,
                ),
                resource_recorder=resources,
            )
        finally:
            if projection == "paused":
                cluster.kubectl(
                    "scale", "deployment/gateway-deltallm-accounting-worker", "--replicas=1"
                )
                cluster.kubectl(
                    "rollout",
                    "status",
                    "deployment/gateway-deltallm-accounting-worker",
                    "--timeout=180s",
                )
        async with local_database() as database:
            drain = await wait_accounting_drain(database)
        (stage / "accounting-drain.json").write_text(json.dumps(drain, indent=2) + "\n")
        if not drain["passed"]:
            raise RuntimeError("Post-load accounting state did not drain")
        cluster.event(
            "accounting_diagnostic_completed",
            api_replicas=api_replicas,
            projection=projection,
            rate=rate,
            duration=duration,
            success_count=report["success_count"],
            preload_events=preload["actual_events"] if preload is not None else 0,
            accounting_drain_seconds=drain["drain_seconds"],
        )
        return {**report, "preload": preload, "accounting_drain": drain}


def candidate_manifest(image: str, *, allow_dirty: bool) -> dict[str, object]:
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    if dirty and not allow_dirty:
        raise RuntimeError("Diagnostic candidate must be a clean checkout")
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
    ).strip()
    return {
        "candidate_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "candidate_source_sha256": source_sha256(),
        "candidate_image_id": image_id,
        "dirty_checkout": dirty,
        "release_eligible": not dirty,
        "purpose": "accounting-v2 causal performance diagnostic",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", default="kind")
    parser.add_argument("--api-replicas", type=int, choices=(1, 4), default=4)
    parser.add_argument("--rate", type=float, default=100)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--projection", choices=("running", "paused"), default="running")
    parser.add_argument("--preload-events", type=int, default=0)
    parser.add_argument("--allow-dirty-diagnostic", action="store_true")
    args = parser.parse_args()
    if not 5 <= args.duration <= 180:
        parser.error("--duration must be between 5 and 180 seconds for causal diagnostics")
    if not 1 <= args.rate <= 200:
        parser.error("--rate must be between 1 and 200 RPS")
    if args.preload_events and not 30_000 <= args.preload_events <= 100_000:
        parser.error("--preload-events must be zero or between 30000 and 100000")

    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        edge_host_port = port.getsockname()[1]
    cluster = LifecycleCluster(
        args.output,
        kind=args.kind,
        purpose="accounting-diag",
        nodes=2,
        port_mappings={30080: edge_host_port},
    )
    manifest = candidate_manifest(args.image, allow_dirty=args.allow_dirty_diagnostic)
    (cluster.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with cluster.owned(args.image):
        install_capacity_dependencies(cluster, args.image)
        install_resource_metrics(
            cluster,
            MonitoringDependencies.read().metrics_server_image,
        )
        values = accounting_values(
            cluster,
            args.image,
            api_replicas=args.api_replicas,
        )
        prime_database(cluster, values)
        result = asyncio.run(
            exercise(
                cluster,
                values,
                args.image,
                api_replicas=args.api_replicas,
                rate=args.rate,
                duration=args.duration,
                projection=args.projection,
                preload_events=args.preload_events,
                edge_host_port=edge_host_port,
            )
        )
    if result["diagnostic_failures"]:
        raise RuntimeError(f"Diagnostic evidence gate failed; see {result['summary']}")


if __name__ == "__main__":
    main()
