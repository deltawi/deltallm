"""Owned Kubernetes PR9 capacity/autoscaling evidence; not a PR10 certificate."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
from time import monotonic
from urllib.parse import urlsplit

import httpx
import yaml

from src.db.repositories import ModelDeploymentRecord, ModelDeploymentRepository
from tests.performance.capacity_fixture import (
    capacity_release,
    capacity_values,
    install_capacity_dependencies,
    install_edge,
)
from tests.performance.lifecycle_fixtures import CHART
from tests.performance.capacity_load import arrival
from tests.performance.capacity_monitoring import install_monitoring
from tests.performance.capacity_samples import (
    api_pods,
    preflight,
    sample,
    wait_pods,
)
from tests.performance.capacity_scaling import exercise_scaling
from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.gateway_concurrency_fixture import seed
from tests.performance.lifecycle_cluster import LifecycleCluster, LOAD_KEY, MASTER_KEY, SALT_KEY
from tests.performance.lifecycle_economics import (
    accept,
    assert_backlog,
    export_records,
    held_ledger,
    recovered,
)


async def seed_models(values: Path) -> None:
    model = yaml.safe_load(values.read_text())["config"]["model_list"][0]
    async with local_database() as db:
        await ModelDeploymentRepository(db).create(
            ModelDeploymentRecord(
                deployment_id=model["deployment_id"],
                model_name=model["model_name"],
                deltallm_params=model["deltallm_params"],
                model_info=model["model_info"],
            )
        )


async def wait_edge(url: str) -> None:
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        async with asyncio.timeout(120):
            while True:
                response = await client.get(url + "/health/readiness")
                if response.status_code == 200:
                    return
                await asyncio.sleep(2)


async def check_edge_bounds(cluster: LifecycleCluster, url: str) -> None:
    """Exercise public metrics denial and the slow-header connection bound."""
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.get(url + "/metrics")
        assert response.status_code == 404
    address = urlsplit(url)
    reader, writer = await asyncio.open_connection(address.hostname, address.port)
    started = monotonic()
    try:
        writer.write(b"GET /health/readiness HTTP/1.1\r\nHost: fixture\r\n")
        await writer.drain()
        result = await asyncio.wait_for(reader.read(256), timeout=8)
    finally:
        writer.close()
        await writer.wait_closed()
    seconds = monotonic() - started
    assert result.startswith(b"HTTP/1.1 408"), result[:80]
    assert seconds < 8
    cluster.event("edge_slow_header_closed", close_seconds=seconds, metrics_status=404)


def body_bytes(snapshot: dict) -> dict[str, float]:
    return {
        pod["pod"]: sum(
            metric["value"]
            for metric in pod["metrics"]
            if metric["name"] == "deltallm_http_request_body_bytes_total"
        )
        for pod in snapshot["pods"]
    }


async def fixed_pods(cluster: LifecycleCluster, values: Path, url: str, replicas: int) -> None:
    await asyncio.to_thread(capacity_release, cluster, values, "--set", f"replicaCount={replicas}")
    await wait_pods(cluster, replicas)
    await wait_edge(url)
    report = await preflight(cluster)
    before = await sample(cluster, f"fixed-{replicas}-before")
    summary = await arrival(url, cluster.output / f"fixed-{replicas}")
    after = await sample(cluster, f"fixed-{replicas}-after")
    manifest = json.loads((cluster.output / "manifest.json").read_text())
    report_sha = hashlib.sha256(report.model_dump_json(by_alias=True).encode()).hexdigest()
    assert all(pod["image_id"] == manifest["candidate_image_id"] for pod in after["pods"])
    assert all(pod["process"]["contract_sha256"] == report_sha for pod in after["pods"])
    initial, final = body_bytes(before), body_bytes(after)
    assert set(initial) == set(final)
    assert all(final[pod] > initial[pod] for pod in initial), "Traffic did not reach every API pod"
    cluster.event("fixed_pods_completed", replicas=replicas, summary=summary)


async def pod_loss(cluster: LifecycleCluster, url: str) -> None:
    async with held_ledger():
        request_id = await accept(url)
        await assert_backlog(request_id)
        pod = (await asyncio.to_thread(api_pods, cluster))[0]["metadata"]["name"]
        # An actual container death, independent of graceful lifecycle behavior.
        await asyncio.to_thread(cluster.kill_container, pod)
        cluster.event("capacity_pod_killed", pod=pod)
    evidence = await recovered(request_id)
    await wait_pods(cluster, 2)
    await wait_edge(url)
    await arrival(url, cluster.output / "after-pod-loss", rate=5, duration=10)
    cluster.event("capacity_pod_loss_recovered", evidence=evidence)


async def exercise(cluster: LifecycleCluster, values: Path, image: str, baseline: str) -> None:
    with ExitStack() as forwards:
        pg = forwards.enter_context(cluster.forward("service/postgres", 5432))
        redis = forwards.enter_context(cluster.forward("service/redis", 6379))
        edge = forwards.enter_context(cluster.forward("service/capacity-edge", 8080))
        os.environ.update(
            DATABASE_URL=f"postgresql://postgres:fixture-only@127.0.0.1:{pg}/deltallm_concurrency",
            REDIS_URL=f"redis://127.0.0.1:{redis}/0",
            DELTALLM_LOAD_API_KEY=LOAD_KEY,
            DELTALLM_MASTER_KEY=MASTER_KEY,
            DELTALLM_SALT_KEY=SALT_KEY,
        )
        url = f"http://127.0.0.1:{edge}"
        await wait_edge(url)
        await check_edge_bounds(cluster, url)
        await sample(cluster, "baseline-before", observe_processes=False)
        baseline_summary = await arrival(
            url,
            cluster.output / "baseline",
            allow_controlled_rejections=True,
        )
        await sample(cluster, "baseline-after", observe_processes=False)
        cluster.event("baseline_completed", image=baseline, summary=baseline_summary)
        candidate_values = capacity_values(cluster, image)
        completed = False
        try:
            for replicas in (2, 3, 4):
                await fixed_pods(cluster, candidate_values, url, replicas)
            await exercise_scaling(cluster, candidate_values, url)
            await pod_loss(cluster, url)
            completed = True
        finally:
            try:
                await export_records(cluster.output / "accepted-records.json")
            except Exception:
                cluster.event("capacity_economic_export_failed")
                if completed:
                    raise


def prime_database(cluster: LifecycleCluster, values: Path) -> None:
    """Migrate and seed db_only routing before creating any API deployment."""
    rendered = cluster.helm(
        "template",
        "gateway",
        str(CHART),
        "-f",
        str(CHART / "values-production.yaml"),
        "-f",
        str(CHART / "values-capacity-experiment.yaml"),
        "-f",
        str(CHART / "values-capacity-fixture.yaml"),
        "-f",
        str(values),
    ).stdout
    job = next(doc for doc in yaml.safe_load_all(rendered) if doc and doc["kind"] == "Job")
    job["metadata"]["name"] = "capacity-prime-migrate"
    job["metadata"].pop("annotations", None)
    cluster.apply([job])
    cluster.kubectl(
        "wait", "job/capacity-prime-migrate", "--for=condition=complete", "--timeout=180s"
    )
    with ExitStack() as forwards:
        pg = forwards.enter_context(cluster.forward("service/postgres", 5432))
        redis = forwards.enter_context(cluster.forward("service/redis", 6379))
        os.environ.update(
            DATABASE_URL=f"postgresql://postgres:fixture-only@127.0.0.1:{pg}/deltallm_concurrency",
            REDIS_URL=f"redis://127.0.0.1:{redis}/0",
            DELTALLM_LOAD_API_KEY=LOAD_KEY,
            DELTALLM_MASTER_KEY=MASTER_KEY,
            DELTALLM_SALT_KEY=SALT_KEY,
        )
        asyncio.run(seed())
        asyncio.run(seed_models(values))
    cluster.event("capacity_db_only_catalog_primed")


def source_manifest(image: str, baseline_manifest: Path) -> dict:
    digest = hashlib.sha256()
    for path in sorted(Path("src").rglob("*.py")) + [Path("uv.lock")]:
        digest.update(str(path).encode() + b"\0" + path.read_bytes())
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
    ).strip()
    return {
        "candidate_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "candidate_source_sha256": digest.hexdigest(),
        "candidate_image_id": image_id,
        "baseline": json.loads(baseline_manifest.read_text()),
        "purpose": "PR9 functional capacity/autoscaling; PR10 qualification pending",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--baseline-image", required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--kind", default="kind")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cluster = LifecycleCluster(args.output, kind=args.kind, purpose="pr9-capacity", nodes=2)
    (cluster.output / "manifest.json").write_text(
        json.dumps(source_manifest(args.image, args.baseline_manifest), indent=2) + "\n"
    )
    with cluster.owned(args.image):
        cluster.run(
            args.kind,
            "load",
            "docker-image",
            args.baseline_image,
            "--name",
            cluster.name,
            timeout=600,
        )
        install_capacity_dependencies(cluster, args.image)
        install_monitoring(cluster)
        values = capacity_values(cluster, args.baseline_image)
        prime_database(cluster, values)
        capacity_release(cluster, values)
        install_edge(cluster)
        asyncio.run(exercise(cluster, values, args.image, args.baseline_image))
        cluster.event("capacity_acceptance_completed")


if __name__ == "__main__":
    main()
