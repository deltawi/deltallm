"""Constant-arrival output TPM profile using the existing local load runner.

The app has fake authentication, routing, and billing, one fixed provider, and
real Redis admission. This profile measures gateway overhead, not provider or
production capacity. The null case can also run against the base revision.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import logging
from pathlib import Path
import resource
from time import perf_counter, process_time
from uuid import uuid4

import httpx
from redis.asyncio import Redis

from scripts.measure_gateway_load import (
    RequestResult,
    _percentiles,
    run_constant_arrival,
    summarize,
    write_results,
)
from tests.conftest import test_app as app_fixture
from tests.performance.routing_cache_profile import queue_slope
from tests.test_cache import _enable_cache
from src.services.admission.limit_counter import LimitCounter


async def measure(args, case):
    requested_case = case
    model_scopes = case.startswith("seven-")
    case = case.replace("seven-", "four-", 1)
    app = await app_fixture.__wrapped__()
    record = next(iter(app.state._test_repo.records.values()))
    record.rpm_limit, record.tpm_limit = 1_000_000, 1_000_000_000
    record.max_parallel_requests = None
    environment = f"profile-{uuid4().hex}"
    redis = Redis.from_url(args.redis_url, decode_responses=True)
    counts = Counter()
    redis_latency = []
    for name in ("eval", "evalsha", "script_load", "get", "hget", "hgetall", "set", "info"):
        original = getattr(redis, name)

        async def measured(*pos, _name=name, _method=original, **kwargs):
            counts[_name] += 1
            started = perf_counter()
            try:
                return await _method(*pos, **kwargs)
            finally:
                if _name in {"eval", "evalsha"}:
                    redis_latency.append(perf_counter() - started)

        setattr(redis, name, measured)
    governed = case != "null"
    kwargs = {"environment": environment} if governed else {}
    app.state.limit_counter = LimitCounter(
        redis_client=redis, degraded_mode="fail_closed", **kwargs
    )
    if governed:
        record.user_id = "profile-user"
        record.output_tpm_limit = 1_000_000_000
        if case != "one-json":
            record.user_output_tpm_limit = record.team_output_tpm_limit = (
                record.org_output_tpm_limit
            ) = 1_000_000_000
    if model_scopes:
        from types import SimpleNamespace
        from src.db.tiers.tiers import TierModelPolicyRecord, TierPolicyLoadResult
        from src.services.tiers.tier_policy_service import TierPolicyService
        from tests.services.test_tier_policy_compiler import _assignment

        record.model_output_tpm_limit = {"gpt-4o-mini": 1_000_000_000}
        record.team_model_output_tpm_limit = {"gpt-4o-mini": 1_000_000_000}
        inputs = TierPolicyLoadResult(
            assignments=(_assignment("profile", organization_id=record.organization_id),),
            model_policies=(
                TierModelPolicyRecord(
                    "profile", "version-1", "gpt-4o-mini", output_tpm_limit=1_000_000_000
                ),
            ),
            capacity_pools=(),
        )
        service = TierPolicyService(
            repository=SimpleNamespace(load_active_tier_policy_inputs=lambda **kwargs: load()),
            mode="enforce",
            missing_service_mode="fail_closed",
        )

        async def load():
            return inputs

        await service.reload()
        app.state.tier_policy_service = service
    if case == "four-cache":
        _enable_cache(app)
    is_stream = case in {"four-stream", "four-unknown"}
    provider_calls = 0

    async def provider(request):
        nonlocal provider_calls
        provider_calls += 1
        body = {
            "id": "fixed",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 0 if case == "four-zero" else 2,
                "total_tokens": 1 if case == "four-zero" else 3,
            },
        }
        if not is_stream:
            return httpx.Response(200, json=body)
        chunk = {
            **body,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
        }
        chunk.pop("usage")
        frames = [f"data: {json.dumps(chunk)}"]
        if case != "four-unknown":
            frames.append(
                f"data: {json.dumps({**body, 'object': 'chat.completion.chunk', 'choices': []})}"
            )
        frames.append("data: [DONE]")
        return httpx.Response(200, content=("\n\n".join(frames) + "\n\n").encode())

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
            app.state.http_client = upstream
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:

                async def send(index, request_id):
                    response = await client.post(
                        "/v1/chat/completions",
                        headers={"Authorization": "Bearer sk-test", "x-request-id": request_id},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": "fixed"}],
                            "max_tokens": 10,
                            "stream": is_stream,
                        },
                    )
                    return RequestResult(
                        status_code=response.status_code, bytes_received=len(response.content)
                    )

                assert (await send(-1, "warmup")).status_code == 200
                warmup_calls = dict(counts)
                counts.clear()
                redis_latency.clear()
                provider_calls = 0
                cpu_started = process_time()
                run = await run_constant_arrival(
                    rate=args.rate, duration_seconds=args.seconds, max_in_flight=128, request=send
                )
                cpu_seconds = process_time() - cpu_started
        report = summarize(run, target_rate=args.rate)
        report.update(
            label=args.label,
            case=requested_case,
            output_scope_count=7
            if model_scopes
            else (4 if governed and case != "one-json" else int(governed)),
            redis_calls=dict(counts),
            warmup_redis_calls=warmup_calls,
            redis_command_latency_seconds=_percentiles(redis_latency),
            provider_calls=provider_calls,
            cpu_seconds=cpu_seconds,
            process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            environment="macOS ASGI, fixed provider, fake auth/routing/billing, Redis 7 standalone Docker",
            **queue_slope(run, int(args.seconds)),
        )
        keys = [
            key async for key in redis.scan_iter(match=f"deltallm:{environment}:v2:output-tpm:*")
        ]
        if keys:
            sizes = {"receipt": [], "bucket": []}
            for key in keys:
                sizes["receipt" if ":receipt:" in key else "bucket"].append(
                    await redis.memory_usage(key)
                )
            report["redis_memory"] = {
                kind: {"count": len(values), "mean_bytes": sum(values) / len(values)}
                for kind, values in sizes.items()
                if values
            }
            await redis.delete(*keys)
        write_results(run, report, args.output_dir / args.label / requested_case)
        print(json.dumps(report), flush=True)
        if case == "four-unknown":
            assert set(report["status_counts"]) <= {"200", "503"}
            assert provider_calls == report["status_counts"].get("200", 0)
        else:
            assert report["status_counts"] == {"200": run.target_count}
        assert not report["generator_dropped_count"]
        expected = (
            {"eval": run.target_count}
            if not governed
            else {
                "evalsha": run.target_count
                * (1 if case in {"four-unknown", "four-cache", "four-zero", "four-zero"} else 2)
            }
        )
        if case == "four-unknown":
            expected["evalsha"] += provider_calls
        assert dict(counts) == expected, (counts, expected)
    finally:
        await redis.aclose()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--cases",
        nargs="+",
        default=[
            "null",
            "one-json",
            "four-json",
            "four-stream",
            "four-unknown",
            "four-cache",
            "four-zero",
        ],
    )
    parser.add_argument("--rate", type=float, default=100)
    parser.add_argument("--seconds", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    for case in args.cases:
        await measure(args, case)


if __name__ == "__main__":
    asyncio.run(main())
