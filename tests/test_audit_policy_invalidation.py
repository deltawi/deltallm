from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest

from src.services.audit_policy_invalidation import AuditPolicyInvalidation
from src.telemetry.lifecycle import WorkerState


class _ControlledPubSub:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[Mapping[str, object]] = asyncio.Queue()
        self.listen_entered = asyncio.Event()

    async def subscribe(self, channel: str) -> None:
        del channel

    async def unsubscribe(self, channel: str) -> None:
        del channel

    def listen(self) -> AsyncIterator[Mapping[str, object]]:
        self.listen_entered.set()
        return self

    def __aiter__(self) -> AsyncIterator[Mapping[str, object]]:
        return self

    async def __anext__(self) -> Mapping[str, object]:
        return await self.messages.get()

    async def aclose(self) -> None:
        return None


class _ControlledRedis:
    def __init__(self) -> None:
        self.subscription = _ControlledPubSub()

    def pubsub(self) -> _ControlledPubSub:
        return self.subscription

    async def publish(self, channel: str, payload: str) -> None:
        del channel, payload


@pytest.mark.asyncio
async def test_listener_becomes_ready_only_after_subscription_acknowledgement() -> None:
    redis = _ControlledRedis()
    cache_resets = 0

    def invalidate_all() -> None:
        nonlocal cache_resets
        cache_resets += 1

    listener = AuditPolicyInvalidation(
        redis_client=redis,
        channel="deltallm:test:v1:audit-policy",
        invalidate_one=lambda _organization_id: None,
        invalidate_all=invalidate_all,
    )
    start_task = asyncio.create_task(listener.start(timeout_seconds=1))
    try:
        await asyncio.wait_for(redis.subscription.listen_entered.wait(), timeout=1)

        assert not start_task.done()
        assert listener.health.state is WorkerState.STARTING
        assert cache_resets == 0

        await redis.subscription.messages.put({"type": "subscribe"})
        await asyncio.wait_for(start_task, timeout=1)

        assert listener.health.state is WorkerState.READY
        assert cache_resets == 1
    finally:
        await listener.shutdown(deadline=asyncio.get_running_loop().time() + 1)
