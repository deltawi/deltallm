"""Constant-arrival cache-path comparison using the repository's ASGI test fixture.

Run this file with PYTHONPATH set to the checkout being measured, using that
checkout's locked environment. Compare the same file against both revisions.
This is a local gateway regression profile, not PostgreSQL/Redis or deployment
capacity certification. Real infrastructure tests remain separate gates.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import inspect
import json
import logging
from pathlib import Path
from time import perf_counter

import httpx

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)

# Match the existing pytest/application bootstrap import order for legacy facades.
from tests.conftest import test_app as app_fixture
from src.cache import CacheKeyBuilder, InMemoryBackend, NoopCacheMetrics
from src.cache import key_builder as key_builder_module


def instrument_async_methods(target, counts, durations):
    def wrap(name, method):
        async def observed(*args, **kwargs):
            counts[name] += 1
            started = perf_counter()
            try:
                return await method(*args, **kwargs)
            finally:
                durations[name] += perf_counter() - started

        return observed

    for name in dir(type(target)):
        method = getattr(target, name)
        if inspect.iscoroutinefunction(method):
            setattr(target, name, wrap(name, method))


def queue_slope(run, seconds):
    points = [
        (
            second,
            sum(
                sample.start_offset_seconds <= second < sample.completion_offset_seconds
                for sample in run.samples
            ),
        )
        for second in range(1, seconds)
    ]
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sum(
        (x - mean_x) ** 2 for x, _ in points
    )
    return {"in_flight_samples": points, "in_flight_slope_per_second": slope}


async def measure_case(case, label, output_dir):
    app = await app_fixture.__wrapped__()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("src.services.key_service").setLevel(logging.WARNING)
    record = next(iter(app.state._test_repo.records.values()))
    record.rpm_limit, record.tpm_limit = 1000, 1_000_000
    app.state.cache_backend = InMemoryBackend(max_size=256)
    app.state.cache_key_builder = CacheKeyBuilder(custom_salt="profile-only")
    app.state.cache_metrics = NoopCacheMetrics()
    redis_counts, redis_duration = Counter(), Counter()
    cache_counts, cache_duration = Counter(), Counter()
    provider_counts, provider_duration = Counter(), Counter()
    instrument_async_methods(app.state.redis, redis_counts, redis_duration)
    instrument_async_methods(app.state.cache_backend, cache_counts, cache_duration)
    instrument_async_methods(app.state.http_client, provider_counts, provider_duration)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def send(index, request_id):
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer sk-test", "x-request-id": request_id},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": f"fixed {index if case == 'miss' else 0}"}
                    ],
                    "max_tokens": 1,
                    "stream": False,
                },
            )
            return RequestResult(
                status_code=response.status_code, bytes_received=len(response.content)
            )

        await send(-1, "warmup")
        for counter in (
            redis_counts,
            redis_duration,
            cache_counts,
            cache_duration,
            provider_counts,
            provider_duration,
        ):
            counter.clear()
        run = await run_constant_arrival(
            rate=10, duration_seconds=20, max_in_flight=16, request=send
        )
    report = summarize(run, target_rate=10)
    report.update(
        label=label,
        case=case,
        environment="ASGI with fake Redis, fixed provider mock, no database",
        measured_key_builder=key_builder_module.__file__,
        redis_calls=dict(redis_counts),
        cache_calls=dict(cache_counts),
        provider_calls=dict(provider_counts),
        provider_seconds=dict(provider_duration),
        **queue_slope(run, 20),
    )
    write_results(run, report, output_dir / label / case)
    print(json.dumps(report), flush=True)
    assert report["success_count"] == 200 and not report["generator_dropped_count"]
    assert (
        cache_counts == {"get": 200, "set": 200} if case == "miss" else cache_counts == {"get": 200}
    )
    assert provider_counts == ({"post": 200} if case == "miss" else {})
    per_request_redis = (
        {
            "get": 1,
            "eval": 6,
            "hgetall": 1,
            "mget": 1,
            "zadd": 2,
            "incr": 1,
            "expire": 2,
            "incrby": 1,
            "zremrangebyscore": 1,
            "pexpire": 1,
        }
        if case == "miss"
        else {"get": 1, "eval": 3}
    )
    assert redis_counts == {name: count * 200 for name, count in per_request_redis.items()}


async def main(args):
    for case in ("miss", "hit"):
        await measure_case(case, args.label, args.output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, choices=("before", "after"))
    parser.add_argument("--output-dir", required=True, type=Path)
    asyncio.run(main(parser.parse_args()))
