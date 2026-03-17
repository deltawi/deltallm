from __future__ import annotations

import asyncio

import pytest

from src.services.governance_invalidation import GovernanceInvalidationService


class _FakeReloadService:
    def __init__(self) -> None:
        self.reload_calls = 0

    async def reload(self) -> None:
        self.reload_calls += 1


class _FakeInvalidateService:
    def __init__(self) -> None:
        self.invalidate_calls = 0

    async def invalidate_all(self) -> None:
        self.invalidate_calls += 1


class _FakePubSub:
    def __init__(self, broker: "_FakeRedis") -> None:
        self.broker = broker
        self.queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.channel: str | None = None

    async def subscribe(self, channel: str) -> None:
        self.channel = channel
        self.broker.subscribers.append(self)

    def listen(self):  # noqa: ANN201
        return self

    def __aiter__(self):  # noqa: ANN204
        return self

    async def __anext__(self) -> dict[str, object]:
        message = await self.queue.get()
        if message.get("type") == "stop":
            raise StopAsyncIteration
        return message

    async def unsubscribe(self, channel: str) -> None:
        del channel
        self.broker.subscribers = [item for item in self.broker.subscribers if item is not self]

    async def close(self) -> None:
        await self.queue.put({"type": "stop"})


class _FakeRedis:
    def __init__(self) -> None:
        self.subscribers: list[_FakePubSub] = []
        self.messages: list[tuple[str, str]] = []

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self)

    async def publish(self, channel: str, payload: str) -> None:
        self.messages.append((channel, payload))
        for subscriber in list(self.subscribers):
            await subscriber.queue.put({"type": "message", "data": payload})


@pytest.mark.asyncio
async def test_governance_invalidation_service_notifies_other_instances() -> None:
    redis = _FakeRedis()
    local_callable = _FakeReloadService()
    local_registry = _FakeInvalidateService()
    local_mcp = _FakeReloadService()
    remote_callable = _FakeReloadService()
    remote_registry = _FakeInvalidateService()
    remote_mcp = _FakeReloadService()

    local = GovernanceInvalidationService(
        redis_client=redis,
        callable_target_grant_service=local_callable,
        mcp_registry_service=local_registry,
        mcp_governance_service=local_mcp,
    )
    remote = GovernanceInvalidationService(
        redis_client=redis,
        callable_target_grant_service=remote_callable,
        mcp_registry_service=remote_registry,
        mcp_governance_service=remote_mcp,
    )
    await local.start()
    await remote.start()

    await local.notify("callable_target", "mcp")
    await asyncio.sleep(0.1)

    assert local_callable.reload_calls == 0
    assert local_registry.invalidate_calls == 0
    assert local_mcp.reload_calls == 0
    assert remote_callable.reload_calls == 1
    assert remote_registry.invalidate_calls == 1
    assert remote_mcp.reload_calls == 1

    await local.close()
    await remote.close()


@pytest.mark.asyncio
async def test_governance_invalidation_service_can_apply_local_invalidations_without_redis() -> None:
    callable_target = _FakeReloadService()
    mcp_registry = _FakeInvalidateService()
    mcp_governance = _FakeReloadService()
    service = GovernanceInvalidationService(
        redis_client=None,
        callable_target_grant_service=callable_target,
        mcp_registry_service=mcp_registry,
        mcp_governance_service=mcp_governance,
    )

    await service.invalidate_local("callable_target", "mcp")

    assert callable_target.reload_calls == 1
    assert mcp_registry.invalidate_calls == 1
    assert mcp_governance.reload_calls == 1


@pytest.mark.asyncio
async def test_governance_invalidation_service_coalesces_remote_invalidations() -> None:
    redis = _FakeRedis()
    remote_callable = _FakeReloadService()
    remote_registry = _FakeInvalidateService()
    remote_mcp = _FakeReloadService()

    local = GovernanceInvalidationService(redis_client=redis, remote_apply_delay_seconds=0.01)
    remote = GovernanceInvalidationService(
        redis_client=redis,
        callable_target_grant_service=remote_callable,
        mcp_registry_service=remote_registry,
        mcp_governance_service=remote_mcp,
        remote_apply_delay_seconds=0.01,
    )
    await local.start()
    await remote.start()

    await local.notify("callable_target")
    await local.notify("mcp")
    await asyncio.sleep(0.05)

    assert remote_callable.reload_calls == 1
    assert remote_registry.invalidate_calls == 1
    assert remote_mcp.reload_calls == 1

    await local.close()
    await remote.close()
