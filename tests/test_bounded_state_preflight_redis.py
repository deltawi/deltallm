"""Gateway preflight capacity drains across pods (docs/operations/bounded-state.md).

Preflight capacity is a shared Redis lease set per scope. Pods release their
own leases when a response ends; a pod that dies holding leases gives them back
when their TTL runs out, without a restart anywhere.
"""

import asyncio

import pytest

from src.models.errors import RateLimitError
from src.services.admission.limit_counter import (
    LimitCounter,
    ParallelLimitCheck,
    _parallel_lease_key,
)
from tests import test_selector_capacity_redis as fixtures

capacity_redis = fixtures.capacity_redis

pytestmark = [pytest.mark.integration, pytest.mark.redis]

LIMIT = 12
TTL_SECONDS = 1


async def test_preflight_leases_drain_after_load_and_after_a_pod_dies(capacity_redis):
    first, second, identity = capacity_redis
    pods = [LimitCounter(client, degraded_mode="fail_closed") for client in (first, second, first)]
    checks = [ParallelLimitCheck("gateway_preflight_global", identity, LIMIT)]
    key = _parallel_lease_key("gateway_preflight_global", identity)

    async def acquire(pod):
        try:
            return pod, await pod.acquire_parallel_leases(checks, ttl_seconds=TTL_SECONDS)
        except RateLimitError:
            return pod, None

    # Three pods race for twice the capacity: exactly LIMIT requests get in.
    held = await asyncio.gather(*(acquire(pods[index % 3]) for index in range(2 * LIMIT)))
    admitted = [(pod, leases) for pod, leases in held if leases]
    assert len(admitted) == LIMIT
    assert await first.zcard(key) == LIMIT

    # Pod 0 dies with its leases; the others finish their requests normally.
    dead = [leases for pod, leases in admitted if pod is pods[0]]
    assert dead
    await asyncio.gather(
        *(
            pod.release_parallel_leases(list(leases))
            for pod, leases in admitted
            if pod is not pods[0]
        )
    )
    assert await first.zcard(key) == len(dead)

    # Once the dead pod's leases expire, the full capacity is back.
    await asyncio.sleep(TTL_SECONDS + 0.2)
    refill = await asyncio.gather(*(acquire(pods[1 + index % 2]) for index in range(LIMIT)))
    assert all(leases for _, leases in refill)
    await asyncio.gather(*(pod.release_parallel_leases(list(leases)) for pod, leases in refill))
    assert await first.exists(key) == 0
