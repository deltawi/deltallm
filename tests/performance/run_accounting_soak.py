"""Nightly accounting v2 soak: mixed batch load, then sustained API load, on kind.

The cluster runs the production configuration (strict production values plus
``values-accounting-native.yaml``) with a fixed mock provider, so provider
limits never blur the result. Defaults are the targets in the robustness plan:
3 batch pods finish 20,000 mixed items in 12 jobs with no failed item at 50
items/s or more, and 2 API pods hold 300 RPS for 30 minutes with p95 under
50 ms and no 5xx. After each phase every bounded in-memory store and queue
must return to its pre-load level within 5 minutes, accounting must drain,
and usage facts must match the successful work exactly once.

Run from the repository root with a locally built image. Docker, kind v0.31.0,
kubectl, Helm and the frozen dev environment are required.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
from time import monotonic

from scripts.measure_gateway_load import RequestResult, run_constant_arrival, summarize
from tests.performance.cluster_load_generator import run_cluster_generator
from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.lifecycle_cluster import LOAD_KEY, MASTER_KEY, SALT_KEY, LifecycleCluster
from tests.performance.native_batch_load import (
    CHAT_ENDPOINT,
    budget_scopes,
    client,
    duplicate_operations,
    item_stats,
    job_plan,
    submit_job,
    verify_outputs,
    wait_jobs,
)
from tests.performance.native_fixture import (
    collect_role_logs,
    EMBEDDING_MODEL,
    native_values,
    pod_restarts,
    restarted_since,
    role_pods,
    scrape_roles,
    seed_native_fixture,
)
from tests.performance.native_qualification_economics import (
    accounting_snapshot,
    drain_or_unsafe,
    wait_native_drain,
)
from tests.performance.run_accounting_diagnostic import activate_accounting
from tests.performance.run_capacity_acceptance import prime_database, wait_edge
from tests.performance.capacity_fixture import capacity_release
from tests.performance.run_gateway_concurrency import generator_evidence_failures
from tests.performance.run_native_qualification import (
    collect_native_logs,
    install_native_dependencies,
)
from tests.performance.soak_checks import (
    api_window,
    batch_outcome,
    capacity_failures,
    counter_by_label,
    counter_delta,
    gauges_returned_to_idle,
    ledger_matches,
)

ROLES = ("api", "batch_worker", "accounting_request", "accounting_worker")
MAX_WINDOW_SECONDS = 600
API_SERVICE = "service/gateway-deltallm"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


async def wait_idle(
    cluster: LifecycleCluster, baseline: dict, *, timeout: float
) -> dict[str, object]:
    """Poll every role until all tracked gauges are back at their pre-load level."""
    started = monotonic()
    while True:
        current = await scrape_roles(cluster, ROLES)
        result = gauges_returned_to_idle(baseline, current)
        result["seconds"] = monotonic() - started
        if result["passed"] or result["seconds"] >= timeout:
            result["timeout_seconds"] = timeout
            return result
        await asyncio.sleep(10)


async def settle(
    cluster: LifecycleCluster,
    *,
    baseline_scrape: dict,
    restarts: dict[str, int],
    before: dict,
    charged: int,
    idle_seconds: float,
) -> dict[str, object]:
    """After a phase: stores idle, accounting drained, facts match the successful work.

    No gateway container may have restarted since the soak began: a restart
    clears the in-memory stores the soak is there to watch.
    """
    idle = await wait_idle(cluster, baseline_scrape, timeout=idle_seconds)
    restarted = restarted_since(restarts, await asyncio.to_thread(pod_restarts, cluster))
    async with local_database() as db:
        drain = await drain_or_unsafe(db, timeout=idle_seconds)
        after = await accounting_snapshot(db)
        ledger = ledger_matches(
            before,
            after,
            charged=charged,
            duplicates=await duplicate_operations(db),
            scopes=await budget_scopes(db),
        )
    drain.pop("samples", None)
    return {
        "passed": idle["passed"] and drain["passed"] and ledger["passed"] and not restarted,
        "idle": idle,
        "accounting_drain": drain,
        "ledger": ledger,
        "restarted_pods": restarted,
    }


async def batch_phase(
    cluster: LifecycleCluster,
    url: str,
    args: argparse.Namespace,
    baseline: dict,
    restarts: dict[str, int],
) -> dict[str, object]:
    async with local_database() as db:
        before = await accounting_snapshot(db)
    plan = job_plan(args.batch_items, args.batch_jobs)
    cluster.event("soak_batch_started", jobs=len(plan), items=args.batch_items)
    async with client(url) as http:
        started = monotonic()
        create_retries: list[dict] = []
        batch_ids = list(
            await asyncio.gather(
                *(
                    submit_job(http, endpoint, f"soak-{index}", count, retries=create_retries)
                    for index, (endpoint, count) in enumerate(plan)
                )
            )
        )
        submitted = monotonic() - started
        final, _, timed_out = await wait_jobs(http, batch_ids, timeout=args.batch_timeout)
        elapsed = monotonic() - started
        verified = 0
        for document in final.values():
            if document["status"] == "completed":
                verified += await verify_outputs(http, document)
    scrape = await scrape_roles(cluster, ROLES)
    async with local_database() as db:
        stats = await item_stats(db, batch_ids)
    outcome = batch_outcome(
        final.values(),
        expected_items=args.batch_items,
        elapsed_seconds=elapsed,
        min_items_per_second=args.min_batch_items_per_second,
    )
    if timed_out:
        outcome["failures"].append("timed_out")
        outcome["passed"] = False
    capacity = capacity_failures(baseline, scrape)
    retries = counter_delta(
        counter_by_label(baseline, "deltallm_batch_item_retries_total", "category"),
        counter_by_label(scrape, "deltallm_batch_item_retries_total", "category"),
    )
    cluster.event(
        "soak_batch_finished",
        completed=outcome["completed_items"],
        failed=outcome["failed_items"],
        items_per_second=round(outcome["items_per_second"], 1),
    )
    settled = await settle(
        cluster,
        baseline_scrape=baseline,
        restarts=restarts,
        before=before,
        charged=outcome["completed_items"],
        idle_seconds=args.idle_seconds,
    )
    report = {
        "plan": [{"endpoint": endpoint, "items": count} for endpoint, count in plan],
        "submit_seconds": submitted,
        # Busy answers to concurrent uploads and creates, retried like a client.
        "create_retries": create_retries,
        "outcome": outcome,
        "verified_output_rows": verified,
        "items": stats,
        "accounting_capacity_refusals": capacity,
        "batch_retries_by_category": retries,
        "jobs": [
            {
                key: document.get(key)
                for key in (
                    "id",
                    "endpoint",
                    "status",
                    "request_counts",
                    "observed_terminal_seconds",
                )
            }
            for document in final.values()
        ],
        "after_load": settled,
    }
    report["passed"] = (
        outcome["passed"]
        and verified == outcome["completed_items"]
        and capacity["passed"]
        and settled["passed"]
    )
    return report


async def probe_phase(
    cluster: LifecycleCluster, url: str, args: argparse.Namespace, restarts: dict[str, int]
) -> dict[str, object]:
    """A fresh job after the soak must finish promptly: no state needs a pod restart."""
    async with local_database() as db:
        before = await accounting_snapshot(db)
    baseline = await scrape_roles(cluster, ROLES)
    async with client(url) as http:
        started = monotonic()
        batch_id = await submit_job(http, CHAT_ENDPOINT, "soak-probe", args.probe_items)
        final, elapsed, timed_out = await wait_jobs(http, [batch_id], timeout=args.probe_seconds)
    # A job never read back counts as not completed rather than aborting the run.
    document = final.get(batch_id) or {
        "status": "unreadable",
        "request_counts": {"completed": 0, "failed": 0},
    }
    outcome = batch_outcome(
        [document],
        expected_items=args.probe_items,
        elapsed_seconds=monotonic() - started,
        min_items_per_second=0,
    )
    if timed_out:
        outcome["failures"].append("timed_out")
        outcome["passed"] = False
    settled = await settle(
        cluster,
        baseline_scrape=baseline,
        restarts=restarts,
        before=before,
        charged=outcome["completed_items"],
        idle_seconds=args.idle_seconds,
    )
    cluster.event("soak_probe_finished", passed=outcome["passed"], probe_seconds=round(elapsed, 1))
    return {
        "outcome": outcome,
        "timeout_seconds": args.probe_seconds,
        "after_load": settled,
        "passed": outcome["passed"] and settled["passed"],
    }


def pin_api_services(cluster: LifecycleCluster, pods: list[str]) -> list[str]:
    """One service per API pod, so each generator shard loads one pod."""
    documents, endpoints = [], []
    for index, pod in enumerate(sorted(pods)):
        cluster.kubectl("label", "pod", pod, f"soak.deltallm.ai/index={index}", "--overwrite")
        name = f"soak-api-{index}"
        documents.append(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name},
                "spec": {
                    "selector": {"soak.deltallm.ai/index": str(index)},
                    "ports": [{"port": 4000, "targetPort": "http"}],
                },
            }
        )
        endpoints.append(f"http://{name}:4000/v1/chat/completions")
    cluster.apply(documents)
    return endpoints


async def embedding_arrival(url: str, *, rate: float, duration: float) -> dict[str, object]:
    """A steady embeddings stream beside the chat generator, from the runner."""
    async with client(url, timeout=10) as http:

        async def request(_index: int, request_id: str) -> RequestResult:
            response = await http.post(
                "/v1/embeddings",
                headers={"x-request-id": request_id},
                json={"model": EMBEDDING_MODEL, "input": "soak embedding " + request_id},
            )
            return RequestResult(
                status_code=response.status_code, bytes_received=len(response.content)
            )

        result = await run_constant_arrival(
            rate=rate, duration_seconds=duration, max_in_flight=64, request=request
        )
    return summarize(result, target_rate=rate)


async def api_phase(
    cluster: LifecycleCluster,
    url: str,
    image: str,
    args: argparse.Namespace,
    restarts: dict[str, int],
) -> dict[str, object]:
    async with local_database() as db:
        before = await accounting_snapshot(db)
    baseline = await scrape_roles(cluster, ROLES)
    endpoints = await asyncio.to_thread(
        pin_api_services, cluster, await asyncio.to_thread(role_pods, cluster, "api")
    )
    windows, charged, remaining = [], 0, args.api_seconds
    index = 0
    cluster.event("soak_api_started", rate=args.api_rate, duration=args.api_seconds)
    while remaining > 0:
        duration = min(MAX_WINDOW_SECONDS, remaining)
        remaining -= duration
        stage = cluster.output / "api" / f"window-{index}"
        chat_task = asyncio.create_task(
            run_cluster_generator(
                cluster,
                image,
                endpoints,
                rate=args.api_rate,
                duration=duration,
                output=stage / "chat",
            )
        )
        embeddings = (
            await embedding_arrival(url, rate=args.api_embedding_rate, duration=duration)
            if args.api_embedding_rate > 0
            else None
        )
        run = await chat_task
        chat = summarize(run, target_rate=args.api_rate)
        chat_gate = api_window(chat, max_p95_seconds=args.max_p95_seconds)
        generator = generator_evidence_failures(run, target_rate=args.api_rate)
        if generator:
            chat_gate["failures"].append("load_generator_fell_behind")
            chat_gate["passed"] = False
        embedding_gate = (
            api_window(embeddings, max_p95_seconds=args.max_embedding_p95_seconds)
            if embeddings is not None
            else {"passed": True, "skipped": True}
        )
        charged += chat["success_count"] + (embeddings["success_count"] if embeddings else 0)
        window = {
            "index": index,
            "duration_seconds": duration,
            "chat": {
                key: chat[key]
                for key in (
                    "target_count",
                    "success_count",
                    "status_counts",
                    "error_counts",
                    "latency_seconds",
                    "client_started_rps",
                )
            },
            "chat_gate": chat_gate,
            "generator_failures": generator,
            "embeddings": None
            if embeddings is None
            else {
                key: embeddings[key]
                for key in (
                    "target_count",
                    "success_count",
                    "status_counts",
                    "error_counts",
                    "latency_seconds",
                )
            },
            "embedding_gate": embedding_gate,
            "passed": chat_gate["passed"] and embedding_gate["passed"],
        }
        write_json(stage / "window.json", window)
        windows.append(window)
        cluster.event("soak_api_window_finished", index=index, passed=window["passed"])
        index += 1
    scrape = await scrape_roles(cluster, ROLES)
    capacity = capacity_failures(baseline, scrape)
    settled = await settle(
        cluster,
        baseline_scrape=baseline,
        restarts=restarts,
        before=before,
        charged=charged,
        idle_seconds=args.idle_seconds,
    )
    return {
        "rate_rps": args.api_rate,
        "embedding_rate_rps": args.api_embedding_rate,
        "seconds": args.api_seconds,
        "windows": windows,
        "accounting_capacity_refusals": capacity,
        "after_load": settled,
        "passed": all(window["passed"] for window in windows)
        and capacity["passed"]
        and settled["passed"],
    }


async def exercise(
    cluster: LifecycleCluster,
    values: Path,
    image: str,
    args: argparse.Namespace,
    report: dict[str, object],
) -> None:
    """Fill ``report`` phase by phase, so a failure keeps what already ran."""
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
        topology = {role: len(await asyncio.to_thread(role_pods, cluster, role)) for role in ROLES}
        cluster.event("soak_topology", **topology)
        expected = {"api": 2, "batch_worker": 3, "accounting_request": 2, "accounting_worker": 1}
        if topology != expected:
            raise RuntimeError(f"Soak topology {topology} does not match {expected}")
        api = forwards.enter_context(cluster.forward(API_SERVICE, 4000))
        url = f"http://127.0.0.1:{api}"
        await wait_edge(url)
        async with local_database() as db:
            initial = await wait_native_drain(db)
        if not initial["passed"]:
            raise RuntimeError("Accounting startup did not reach an empty baseline")
        baseline = await scrape_roles(cluster, ROLES)
        restarts = await asyncio.to_thread(pod_restarts, cluster)
        write_json(
            cluster.output / "baseline-gauges.json",
            {pod: entry["role"] for pod, entry in baseline.items()},
        )
        report["topology"] = topology
        if args.batch_items:
            report["batch"] = await batch_phase(cluster, url, args, baseline, restarts)
            write_json(cluster.output / "soak-report.json", report)
            report["probe"] = await probe_phase(cluster, url, args, restarts)
            write_json(cluster.output / "soak-report.json", report)
        if args.api_seconds:
            report["api"] = await api_phase(cluster, url, image, args, restarts)
        restarted = restarted_since(restarts, await asyncio.to_thread(pod_restarts, cluster))
        report["restarts"] = {"passed": not restarted, "restarted_pods": restarted}
        report["passed"] = all(
            section["passed"]
            for name, section in report.items()
            if isinstance(section, dict) and name != "topology"
        )


def refusal_detail(refusals: dict) -> str:
    if refusals["passed"]:
        return ""
    total = sum(refusals["capacity_refusals"].values())
    return f"full-store refusals during the phase: {total:,.0f}"


def markdown(report: dict, args: argparse.Namespace) -> str:
    def mark(passed: bool) -> str:
        return "pass" if passed else "**FAIL**"

    lines = ["## Accounting v2 soak", "", "| Check | Result | Detail |", "|---|---|---|"]
    batch = report.get("batch")
    if batch:
        outcome = batch["outcome"]
        settled = batch["after_load"]
        lines += [
            f"| Batch: {args.batch_jobs} jobs, {args.batch_items:,} items | {mark(outcome['passed'])} | "
            f"{outcome['completed_items']:,} done, {outcome['failed_items']:,} failed, "
            f"{outcome['items_per_second']:.1f} items/s (target {args.min_batch_items_per_second}) "
            f"{' '.join(outcome['failures'])} |",
            f"| Batch: create retries (busy, retried) | info | "
            f"{len(batch.get('create_retries', []))} |",
            f"| Batch: no full-store refusals | {mark(batch['accounting_capacity_refusals']['passed'])} | "
            f"{batch['accounting_capacity_refusals']['capacity_refusals'] or 'none'} |",
            f"| Batch: stores idle after load | {mark(settled['idle']['passed'])} | "
            f"{len(settled['idle']['above_baseline'])} gauges above baseline after {settled['idle']['seconds']:.0f} s |",
            f"| Batch: accounting drained, charged once | {mark(settled['accounting_drain']['passed'] and settled['ledger']['passed'])} | "
            f"{settled['ledger']['new_facts']:,} facts for {settled['ledger']['charged_requests']:,} items "
            f"{' '.join(settled['ledger']['failures'])} |",
        ]
    probe = report.get("probe")
    if probe:
        lines.append(
            f"| Fresh job after soak, no restart | {mark(probe['passed'])} | "
            f"{probe['outcome']['completed_items']}/{args.probe_items} done in "
            f"{probe['outcome']['elapsed_seconds']:.0f} s (limit {args.probe_seconds} s) |"
        )
    api = report.get("api")
    if api:
        for window in api["windows"]:
            gate = window["chat_gate"]
            p95 = gate["p95_seconds"]
            lines.append(
                f"| API window {window['index']}: {args.api_rate} RPS, {window['duration_seconds']} s | "
                f"{mark(window['passed'])} | p95 {'n/a' if p95 is None else f'{p95 * 1000:.1f} ms'}, "
                f"{gate['server_errors']} 5xx, success {gate['success_ratio']:.4f} "
                f"{' '.join(gate['failures'] + window['embedding_gate'].get('failures', []))} |"
            )
        settled = api["after_load"]
        lines.append(
            f"| API: stores idle, accounting drained, charged once | {mark(settled['passed'] and api['accounting_capacity_refusals']['passed'])} | "
            f"{settled['ledger']['new_facts']:,} facts for {settled['ledger']['charged_requests']:,} requests "
            f"{' '.join(settled['ledger']['failures'])} "
            f"{refusal_detail(api['accounting_capacity_refusals'])} |"
        )
    restarts = report.get("restarts")
    if restarts:
        lines.append(
            f"| No gateway container restarted during the soak | {mark(restarts['passed'])} | "
            f"{', '.join(restarts['restarted_pods']) or 'none'} |"
        )
    lines += ["", f"Overall: {mark(bool(report.get('passed')))}", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--kind", default="kind")
    parser.add_argument("--batch-items", type=int, default=20_000)
    parser.add_argument("--batch-jobs", type=int, default=12)
    parser.add_argument("--batch-timeout", type=float, default=1800)
    parser.add_argument("--min-batch-items-per-second", type=float, default=50)
    parser.add_argument("--probe-items", type=int, default=500)
    parser.add_argument("--probe-seconds", type=float, default=120)
    parser.add_argument("--idle-seconds", type=float, default=300)
    parser.add_argument("--api-rate", type=float, default=300)
    parser.add_argument("--api-seconds", type=int, default=1800)
    parser.add_argument("--api-embedding-rate", type=float, default=10)
    parser.add_argument("--max-p95-seconds", type=float, default=0.050)
    parser.add_argument("--max-embedding-p95-seconds", type=float, default=0.250)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a fresh evidence directory")
    if args.batch_items and not args.batch_jobs <= args.batch_items <= 120_000:
        parser.error("Batch items must cover every job and stay within 120,000")
    if not 0 <= args.api_seconds <= 7200 or not 1 <= args.api_rate <= 500:
        parser.error("API soak supports up to 2 hours at 1 to 500 RPS")
    if not 0 <= args.api_embedding_rate <= 50 or not 0 < args.probe_items <= 5000:
        parser.error("Embedding rate is 0 to 50 RPS and the probe is 1 to 5,000 items")
    if args.batch_items and math.ceil(args.batch_items / args.batch_jobs) > 10_000:
        parser.error("Each job is limited to 10,000 items; add jobs")
    cluster = LifecycleCluster(args.output, kind=args.kind, purpose="accounting-soak", nodes=1)
    write_json(
        cluster.output / "arguments.json", {key: str(value) for key, value in vars(args).items()}
    )
    report: dict[str, object] = {"passed": False}
    with cluster.owned(args.image):
        install_native_dependencies(cluster, args.image)
        values = native_values(cluster, args.image, api_replicas=2, batch_replicas=3)
        prime_database(cluster, values, seeder=seed_native_fixture)
        try:
            asyncio.run(exercise(cluster, values, args.image, args, report))
        finally:
            write_json(cluster.output / "soak-report.json", report)
            (cluster.output / "summary.md").write_text(markdown(report, args))
            collect_native_logs(cluster)
            collect_role_logs(cluster)
    cluster.event("soak_completed", passed=report["passed"])
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
