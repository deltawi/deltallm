"""Measure auth against real Redis with the existing constant-arrival runner.

Run with --before <commit> --redis-url <isolated Redis URL> --output-dir <path>.
The primary repository is a fixed in-process fixture; SQL counts are logical.
No provider runs at this seam. Results do not certify full gateway capacity.
"""

from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import gc
import json
from pathlib import Path
import subprocess
import types
from uuid import uuid4

from redis.asyncio import Redis

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from src.db.repositories import KeyRecord
from src.services.key_service import KeyService
from tests.performance.routing_cache_profile import queue_slope


class ObservedRedis(Redis):
    calls = 0

    async def eval(self, script, numkeys, *args):
        self.calls += 1
        return await super().eval(script, numkeys, *args)


class PrimaryFixture:
    calls = 0

    def __init__(self, populated):
        limits = {f"model-{i:02d}-".ljust(256, "x"): 1000 for i in range(64)} if populated else {}
        self.record = KeyRecord(
            token="fixture",
            output_tpm_limit=1000,
            model_output_tpm_limit=limits,
            team_model_output_tpm_limit=limits,
        )

    async def get_by_token(self, token):
        self.calls += 1
        return self.record.__class__(**{**self.record.__dict__, "token": token})


def before_service(revision):
    modules = []
    for name in ("key_auth_cache", "key_service"):
        source = subprocess.check_output(
            ["git", "show", f"{revision}:src/services/{name}.py"], text=True
        )
        module = types.ModuleType(f"src.services.profile_before_{name}")
        module.__package__ = "src.services"
        # Dataclass field resolution requires a registered module.
        import sys

        sys.modules[module.__name__] = module
        exec(compile(source, f"{revision}:{name}", "exec"), module.__dict__)
        modules.append(module)
    modules[1].KeyAuthCache = modules[0].KeyAuthCache
    return modules[1].KeyService


async def measure(args, service_type, label, path, populated):
    redis = ObservedRedis.from_url(args.redis_url, decode_responses=True)
    repository = PrimaryFixture(populated)
    service = service_type(repository, redis, auth_cache_ttl_seconds=60)
    prefix = uuid4().hex
    tokens = (
        [prefix + str(i) for i in range(int(args.rate * args.duration))]
        if path == "cold"
        else [prefix]
    )
    try:
        if path == "warm":
            await service.get_auth_by_token_hash(tokens[0])
        # Release the previous case's completed tasks before its next arrival window.
        gc.collect()
        redis.calls = repository.calls = 0

        async def request(index, request_id):
            auth = await service.get_auth_by_token_hash(tokens[index] if path == "cold" else prefix)
            assert auth.key_output_tpm_limit == 1000
            assert len(auth.key_model_output_tpm_limit or {}) == (64 if populated else 0)
            return RequestResult(status_code=200)

        result = await run_constant_arrival(
            rate=args.rate, duration_seconds=args.duration, max_in_flight=128, request=request
        )
        summary = summarize(result, target_rate=args.rate)
        summary.update(queue_slope(result, args.duration))
        summary.update(
            label=label,
            path=path,
            populated_maps=populated,
            redis_calls=redis.calls,
            primary_calls=repository.calls,
            provider_calls=0,
            provider_seconds=0,
            fixture_note="Fixed in-process primary; real standalone Redis; no provider",
        )
        summary["cache_value_bytes"] = {
            f"v{version}": len((await redis.get(f"key:v{version}:{tokens[0]}") or "").encode())
            for version in (5, 7)
        }
        write_results(result, summary, args.output_dir)
        count = result.target_count
        assert summary["success_count"] == count and not result.generator_dropped_count, summary
        assert redis.calls == count * (2 if path == "cold" else 1)
        assert repository.calls == (count if path == "cold" else 0)
        return summary
    finally:
        keys = [f"key:v{version}:{token}" for token in tokens for version in (5, 7)]
        for start in range(0, len(keys), 500):
            await redis.delete(*keys[start : start + 500])
        await redis.aclose()


async def main(args):
    baseline = before_service(args.before)
    results = []
    for path, populated in (("warm", False), ("warm", True), ("cold", True)):
        for label, service in (("before", baseline), ("after", KeyService)):
            results.append(await measure(args, service, label, path, populated))
    payload = {
        "before_revision": args.before,
        "after_source_sha256": sha256(
            Path("src/services/key_auth_cache.py").read_bytes()
        ).hexdigest(),
        "results": results,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rate", type=int, default=50)
    parser.add_argument("--duration", type=int, default=10)
    arguments = parser.parse_args()
    if arguments.duration < 3:
        parser.error("--duration must be at least three seconds for queue samples")
    asyncio.run(main(arguments))
