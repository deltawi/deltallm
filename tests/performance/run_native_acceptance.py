"""Capacity and lifecycle acceptance on accounting v2 (values-accounting-native.yaml).

The legacy capacity and lifecycle jobs prove their behavior on the spend
outbox path. This run proves the same promises on the native local_journal
path production runs, on one disposable kind node:

1. Fixed replicas: 2 and then 3 API pods serve strict traffic through a
   bounded edge, every pod takes traffic, and usage facts match exactly.
2. Rollout: a Helm rollout of every role while API traffic and an accepted
   batch are in flight. The batch completes, nothing is charged twice.
3. Pod loss: SIGKILL an API pod, an accounting request pod and the batch pod
   during load. The batch completes, accounting drains without a restart, and
   no work is charged twice.

After each step every bounded in-memory store and queue must return to its
pre-load level, accounting must drain, and budgets must hold no open money.
Batch volume stays below one pod's receipt store; the nightly soak owns volume.
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

from tests.performance.capacity_fixture import capacity_release, install_edge
from scripts.measure_gateway_load import RequestResult, run_constant_arrival, summarize
from tests.performance.capacity_load import PAYLOAD, arrival
from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.gateway_concurrency_fixture import MODEL
from tests.performance.lifecycle_cluster import LOAD_KEY, MASTER_KEY, SALT_KEY, LifecycleCluster
from tests.performance.native_batch_load import (
    CHAT_ENDPOINT,
    budget_scopes,
    client,
    duplicate_operations,
    item_stats,
    verify_outputs,
    wait_jobs,
)
from tests.performance.native_fixture import (
    BATCH_ITEM_LEASE_SECONDS,
    native_values,
    role_pods,
    scrape_roles,
    seed_native_fixture,
)
from tests.performance.native_qualification_economics import accounting_snapshot, wait_native_drain
from tests.performance.run_accounting_diagnostic import activate_accounting
from tests.performance.run_capacity_acceptance import prime_database, wait_edge
from tests.performance.run_native_qualification import (
    collect_native_logs,
    install_native_dependencies,
)
from tests.performance.soak_checks import (
    capacity_failures,
    counter_delta,
    counter_total,
    gauges_returned_to_idle,
    ledger_matches,
)

ROLES = ("api", "batch_worker", "accounting_request", "accounting_worker")
# The mock answers this prompt after 10 s, so a job stays in flight across a
# rollout or pod loss. 300 items at concurrency 20 is about 150 s of work.
SLOW_PROMPT = "Lifecycle batch fixture."
BATCH_ITEMS = 300
SETTLE_SECONDS = 300


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


async def settle(
    cluster: LifecycleCluster,
    *,
    baseline: dict,
    before: dict,
    charged: int,
    allowed_extra: int = 0,
    timeout: float = SETTLE_SECONDS,
) -> dict[str, object]:
    started = monotonic()
    while True:
        idle = gauges_returned_to_idle(baseline, await scrape_roles(cluster, ROLES))
        if idle["passed"] or monotonic() - started >= timeout:
            break
        await asyncio.sleep(5)
    async with local_database() as db:
        drain = await wait_native_drain(db, timeout=timeout)
        after = await accounting_snapshot(db)
        ledger = ledger_matches(
            before,
            after,
            charged=charged,
            duplicates=await duplicate_operations(db),
            scopes=await budget_scopes(db),
            allowed_extra=allowed_extra,
        )
    drain.pop("samples", None)
    result = {
        "passed": idle["passed"] and drain["passed"] and ledger["passed"],
        "idle": idle,
        "accounting_drain": drain,
        "ledger": ledger,
    }
    if not result["passed"]:
        raise AssertionError(
            f"Native state did not settle: {json.dumps(result, default=str)[:4000]}"
        )
    return result


async def start_slow_batch(url: str, prefix: str) -> str:
    rows = [
        {
            "custom_id": f"{prefix}-{index}",
            "method": "POST",
            "url": CHAT_ENDPOINT,
            "body": {
                "model": MODEL,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": SLOW_PROMPT}],
                "metadata": {"cache": False},
            },
        }
        for index in range(BATCH_ITEMS)
    ]
    async with client(url) as http:
        uploaded = await http.post(
            "/v1/files",
            data={"purpose": "batch"},
            files={
                "file": (
                    f"{prefix}.jsonl",
                    "\n".join(json.dumps(row) for row in rows),
                    "application/jsonl",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text[:500]
        created = await http.post(
            "/v1/batches",
            json={
                "input_file_id": uploaded.json()["id"],
                "endpoint": CHAT_ENDPOINT,
                "completion_window": "24h",
            },
        )
        assert created.status_code == 200, created.text[:500]
        batch_id = created.json()["id"]
    async with local_database() as db:
        deadline = monotonic() + 60
        while monotonic() < deadline:
            rows = await db.query_raw(
                "SELECT count(*)::int AS active FROM deltallm_batch_item "
                "WHERE batch_id=$1 AND status='in_progress'",
                batch_id,
            )
            if rows[0]["active"]:
                return batch_id
            await asyncio.sleep(0.5)
    raise TimeoutError("batch worker did not claim the accepted job")


async def finish_batch(url: str, batch_id: str, *, timeout: float) -> dict:
    async with client(url) as http:
        final, elapsed, timed_out = await wait_jobs(http, [batch_id], timeout=timeout)
        document = final[batch_id]
        async with local_database() as db:
            stats = await item_stats(db, [batch_id])
        assert not timed_out, f"batch did not settle in {timeout:.0f} s: {stats}"
        assert document["status"] == "completed", (document, stats)
        assert document["request_counts"]["completed"] == BATCH_ITEMS, (document, stats)
        assert document["request_counts"]["failed"] == 0, (document, stats)
        assert await verify_outputs(http, document) == BATCH_ITEMS
    return {"batch_id": batch_id, "seconds": elapsed, "items": stats}


async def counted_arrival(url: str, *, rate: float, duration: float) -> dict:
    """Chat traffic that records every outcome; a killed pod may fail any request."""
    async with httpx.AsyncClient(
        timeout=10,
        trust_env=False,
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=0),
    ) as http:

        async def request(_index: int, request_id: str) -> RequestResult:
            try:
                response = await http.post(
                    url + "/v1/chat/completions",
                    headers={"Authorization": "Bearer " + LOAD_KEY, "x-request-id": request_id},
                    json=PAYLOAD,
                )
            except httpx.HTTPError as error:
                return RequestResult(status_code=None, error=type(error).__name__)
            return RequestResult(
                status_code=response.status_code, bytes_received=len(response.content)
            )

        result = await run_constant_arrival(
            rate=rate, duration_seconds=duration, max_in_flight=64, request=request
        )
    return summarize(result, target_rate=rate)


def body_bytes(scrape: dict) -> dict[str, float]:
    return {
        pod: value
        for pod, value in counter_total(scrape, "deltallm_http_request_body_bytes_total").items()
        if scrape[pod]["role"] == "api"
    }


async def fixed_replicas(cluster: LifecycleCluster, values: Path, url: str) -> list[dict]:
    results = []
    for replicas in (2, 3):
        await asyncio.to_thread(
            capacity_release, cluster, values, "--set", f"replicaCount={replicas}"
        )
        async with asyncio.timeout(180):
            while len(await asyncio.to_thread(role_pods, cluster, "api")) != replicas:
                await asyncio.sleep(2)
        await wait_edge(url)
        # Let the edge's DNS resolver and health checks see every new pod.
        await asyncio.sleep(5)
        baseline = await scrape_roles(cluster, ROLES)
        async with local_database() as db:
            before = await accounting_snapshot(db)
        summary = await arrival(url, cluster.output / f"fixed-{replicas}")
        after = await scrape_roles(cluster, ROLES)
        grew = counter_delta(body_bytes(baseline), body_bytes(after))
        assert set(grew) == set(body_bytes(after)), f"Traffic did not reach every API pod: {grew}"
        capacity = capacity_failures(baseline, after)
        assert capacity["passed"], capacity
        settled = await settle(
            cluster, baseline=baseline, before=before, charged=summary["success_count"]
        )
        results.append(
            {"replicas": replicas, "arrival": summary, "pods_with_traffic": len(grew), **settled}
        )
        cluster.event("native_fixed_replicas_completed", replicas=replicas)
    return results


async def rollout(cluster: LifecycleCluster, values: Path, url: str) -> dict:
    baseline = await scrape_roles(cluster, ROLES)
    async with local_database() as db:
        before = await accounting_snapshot(db)
    batch_id = await start_slow_batch(url, "native-rollout")
    cluster.event("native_rollout_started_with_batch", batch_id=batch_id)
    rolled = asyncio.create_task(
        asyncio.to_thread(
            capacity_release,
            cluster,
            values,
            "--set",
            "replicaCount=2,podAnnotations.native-acceptance=rollout-1,"
            "batchWorker.podAnnotations.native-acceptance=rollout-1,"
            "accountingRequest.podAnnotations.native-acceptance=rollout-1,"
            "accountingWorker.podAnnotations.native-acceptance=rollout-1",
        )
    )
    traffic = await arrival(
        url,
        cluster.output / "rollout-traffic",
        rate=5,
        duration=60,
        allow_controlled_rejections=True,
    )
    await rolled
    await asyncio.to_thread(
        cluster.kubectl,
        "rollout",
        "status",
        "deployment",
        "-l",
        "app.kubernetes.io/instance=gateway",
        "--timeout=300s",
    )
    await wait_edge(url)
    batch = await finish_batch(url, batch_id, timeout=BATCH_ITEM_LEASE_SECONDS + 360)
    settled = await settle(
        cluster,
        baseline=baseline,
        before=before,
        charged=traffic["success_count"] + BATCH_ITEMS,
    )
    cluster.event("native_rollout_completed", batch_seconds=round(batch["seconds"], 1))
    return {"traffic": traffic, "batch": batch, **settled}


async def pod_loss(cluster: LifecycleCluster, url: str) -> dict:
    baseline = await scrape_roles(cluster, ROLES)
    async with local_database() as db:
        before = await accounting_snapshot(db)
    batch_id = await start_slow_batch(url, "native-pod-loss")

    async def kill_one_of_each() -> list[dict]:
        killed = []
        # Let the batch settle into steady work before the first loss.
        await asyncio.sleep(10)
        for role in ("api", "accounting_request", "batch_worker"):
            pod = (await asyncio.to_thread(role_pods, cluster, role))[0]
            container = await asyncio.to_thread(cluster.kill_container, pod)
            killed.append({"role": role, "pod": pod, "container": container})
            cluster.event("native_pod_killed", role=role, pod=pod)
            await asyncio.sleep(15)
        return killed

    killer = asyncio.create_task(kill_one_of_each())
    traffic = await counted_arrival(url, rate=5, duration=60)
    write_json(cluster.output / "pod-loss-traffic.json", traffic)
    killed = await killer
    async with asyncio.timeout(300):
        while any(
            len(await asyncio.to_thread(role_pods, cluster, role)) != count
            for role, count in (("api", 2), ("accounting_request", 2), ("batch_worker", 1))
        ):
            await asyncio.sleep(2)
    await wait_edge(url)
    batch = await finish_batch(url, batch_id, timeout=BATCH_ITEM_LEASE_SECONDS + 600)
    rejected = traffic["target_count"] - traffic["success_count"]
    settled = await settle(
        cluster,
        baseline=baseline,
        before=before,
        charged=traffic["success_count"] + BATCH_ITEMS,
        # A killed API pod may have charged answers its client never received.
        allowed_extra=rejected,
        timeout=600,
    )
    cluster.event("native_pod_loss_recovered", batch_seconds=round(batch["seconds"], 1))
    return {"killed": killed, "traffic": traffic, "batch": batch, **settled}


async def exercise(cluster: LifecycleCluster, values: Path, report: dict) -> None:
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
        await asyncio.to_thread(install_edge, cluster)
        edge = forwards.enter_context(cluster.forward("service/capacity-edge", 8080))
        url = f"http://127.0.0.1:{edge}"
        await wait_edge(url)
        async with local_database() as db:
            initial = await wait_native_drain(db)
        assert initial["passed"], "Accounting startup did not reach an empty baseline"
        async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
            assert (await http.get(url + "/metrics")).status_code == 404
        report["fixed_replicas"] = await fixed_replicas(cluster, values, url)
        write_json(cluster.output / "native-acceptance.json", report)
        report["rollout"] = await rollout(cluster, values, url)
        write_json(cluster.output / "native-acceptance.json", report)
        report["pod_loss"] = await pod_loss(cluster, url)
        report["passed"] = True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--kind", default="kind")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a fresh evidence directory")
    cluster = LifecycleCluster(args.output, kind=args.kind, purpose="native-acceptance", nodes=1)
    report: dict[str, object] = {"passed": False}
    with cluster.owned(args.image):
        install_native_dependencies(cluster, args.image)
        values = native_values(cluster, args.image, api_replicas=2, batch_replicas=1)
        prime_database(cluster, values, seeder=seed_native_fixture)
        try:
            asyncio.run(exercise(cluster, values, report))
        finally:
            write_json(cluster.output / "native-acceptance.json", report)
            collect_native_logs(cluster)
    cluster.event("native_acceptance_completed")


if __name__ == "__main__":
    main()
