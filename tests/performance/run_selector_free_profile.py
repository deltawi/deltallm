"""Run one warmup plus three measured constant-arrival rounds against a local gateway.

Uses the canonical load generator. DATABASE_URL, REDIS_URL, and DELTALLM_LOAD_API_KEY
must explicitly identify disposable local dependencies; no live provider is used.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from prisma import Prisma
from prometheus_client.parser import text_string_to_metric_families
from redis.asyncio import Redis

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)


async def snapshot(client, db, redis):
    rows = await db.query_raw("""
        SELECT COALESCE(SUM(calls), 0)::bigint AS calls FROM pg_stat_statements
        WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND query NOT LIKE '%pg_stat_statements%'
    """)
    commands = await redis.info("commandstats")
    provider = (await client.get("http://127.0.0.1:59441/stats")).json()
    metrics = (await client.get("http://127.0.0.1:59440/metrics")).text
    phases = {}
    for family in text_string_to_metric_families(metrics):
        for sample in family.samples:
            if sample.name.startswith(
                "deltallm_request_phase_latency_seconds_"
            ) and sample.name.endswith(("_count", "_sum")):
                key = sample.labels["phase"] + (
                    "_count" if sample.name.endswith("_count") else "_seconds"
                )
                phases[key] = phases.get(key, 0) + sample.value
    return {
        "sql": int(rows[0]["calls"]),
        "provider": provider["calls"],
        "phases": phases,
        "redis": {key: value["calls"] for key, value in commands.items() if key != "cmdstat_info"},
    }


def differences(before, after):
    return {key: after.get(key, 0) - before.get(key, 0) for key in before.keys() | after.keys()}


def queue_samples(run):
    points = [
        (
            second,
            sum(
                sample.start_offset_seconds <= second < sample.completion_offset_seconds
                for sample in run.samples
            ),
        )
        for second in range(1, 60)
    ]
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sum(
        (x - mean_x) ** 2 for x, _ in points
    )
    return {"in_flight_at_each_second": points, "in_flight_slope_per_second": slope}


async def measure(label, output_dir):
    for name in ("DATABASE_URL", "REDIS_URL"):
        if urlparse(os.environ[name]).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The regression profile requires disposable loopback dependencies")
    db = Prisma(datasource={"url": os.environ["DATABASE_URL"]})
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    key = os.environ["DELTALLM_LOAD_API_KEY"]
    await db.connect()
    try:
        async with httpx.AsyncClient(
            timeout=10, limits=httpx.Limits(max_connections=32, max_keepalive_connections=32)
        ) as client:

            async def send(index, request_id):
                response = await client.post(
                    "http://127.0.0.1:59440/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "x-request-id": request_id},
                    json={
                        "model": "pr2-local-chat",
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        "max_tokens": 1,
                        "stream": False,
                    },
                )
                return RequestResult(
                    status_code=response.status_code, bytes_received=len(response.content)
                )

            for round_index in range(4):
                before = await snapshot(client, db, redis)
                run = await run_constant_arrival(
                    rate=10, duration_seconds=60, max_in_flight=32, request=send
                )
                after = await snapshot(client, db, redis)
                report = summarize(run, target_rate=10)
                report.update(
                    label=label,
                    round=round_index,
                    warmup=round_index == 0,
                    sql_calls=after["sql"] - before["sql"],
                    provider_calls=after["provider"] - before["provider"],
                    redis_calls=differences(before["redis"], after["redis"]),
                    phase_metrics=differences(before["phases"], after["phases"]),
                    **queue_samples(run),
                )
                write_results(run, report, output_dir / label)
                print(json.dumps(report), flush=True)
                if report["success_count"] != 600 or report["generator_dropped_count"]:
                    raise RuntimeError("local performance profile failed")
    finally:
        await redis.aclose()
        await db.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, choices=("before", "after"))
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()
    asyncio.run(measure(arguments.label, arguments.output_dir))
