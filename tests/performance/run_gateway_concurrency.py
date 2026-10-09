"""Canonical one-token nonstream workload with real local PostgreSQL/Redis evidence."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
import json
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

import httpx
from prisma import Prisma
from redis.asyncio import Redis

from scripts.measure_gateway_load import (
    RequestResult,
    RunResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from tests.performance.gateway_concurrency_fixture import (
    MODEL,
    fixture_key,
)
from tests.performance.gateway_concurrency_diagnostics import (
    DependencyDiagnosticsRecorder,
    KubernetesResourceRecorder,
)
from tests.performance.gateway_concurrency_dependencies import local_dependencies, require_local_url
from tests.performance.gateway_concurrency_metrics import MetricSource, MetricsRecorder
from tests.performance.gateway_concurrency_redis import (
    redis_client_breakdown,
    redis_round_trip_budget,
)
from tests.performance.gateway_concurrency_manifest import read_manifest

ERROR_CODES = {
    "gateway_draining",
    "request_deadline_exceeded",
    "gateway_work_unavailable",
    "edge_unavailable",
    "gateway_ingress_full",
    "gateway_ingress_buffer_full",
    "gateway_request_body_too_large",
    "gateway_request_body_timeout",
    "invalid_content_length",
    "auth_fallback_unavailable",
    "database_unavailable",
    "audit_persistence_unavailable",
    "gateway_preflight_global_parallel_exceeded",
    "gateway_preflight_org_parallel_exceeded",
    "prompt_resolution_timeout",
    "spend_ingestion_unavailable",
    "spend_persistence_unavailable",
    "rate_limit_exceeded",
}
MAX_RESPONSE_BYTES = 65536
DEPENDENCY_SNAPSHOT_TIMEOUT_SECONDS = 5.0


def error_code(payload: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error", payload.get("detail"))
        if (
            isinstance(error, dict)
            and isinstance(error.get("code"), str)
            and error["code"] in ERROR_CODES
        ):
            return str(error["code"])
    return "unclassified_http_error"


def valid_completion(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    usage = payload.get("usage")
    choices = payload.get("choices")
    return (
        isinstance(usage, dict)
        and usage.get("completion_tokens") == 1
        and isinstance(choices, list)
        and len(choices) == 1
        and isinstance(choices[0], dict)
        and isinstance(choices[0].get("message"), dict)
        and choices[0]["message"].get("role") == "assistant"
        and choices[0]["message"].get("content") == "OK"
    )


async def dependency_counts(db: Prisma, redis: Redis) -> dict[str, int]:
    async with asyncio.timeout(DEPENDENCY_SNAPSHOT_TIMEOUT_SECONDS):
        rows = await db.query_raw("""
            SELECT COALESCE(SUM(calls), 0)::bigint AS calls FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND query NOT LIKE '%pg_stat_statements%'
        """)
        commands = await redis.info("commandstats")
    result = {"postgres_calls_including_background": int(rows[0]["calls"])}
    for name, values in commands.items():
        if re.fullmatch(r"cmdstat_[a-z_|]{1,32}", name) and name != "cmdstat_info":
            result[name] = int(values["calls"])
    return result


def in_flight_series(run: RunResult) -> list[dict[str, float]]:
    events = sorted(
        [(sample.start_offset_seconds, 1) for sample in run.samples]
        + [(sample.completion_offset_seconds, -1) for sample in run.samples]
    )
    current = 0
    cursor = 0
    points = []
    for second in range(int(run.arrival_window_seconds) + 1):
        while cursor < len(events) and events[cursor][0] <= second:
            current += events[cursor][1]
            cursor += 1
        points.append({"offset_seconds": float(second), "client_in_flight": float(current)})
    return points


def generator_evidence_failures(run: RunResult, *, target_rate: float) -> list[str]:
    failures = []
    if run.scheduled_count + run.generator_dropped_count != run.target_count:
        failures.append("generator_target_accounting")
    if len(run.samples) != run.scheduled_count:
        failures.append("generator_completion_accounting")
    if run.generator_dropped_count:
        failures.append("generator_drops")
    if run.scheduled_count / run.arrival_window_seconds < target_rate * 0.99:
        failures.append("generator_offered_rate")
    if run.generator_start_skew_seconds > 0.1:
        failures.append("generator_start_skew")
    lag = summarize(run, target_rate=target_rate)["scheduling_lag_seconds"]
    if not isinstance(lag, dict) or lag["p95"] is None or lag["p95"] > 0.1:
        failures.append("generator_scheduling_lag")
    return failures


async def measure(
    args: argparse.Namespace,
    *,
    resource_recorder: KubernetesResourceRecorder | None = None,
    workload_runner: Callable[[], Awaitable[RunResult]] | None = None,
    allow_1000_rps_diagnostic: bool = False,
) -> dict[str, object]:
    supported_rate = 0 < args.rate <= 500 or (
        allow_1000_rps_diagnostic and args.rate == 1000 and args.duration <= 60
    )
    if not supported_rate or not 5 <= args.duration <= 600:
        raise ValueError(
            "Use rates up to 500 RPS and durations from 5 to 600 seconds; "
            "1,000 RPS needs explicit diagnostic mode and a duration up to 60 seconds"
        )
    manifest = read_manifest(args.server_manifest)
    endpoint = require_local_url(args.url, schemes={"http"})
    api_urls = [require_local_url(url, schemes={"http"}) for url in args.metrics_url]
    if len(api_urls) != manifest.api_processes:
        raise ValueError("Provide one metrics endpoint for every declared API process")
    worker_urls = [
        require_local_url(url, schemes={"http"})
        for url in getattr(args, "accounting_worker_metrics_url", [])
    ]
    if len(worker_urls) != getattr(manifest, "accounting_worker_processes", 0):
        raise ValueError(
            "Provide one metrics endpoint for every declared accounting-worker process"
        )
    worker_roles = getattr(
        args, "accounting_worker_source_roles", ["accounting_worker"] * len(worker_urls)
    )
    if len(worker_roles) != len(worker_urls) or any(
        role not in {"accounting_worker", "accounting_request"} for role in worker_roles
    ):
        raise ValueError("Provide one fixed accounting role for each worker metrics endpoint")
    metric_sources = [
        MetricSource(url=url, role="api", process=index) for index, url in enumerate(api_urls)
    ] + [
        MetricSource(url=url, role=role, process=index)
        for index, (url, role) in enumerate(zip(worker_urls, worker_roles, strict=True))
    ]
    key = fixture_key()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output_dir / f"metrics-{uuid4().hex}.jsonl"
    diagnostics_path = args.output_dir / f"dependencies-{uuid4().hex}.jsonl"
    dependency_recorder: DependencyDiagnosticsRecorder | None = None
    async with local_dependencies() as dependencies:
        db, redis = dependencies.database, dependencies.redis
        async with httpx.AsyncClient(
            timeout=10,
            limits=httpx.Limits(max_connections=1000, max_keepalive_connections=100),
            trust_env=False,
            follow_redirects=False,
        ) as client:

            async def request(index: int, request_id: str) -> RequestResult:
                del index
                async with asyncio.timeout(10):
                    body = bytearray()
                    async with client.stream(
                        "POST",
                        endpoint,
                        headers={"Authorization": f"Bearer {key}", "x-request-id": request_id},
                        json={
                            "model": MODEL,
                            "messages": [{"role": "user", "content": "Reply with OK."}],
                            "max_tokens": 1,
                            "stream": False,
                            "metadata": {"cache": False},
                        },
                    ) as response:
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                return RequestResult(
                                    response.status_code, error="response_too_large"
                                )
                            body.extend(chunk)
                        try:
                            payload = json.loads(body)
                        except (ValueError, UnicodeDecodeError):
                            return RequestResult(response.status_code, error="invalid_response")
                        error = None
                        if response.status_code >= 400:
                            error = error_code(payload)
                        elif not valid_completion(payload):
                            error = "invalid_response"
                        return RequestResult(
                            response.status_code, error=error, bytes_received=len(body)
                        )

            warmup = await request(-1, uuid4().hex)
            if warmup.status_code != 200 or warmup.error is not None:
                raise ValueError(
                    f"Workload precheck failed: HTTP {warmup.status_code}, {warmup.error}"
                )
            before = await dependency_counts(db, redis)
            async with AsyncExitStack() as stack:
                recorder = await stack.enter_async_context(
                    MetricsRecorder(metric_sources, metrics_path, interval_seconds=5)
                )
                if getattr(args, "dependency_diagnostics", False):
                    dependency_recorder = await stack.enter_async_context(
                        DependencyDiagnosticsRecorder(db, redis, diagnostics_path)
                    )
                if resource_recorder is not None:
                    await stack.enter_async_context(resource_recorder)
                arrival_start = perf_counter() - recorder.started
                run = await recorder.run_workload(
                    workload_runner
                    or (
                        lambda: run_constant_arrival(
                            rate=args.rate,
                            duration_seconds=args.duration,
                            max_in_flight=1000,
                            request=request,
                            drain_timeout_seconds=15,
                        )
                    )
                )
            after = await dependency_counts(db, redis)
    report = summarize(run, target_rate=args.rate)
    report["success_count"] = sum(
        sample.status_code is not None and 200 <= sample.status_code < 300 and sample.error is None
        for sample in run.samples
    )
    source_evidence = recorder.evidence()
    redis_budget = redis_round_trip_budget(recorder)
    report.update(
        {
            "label": args.label,
            "workload": "fixed-one-token-nonstream-v1",
            "response_cache_bypass": True,
            "generator_python": sys.version.split()[0],
            "generator_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "server_manifest": manifest.model_dump(mode="json"),
            "server_manifest_source": "operator_declared",
            "metrics_file": metrics_path.name,
            "metrics_arrival_start_offset_seconds": arrival_start,
            "metrics_scrape_errors": recorder.errors,
            "metrics_sources": source_evidence,
            "redis_round_trip_budget": redis_budget,
            "redis_client_breakdown": redis_client_breakdown(recorder),
            "dependency_diagnostics_file": (
                diagnostics_path.name if dependency_recorder is not None else None
            ),
            "dependency_diagnostic_snapshots": (
                dependency_recorder.snapshots if dependency_recorder is not None else 0
            ),
            "dependency_diagnostic_errors": (
                dependency_recorder.errors if dependency_recorder is not None else 0
            ),
            "resource_diagnostics_file": (
                resource_recorder.output.name if resource_recorder is not None else None
            ),
            "resource_evidence": (
                resource_recorder.evidence() if resource_recorder is not None else None
            ),
            "client_in_flight": in_flight_series(run),
            "error_counts": dict(Counter(sample.error for sample in run.samples if sample.error)),
            "dependency_call_deltas_including_background": {
                name: after.get(name, 0) - before.get(name, 0)
                for name in before.keys() | after.keys()
            },
            "qualification": "baseline_only",
            "isolated_load_generator": workload_runner is not None,
            "server_request_phases": {
                phase: recorder.histogram_delta(
                    "deltallm_request_phase_latency_seconds",
                    labels={"route": "chat_completions", "phase": phase},
                )
                for phase in (
                    "response_total",
                    "application_total",
                    "capacity_admission",
                    "budget",
                    "upstream_http",
                    "after_response",
                )
            },
            "accounting_database_latency": recorder.histogram_delta(
                "deltallm_accounting_database_call_seconds",
                roles=frozenset({"accounting_worker", "accounting_request"}),
            ),
        }
    )
    diagnostic_failures: list[str] = []
    if getattr(args, "diagnostic_gate", False):
        diagnostic_failures.extend(generator_evidence_failures(run, target_rate=args.rate))
        if dependency_recorder is None:
            diagnostic_failures.append("dependency_diagnostics_missing")
        elif dependency_recorder.snapshots < 2:
            diagnostic_failures.append("dependency_diagnostics_incomplete")
        if dependency_recorder is not None and dependency_recorder.errors:
            diagnostic_failures.append("dependency_diagnostics_errors")
        if recorder.errors:
            diagnostic_failures.append("metrics_scrape_errors")
        if redis_budget["observed_requests"] <= 0:
            diagnostic_failures.append("redis_round_trip_budget_evidence_missing")
        elif not redis_budget["passed"]:
            diagnostic_failures.append("redis_round_trip_budget_exceeded")
        if resource_recorder is None:
            diagnostic_failures.append("resource_diagnostics_missing")
        else:
            resource_evidence = resource_recorder.evidence()
            if resource_evidence["snapshots"] < 2 or resource_evidence["missing_required_roles"]:
                diagnostic_failures.append("resource_diagnostics_incomplete")
            if resource_evidence["errors"]:
                diagnostic_failures.append("resource_diagnostics_errors")
        if any(source["successful_scrapes"] < 2 for source in source_evidence):
            diagnostic_failures.append("insufficient_metrics_samples")
        if any(
            source["source_role"] in {"accounting_worker", "accounting_request"}
            and not source["accounting_metrics_observed"]
            for source in source_evidence
        ):
            diagnostic_failures.append("accounting_worker_metrics_missing")
        if any(
            sample.status_code == 500 and sample.error == "unclassified_http_error"
            for sample in run.samples
        ):
            diagnostic_failures.append("unclassified_http_500")
        report["diagnostic_failures"] = diagnostic_failures
        report["qualification"] = (
            "diagnostic_passed" if not diagnostic_failures else "diagnostic_failed"
        )
    raw_path, summary_path = write_results(
        run, report, args.output_dir, compress=getattr(args, "compress_samples", False)
    )
    if diagnostic_failures and getattr(args, "raise_on_diagnostic_failure", True):
        raise ValueError(f"Diagnostic evidence gate failed; see {summary_path}")
    return {"summary": str(summary_path), "raw": str(raw_path), **report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:59440/v1/chat/completions")
    parser.add_argument("--metrics-url", action="append", required=True)
    parser.add_argument("--accounting-worker-metrics-url", action="append", default=[])
    parser.add_argument("--diagnostic-gate", action="store_true")
    parser.add_argument("--dependency-diagnostics", action="store_true")
    parser.add_argument("--label", choices=("before", "after"), required=True)
    parser.add_argument("--rate", type=float, default=50)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--server-manifest", type=Path, required=True)
    print(json.dumps(asyncio.run(measure(parser.parse_args())), indent=2))
