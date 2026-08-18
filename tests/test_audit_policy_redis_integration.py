from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.services.audit_policy_invalidation import AuditPolicyInvalidation
from src.telemetry.lifecycle import WorkerState


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/15")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_redis_policy_invalidation_cross_instance_and_reconnect() -> None:
    publisher_client = Redis.from_url(REDIS_URL, decode_responses=True)
    listener_client = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        try:
            await publisher_client.ping()
            await listener_client.ping()
        except Exception:
            pytest.skip("a real Redis service is not available")

        channel = f"deltallm:test:v1:audit-policy:{uuid4().hex}"
        invalidated_organizations: list[str] = []
        invalidated = asyncio.Event()
        cache_resets = 0
        reconnected = asyncio.Event()

        def invalidate_one(organization_id: str) -> None:
            invalidated_organizations.append(organization_id)
            invalidated.set()

        def invalidate_all() -> None:
            nonlocal cache_resets
            cache_resets += 1
            # One reset occurs at initial subscribe, one when the connection
            # fails, and the third proves a fresh subscription is active.
            if cache_resets > 2:
                reconnected.set()

        publisher = AuditPolicyInvalidation(
            redis_client=publisher_client,
            channel=channel,
            invalidate_one=lambda _organization_id: None,
            invalidate_all=lambda: None,
        )
        listener = AuditPolicyInvalidation(
            redis_client=listener_client,
            channel=channel,
            invalidate_one=invalidate_one,
            invalidate_all=invalidate_all,
        )
        await publisher.start(timeout_seconds=1)
        await listener.start(timeout_seconds=1)

        assert listener.health.state is WorkerState.READY
        await publisher.publish(organization_id="org-1", enabled=False, version=2)
        await asyncio.wait_for(invalidated.wait(), timeout=1)
        assert invalidated_organizations == ["org-1"]

        invalidated.clear()
        await publisher_client.publish(channel, "not-json")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(invalidated.wait(), timeout=0.05)

        await listener_client.connection_pool.disconnect()
        await asyncio.wait_for(reconnected.wait(), timeout=2)
        assert listener.health.state is WorkerState.READY

        invalidated.clear()
        await publisher.publish(organization_id="org-2", enabled=False, version=3)
        await asyncio.wait_for(invalidated.wait(), timeout=1)
        assert invalidated_organizations[-1] == "org-2"
    finally:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1
        if "publisher" in locals():
            await publisher.shutdown(deadline=deadline)
        if "listener" in locals():
            await listener.shutdown(deadline=deadline)
        await publisher_client.aclose()
        await listener_client.aclose()
