"""Focused constant-arrival comparison for the Realtime review fixes.

Run this same file with PYTHONPATH pointing at each checkout. Uses isolated real
Redis/PostgreSQL and a fixed 1 ms provider delay for receipt acceptance. It measures
the changed owner operations, excluding handshake, dispatch and settlement; it is
not a production capacity or complete WebSocket latency benchmark.
"""

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from prisma import Prisma
from redis.asyncio import Redis

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from tests.performance.routing_cache_profile import queue_slope
from tests.test_realtime_billing_postgres import dispatch, realtime_db
from tests.test_selector_charge_db_integration import selector_billing_db
from tests.test_realtime_runtime_postgres import response_event
from src.db.billing.realtime_billing import RealtimeBillingRepository
from src.realtime.admission import RealtimeSessionPermit
from src.realtime.config import RealtimeSettings
from src.router.candidates import AttemptCapacity
from src.router.cooldown import CooldownManager
from src.router.health_state import DeploymentHealthRef
from src.router.state import RedisStateBackend
from src.services.admission.limit_counter import LimitCounter, ParallelLimitCheck

RATE, SECONDS = 20, 5


def counted(method, calls, name):
    async def observed(*args, **kwargs):
        calls[name] += 1
        return await method(*args, **kwargs)

    return observed


async def measure(args, name, operation, redis, expected, provider_seconds=()):
    calls = Counter()
    transaction = Prisma.tx

    def counted_transaction(*args, **kwargs):
        calls["sql_transactions"] += 1
        return transaction(*args, **kwargs)

    with (
        patch.object(redis, "execute_command", counted(redis.execute_command, calls, "redis")),
        patch.object(Prisma, "query_raw", counted(Prisma.query_raw, calls, "sql_query")),
        patch.object(Prisma, "execute_raw", counted(Prisma.execute_raw, calls, "sql_execute")),
        patch.object(Prisma, "tx", counted_transaction),
    ):

        async def send(index, request_id):
            await operation(index)
            return RequestResult(status_code=200)

        run = await run_constant_arrival(
            rate=RATE, duration_seconds=SECONDS, max_in_flight=16, request=send
        )
    report = summarize(run, target_rate=RATE)
    report.update(
        label=args.label,
        case=name,
        calls=dict(calls),
        fixed_provider_delay_seconds=0.001 if provider_seconds else 0,
        measured_provider_seconds=sum(provider_seconds),
        environment="local Redis/PostgreSQL; owner operations; fixed 1 ms receipt delay",
        **queue_slope(run, SECONDS),
    )
    write_results(run, report, args.output_dir / args.label / name)
    print(json.dumps(report), flush=True)
    assert report["success_count"] == RATE * SECONDS and not report["generator_dropped_count"]
    assert calls == {key: value * RATE * SECONDS for key, value in expected.items()}


async def lease_profiles(args, redis, identity):
    counter = LimitCounter(redis, degraded_mode="fail_closed")
    check = ParallelLimitCheck("key", identity, 3)
    long = await counter.acquire_parallel_leases([check], ttl_seconds=300)
    short = await counter.acquire_parallel_leases([check], ttl_seconds=30)
    try:
        for strict in (False, True):

            async def renew(index):
                await counter.refresh_parallel_leases(
                    list(short), ttl_seconds=30, require_owned=strict
                )

            await measure(args, "renew_strict_" + str(strict), renew, redis, {"redis": 1})
    finally:
        await counter.release_parallel_leases([*long, *short])


async def receipt_profile(args, redis, db, context, identity, recovering):
    context = replace(context, started_at=context.started_at - timedelta(minutes=2))
    state = RedisStateBackend(redis, degraded_mode="fail_closed")
    cooldown = CooldownManager(state)
    owner = SimpleNamespace(settings=RealtimeSettings(), billing=RealtimeBillingRepository(db))
    request = SimpleNamespace(session_id=context.attribution.session_id, profile="realtime")
    route = SimpleNamespace(
        usage_type="tokens",
        generation=SimpleNamespace(router=SimpleNamespace(state=state), cooldown_manager=cooldown),
    )
    permits = []
    for index in range(RATE * SECONDS):
        ref = DeploymentHealthRef(f"{identity}-{recovering}-{index}")
        if recovering:
            await cooldown.manual_cooldown(ref, 60)
            await redis.delete(state.keyspace.cooldown(ref.deployment_id, ref.generation))
        permit = RealtimeSessionPermit(owner, request, route, context, None)
        permit.provider_permit = await state.acquire_attempt(
            ref, AttemptCapacity(require_shared=True)
        )
        permit.current = await dispatch(db, context)
        permits.append(permit)
    provider_seconds = []
    try:

        async def accept(index):
            started = perf_counter()
            await asyncio.sleep(0.001)
            provider_seconds.append(perf_counter() - started)
            event = response_event(index)
            event["response"]["id"] = f"resp_{recovering}_{index}"
            event["response"]["status"] = "completed"
            await permits[index].accept_usage(event)

        await measure(
            args,
            "receipt_recovering_" + str(recovering),
            accept,
            redis,
            {"redis": 1, "sql_query": 2, "sql_execute": 1, "sql_transactions": 1},
            provider_seconds,
        )
    finally:
        for permit in permits:
            await permit.close()


async def main(args):
    logging.disable(logging.INFO)
    redis = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    identity = "realtime-profile-" + uuid4().hex
    try:
        await lease_profiles(args, redis, identity)
        async with asynccontextmanager(selector_billing_db.__wrapped__)() as selector:
            async with asynccontextmanager(realtime_db.__wrapped__)(selector) as (db, context):
                for recovering in (False, True):
                    await receipt_profile(args, redis, db, context, identity, recovering)
    finally:
        keys = [key async for key in redis.scan_iter(match=f"*{identity}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    asyncio.run(main(parser.parse_args()))
