"""Run the packaged application in a new, disposable Kubernetes cluster.

Run from the repository root with --image naming a locally built release image.
Docker, kind v0.31.0, kubectl, Helm and the frozen dev environment are required.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
from time import monotonic

import httpx
import yaml

from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.gateway_concurrency_fixture import seed
from tests.performance.gateway_concurrency_manifest import local_manifest
from tests.performance.lifecycle_cluster import LifecycleCluster, LOAD_KEY, MASTER_KEY, SALT_KEY
from tests.performance.lifecycle_fixtures import chart_values, install_dependencies, release
from tests.performance.run_gateway_concurrency import measure
from tests.performance.lifecycle_economics import (
    accept,
    assert_backlog,
    claim,
    export_records,
    held_ledger,
    recovered,
    reconcile_interrupted_streams,
)
from tests.performance.lifecycle_batch import batch_rollout
from tests.performance.lifecycle_migrations import concurrent_migrations, failed_migrations
from tests.performance.lifecycle_recovery import readiness_recovery


def api_pods(cluster: LifecycleCluster) -> list[str]:
    payload = json.loads(
        cluster.kubectl(
            "get",
            "pods",
            "-l",
            "app.kubernetes.io/instance=gateway,app.kubernetes.io/component=api",
            "-o",
            "json",
        ).stdout
    )
    return [
        pod["metadata"]["name"]
        for pod in payload["items"]
        if not pod["metadata"].get("deletionTimestamp")
    ]


async def ready(url: str, *, timeout: float = 60) -> dict:
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            try:
                response = await client.get(url + "/health/readiness")
                if response.status_code == 200:
                    return response.json()
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError("application did not become ready")


async def long_stream(cluster: LifecycleCluster, url: str, entered: asyncio.Event) -> dict:
    started = monotonic()
    data = bytearray()
    async with httpx.AsyncClient(timeout=200, trust_env=False) as client:
        try:
            async with client.stream(
                "POST",
                url + "/v1/chat/completions",
                headers={"Authorization": "Bearer " + LOAD_KEY},
                json={
                    "model": "concurrency-fixture",
                    "max_tokens": 1,
                    "stream": True,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "metadata": {"cache": False},
                },
            ) as response:
                assert response.status_code == 200, await response.aread()
                async for chunk in response.aiter_bytes():
                    if not entered.is_set():
                        cluster.event("stream_first_byte", ttft_seconds=monotonic() - started)
                        entered.set()
                    data.extend(chunk)
                    assert len(data) < 65536
        except (httpx.RemoteProtocolError, httpx.ReadError):
            pass
    assert entered.is_set()
    assert b"[DONE]" not in data, "interrupted stream emitted terminal success"
    result = {
        "duration_seconds": monotonic() - started,
        "bytes": len(data),
        "terminal_success": False,
    }
    cluster.event("stream_interrupted", **result)
    return result


async def sample(cluster: LifecycleCluster, ports: list[int], label: str) -> dict:
    manifest = await local_manifest(len(ports))
    image = json.loads(
        cluster.run("docker", "image", "inspect", os.environ["PR8_TEST_IMAGE"]).stdout
    )[0]
    manifest = manifest.model_copy(
        update={"image_digest": image["Id"], "cpu_limit_cores": 2.0, "memory_limit_mib": 2048}
    )
    path = cluster.output / f"{label}-manifest.json"
    path.write_text(manifest.model_dump_json(indent=2) + "\n")
    return await measure(
        argparse.Namespace(
            url=f"http://127.0.0.1:{ports[0]}/v1/chat/completions",
            metrics_url=[f"http://127.0.0.1:{port}/metrics" for port in ports],
            rate=10,
            duration=10,
            label=label,
            output_dir=cluster.output / label,
            server_manifest=path,
        )
    )


async def observe_drain(cluster: LifecycleCluster, url: str, pod: str) -> None:
    started = monotonic()
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        while monotonic() - started < 180:
            response = await client.get(url + "/health/readiness")
            if (
                response.status_code == 503
                and response.json().get("details", {}).get("process", {}).get("state") == "draining"
            ):
                rejected = await client.post(url + "/v1/chat/completions", content=b"invalid body")
                assert rejected.status_code == 503
                assert rejected.json()["error"]["code"] == "gateway_draining"
                cluster.event("drain_observed_and_fresh_work_rejected", pod=pod)
                await asyncio.to_thread(
                    cluster.kubectl,
                    "exec",
                    pod,
                    "--",
                    "python",
                    "-c",
                    "import os,signal; os.kill(1,signal.SIGTERM)",
                    check=False,
                )
                cluster.event("repeated_signal_sent", pod=pod)
                return
            await asyncio.sleep(0.1)
    raise TimeoutError("old pod never withdrew readiness")


async def exercise(cluster: LifecycleCluster, values: Path) -> None:
    with ExitStack() as stack:
        pg_port = stack.enter_context(cluster.forward("service/postgres", 5432))
        redis_port = stack.enter_context(cluster.forward("service/redis", 6379))
        os.environ.update(
            DATABASE_URL=f"postgresql://postgres:fixture-only@127.0.0.1:{pg_port}/deltallm_concurrency",
            REDIS_URL=f"redis://127.0.0.1:{redis_port}/0",
            DELTALLM_LOAD_API_KEY=LOAD_KEY,
            DELTALLM_MASTER_KEY=MASTER_KEY,
            DELTALLM_SALT_KEY=SALT_KEY,
        )
        await seed()
        completed = False
        try:
            await exercise_ready_cluster(cluster, values)
            completed = True
        finally:
            try:
                await export_records(cluster.output / "accepted-records.json")
            except Exception:
                cluster.event("economic_export_failed")
                if completed:
                    raise


async def exercise_ready_cluster(cluster: LifecycleCluster, values: Path) -> None:
    async with local_database() as db:
        await db.execute_raw("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
    await concurrent_migrations(cluster, values)
    cluster.kubectl("rollout", "restart", "deployment", "-l", "app.kubernetes.io/instance=gateway")
    cluster.kubectl(
        "rollout",
        "status",
        "deployment",
        "-l",
        "app.kubernetes.io/instance=gateway",
        "--timeout=180s",
    )
    cluster.event("fixture_loaded")
    profile = Path(cluster.directory.name) / "profile.yaml"
    profile.write_text(yaml.safe_dump(yaml.safe_load(values.read_text())["config"]))
    os.environ["DELTALLM_CONFIG_PATH"] = str(profile)
    pods = api_pods(cluster)
    assert len(pods) == 2
    with ExitStack() as forwards:
        ports = [forwards.enter_context(cluster.forward("pod/" + pod, 4000)) for pod in pods]
        urls = [f"http://127.0.0.1:{port}" for port in ports]
        for url in urls:
            payload = await ready(url)
            assert payload["details"]["process"]["state"] == "serving"
        all_pods = json.loads(
            cluster.kubectl(
                "get", "pods", "-l", "app.kubernetes.io/instance=gateway", "-o", "json"
            ).stdout
        )
        for pod in all_pods["items"]:
            forwards.enter_context(cluster.follow_logs(pod["metadata"]["name"]))
        await readiness_recovery(cluster, urls)
        report = await sample(cluster, ports, "before-rollout")
        assert report["success_count"] == 100, report["error_counts"]
        await failed_migrations(cluster, values)
        for url in urls:
            await ready(url)
        entered = asyncio.Event()
        stream = asyncio.create_task(long_stream(cluster, urls[0], entered))
        draining = None
        try:
            async with asyncio.timeout(10):
                await entered.wait()
            async with held_ledger():
                request_id = await accept(urls[1])
                await claim(request_id)
                cluster.event("rollout_started_with_accepted_backlog")
                draining = asyncio.create_task(observe_drain(cluster, urls[0], pods[0]))
                rollout = asyncio.create_task(
                    asyncio.to_thread(
                        release,
                        cluster,
                        values,
                        "--set",
                        "podAnnotations.lifecycle-test=rollout-1,batchWorker.podAnnotations.lifecycle-test=rollout-1",
                    )
                )
                await asyncio.wait_for(stream, timeout=185)
                await rollout
                await draining
                await assert_backlog(request_id)
                cluster.event("rollout_completed_with_shared_backlog")
            cluster.event("rollout_records_recovered", **await recovered(request_id))
        finally:
            if not stream.done():
                stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
            if draining is not None:
                draining.cancel()
                await asyncio.gather(draining, return_exceptions=True)

    pods = api_pods(cluster)
    with ExitStack() as forwards:
        ports = [forwards.enter_context(cluster.forward("pod/" + pod, 4000)) for pod in pods]
        for port in ports:
            await ready(f"http://127.0.0.1:{port}")
        await batch_rollout(cluster, f"http://127.0.0.1:{ports[0]}")
        report = await sample(cluster, ports, "after-rollout")
        assert report["success_count"] == 100, report["error_counts"]
    await pod_loss(cluster)
    cluster.kubectl(
        "rollout",
        "status",
        "deployment",
        "-l",
        "app.kubernetes.io/instance=gateway",
        "--timeout=120s",
    )
    cluster.event("interrupted_streams_reconciled", **await reconcile_interrupted_streams())
    with cluster.forward("service/provider", 8000) as port:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            events = await client.get(f"http://127.0.0.1:{port}/fixture/stream-events")
            events.raise_for_status()
            (cluster.output / "upstream-closures.json").write_text(events.text + "\n")


async def pod_loss(cluster: LifecycleCluster) -> None:
    payload = json.loads(
        cluster.kubectl(
            "get", "pods", "-l", "app.kubernetes.io/instance=gateway", "-o", "json"
        ).stdout
    )
    pods = [
        pod["metadata"]["name"]
        for pod in payload["items"]
        if pod["metadata"]["labels"].get("app.kubernetes.io/component") in {"api", "batch-worker"}
        and not pod["metadata"].get("deletionTimestamp")
    ]
    assert len(pods) == 3
    with ExitStack() as forwards:
        ports = [forwards.enter_context(cluster.forward("pod/" + pod, 4000)) for pod in pods]
        urls = [f"http://127.0.0.1:{port}" for port in ports]
        entered = [asyncio.Event() for _ in pods]
        streams = [
            asyncio.create_task(long_stream(cluster, url, event))
            for url, event in zip(urls, entered)
        ]
        try:
            async with asyncio.timeout(15):
                await asyncio.gather(*(event.wait() for event in entered))
            async with held_ledger():
                request_id = await accept(urls[0])
                owned = await claim(request_id)
                victim = owned["locked_by"].split(":", 1)[0]
                assert victim in pods, owned
                cluster.event("pod_killed_with_owned_claim", pod=victim, event_id=owned["event_id"])
                killed_id = await asyncio.to_thread(cluster.kill_container, victim)
                await asyncio.wait_for(streams[pods.index(victim)], timeout=10)
            await observe_killed_container(cluster, victim, killed_id)
            survivor = next(url for pod, url in zip(pods, urls) if pod != victim)
            await ready(survivor)
            await accept(survivor)
            cluster.event("survivor_served_after_pod_loss")
            cluster.event(
                "lost_pod_records_recovered", **await recovered(request_id, old_claim=owned)
            )
        finally:
            for task in streams:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*streams, return_exceptions=True)


async def observe_killed_container(cluster: LifecycleCluster, pod: str, container_id: str) -> None:
    deadline = monotonic() + 30
    while monotonic() < deadline:
        document = json.loads(
            (await asyncio.to_thread(cluster.kubectl, "get", "pod", pod, "-o", "json")).stdout
        )
        statuses = document["status"]["containerStatuses"]
        terminated = statuses[0].get("lastState", {}).get("terminated", {})
        if terminated.get("containerID") == container_id:
            assert terminated["exitCode"] == 137, terminated
            cluster.event("abrupt_container_exit_verified", pod=pod, **terminated)
            return
        await asyncio.sleep(0.2)
    raise TimeoutError("Kubernetes did not report the selected container's SIGKILL exit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--kind", default="kind")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["PR8_TEST_IMAGE"] = args.image
    cluster = LifecycleCluster(args.output, kind=args.kind)
    with cluster.owned(args.image):
        install_dependencies(cluster, args.image)
        values = chart_values(cluster, args.image)
        release(cluster, values)
        cluster.event("fresh_migration_completed")
        asyncio.run(exercise(cluster, values))
        cluster.event("acceptance_completed")


if __name__ == "__main__":
    main()
