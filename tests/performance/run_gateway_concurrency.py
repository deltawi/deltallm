"""Canonical one-token nonstream workload with real local PostgreSQL/Redis evidence."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import os
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
    fixture_database_url,
    fixture_key,
    require_local_url,
)
from tests.performance.gateway_concurrency_metrics import MetricsRecorder
from tests.performance.gateway_concurrency_manifest import read_manifest

ERROR_CODES = {
    "audit_persistence_unavailable",
    "gateway_preflight_global_parallel_exceeded",
    "gateway_preflight_org_parallel_exceeded",
    "prompt_resolution_timeout",
    "spend_ingestion_unavailable",
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


async def measure(args: argparse.Namespace) -> dict[str, object]:
    if not 0 < args.rate <= 200 or not 5 <= args.duration <= 600:
        raise ValueError("Use rates up to 200 RPS and durations from 5 to 600 seconds")
    manifest = read_manifest(args.server_manifest)
    endpoint = require_local_url(args.url, schemes={"http"})
    urls = [require_local_url(url, schemes={"http"}) for url in args.metrics_url]
    if len(urls) != manifest.api_processes:
        raise ValueError("Provide one metrics endpoint for every declared API process")
    redis_url = require_local_url(os.environ["REDIS_URL"], schemes={"redis", "rediss"})
    key = fixture_key()
    db = Prisma(datasource={"url": fixture_database_url()})
    redis = Redis.from_url(
        redis_url,
        decode_responses=True,
        max_connections=2,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output_dir / f"metrics-{uuid4().hex}.jsonl"
    await db.connect()
    try:
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

            before = await dependency_counts(db, redis)
            async with MetricsRecorder(urls, metrics_path) as recorder:
                arrival_start = perf_counter() - recorder.started
                run = await run_constant_arrival(
                    rate=args.rate,
                    duration_seconds=args.duration,
                    max_in_flight=1000,
                    request=request,
                )
            after = await dependency_counts(db, redis)
    finally:
        await redis.aclose()
        await db.disconnect()
    report = summarize(run, target_rate=args.rate)
    report["success_count"] = sum(
        sample.status_code is not None and 200 <= sample.status_code < 300 and sample.error is None
        for sample in run.samples
    )
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
            "client_in_flight": in_flight_series(run),
            "error_counts": dict(Counter(sample.error for sample in run.samples if sample.error)),
            "dependency_call_deltas_including_background": {
                name: after.get(name, 0) - before.get(name, 0)
                for name in before.keys() | after.keys()
            },
            "qualification": "baseline_only",
        }
    )
    raw_path, summary_path = write_results(run, report, args.output_dir)
    return {"summary": str(summary_path), "raw": str(raw_path), **report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:59440/v1/chat/completions")
    parser.add_argument("--metrics-url", action="append", required=True)
    parser.add_argument("--label", choices=("before", "after"), required=True)
    parser.add_argument("--rate", type=float, default=50)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--server-manifest", type=Path, required=True)
    print(json.dumps(asyncio.run(measure(parser.parse_args())), indent=2))
