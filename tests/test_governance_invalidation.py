from __future__ import annotations

import asyncio

import pytest

from src.services.governance_invalidation import (
    GovernanceInvalidationApplyError,
    GovernanceInvalidationService,
)


class _FakeReloadService:
    def __init__(self, *, fail_times: int = 0, mode: str = "shadow") -> None:
        self.fail_times = fail_times
        self.mode = mode
        self.reload_calls = 0

    async def reload(self) -> None:
        self.reload_calls += 1
        if self.reload_calls <= self.fail_times:
            raise RuntimeError("reload unavailable")


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


class _FailingRedis(_FakeRedis):
    async def publish(self, channel: str, payload: str) -> None:
        del channel, payload
        raise RuntimeError("redis unavailable")


@pytest.mark.asyncio
async def test_governance_invalidation_service_notifies_other_instances() -> None:
    redis = _FakeRedis()
    local_callable = _FakeReloadService()
    local_tier = _FakeReloadService()
    local_registry = _FakeInvalidateService()
    local_mcp = _FakeReloadService()
    remote_callable = _FakeReloadService()
    remote_tier = _FakeReloadService()
    remote_registry = _FakeInvalidateService()
    remote_mcp = _FakeReloadService()

    local = GovernanceInvalidationService(
        redis_client=redis,
        callable_target_grant_service=local_callable,
        tier_policy_service=local_tier,
        mcp_registry_service=local_registry,
        mcp_governance_service=local_mcp,
    )
    remote = GovernanceInvalidationService(
        redis_client=redis,
        callable_target_grant_service=remote_callable,
        tier_policy_service=remote_tier,
        mcp_registry_service=remote_registry,
        mcp_governance_service=remote_mcp,
    )
    await local.start()
    await remote.start()

    await local.notify("callable_target", "mcp", "tier_policy")
    await asyncio.sleep(0.1)

    assert local_callable.reload_calls == 0
    assert local_tier.reload_calls == 0
    assert local_registry.invalidate_calls == 0
    assert local_mcp.reload_calls == 0
    assert remote_callable.reload_calls == 1
    assert remote_tier.reload_calls == 1
    assert remote_registry.invalidate_calls == 1
    assert remote_mcp.reload_calls == 1

    await local.close()
    await remote.close()


@pytest.mark.asyncio
async def test_governance_invalidation_service_returns_false_when_publish_fails() -> None:
    service = GovernanceInvalidationService(redis_client=_FailingRedis())

    assert await service.notify("tier_policy") is False


@pytest.mark.asyncio
async def test_governance_invalidation_service_can_apply_local_invalidations_without_redis() -> (
    None
):
    callable_target = _FakeReloadService()
    tier_policy = _FakeReloadService()
    mcp_registry = _FakeInvalidateService()
    mcp_governance = _FakeReloadService()
    service = GovernanceInvalidationService(
        redis_client=None,
        callable_target_grant_service=callable_target,
        tier_policy_service=tier_policy,
        mcp_registry_service=mcp_registry,
        mcp_governance_service=mcp_governance,
    )

    await service.invalidate_local("callable_target", "mcp", "tier_policy")

    assert callable_target.reload_calls == 1
    assert tier_policy.reload_calls == 1
    assert mcp_registry.invalidate_calls == 1
    assert mcp_governance.reload_calls == 1


@pytest.mark.asyncio
async def test_governance_invalidation_service_applies_remaining_local_targets_after_failure() -> (
    None
):
    callable_target = _FakeReloadService(fail_times=1)
    tier_policy = _FakeReloadService()
    service = GovernanceInvalidationService(
        redis_client=None,
        callable_target_grant_service=callable_target,
        tier_policy_service=tier_policy,
    )

    with pytest.raises(GovernanceInvalidationApplyError) as exc_info:
        await service.invalidate_local("callable_target", "tier_policy")

    assert exc_info.value.failed_targets == ("callable_target",)
    assert callable_target.reload_calls == 1
    assert tier_policy.reload_calls == 1
    await service.close()


@pytest.mark.asyncio
async def test_governance_invalidation_service_retries_failed_local_targets() -> None:
    tier_policy = _FakeReloadService(fail_times=1)
    service = GovernanceInvalidationService(
        redis_client=None,
        tier_policy_service=tier_policy,
        remote_retry_delay_seconds=0.01,
    )

    with pytest.raises(GovernanceInvalidationApplyError) as exc_info:
        await service.invalidate_local("tier_policy")
    for _ in range(20):
        if tier_policy.reload_calls >= 2:
            break
        await asyncio.sleep(0.02)

    assert exc_info.value.failed_targets == ("tier_policy",)
    assert tier_policy.reload_calls == 2
    await service.close()


@pytest.mark.asyncio
async def test_governance_invalidation_service_skips_disabled_tier_policy() -> None:
    tier_policy = _FakeReloadService(mode="disabled")
    service = GovernanceInvalidationService(
        redis_client=None,
        tier_policy_service=tier_policy,
    )

    await service.invalidate_local("tier_policy")

    assert tier_policy.reload_calls == 0


@pytest.mark.asyncio
async def test_governance_invalidation_service_coalesces_remote_invalidations() -> None:
    redis = _FakeRedis()
    remote_callable = _FakeReloadService()
    remote_tier = _FakeReloadService()
    remote_registry = _FakeInvalidateService()
    remote_mcp = _FakeReloadService()

    local = GovernanceInvalidationService(redis_client=redis, remote_apply_delay_seconds=0.01)
    remote = GovernanceInvalidationService(
        redis_client=redis,
        callable_target_grant_service=remote_callable,
        tier_policy_service=remote_tier,
        mcp_registry_service=remote_registry,
        mcp_governance_service=remote_mcp,
        remote_apply_delay_seconds=0.01,
    )
    await local.start()
    await remote.start()

    await local.notify("callable_target")
    await local.notify("tier_policy")
    await local.notify("mcp")
    await asyncio.sleep(0.05)

    assert remote_callable.reload_calls == 1
    assert remote_tier.reload_calls == 1
    assert remote_registry.invalidate_calls == 1
    assert remote_mcp.reload_calls == 1

    await local.close()
    await remote.close()


@pytest.mark.asyncio
async def test_governance_invalidation_service_retries_failed_remote_targets() -> None:
    redis = _FakeRedis()
    remote_tier = _FakeReloadService(fail_times=1)

    local = GovernanceInvalidationService(redis_client=redis, remote_apply_delay_seconds=0.01)
    remote = GovernanceInvalidationService(
        redis_client=redis,
        tier_policy_service=remote_tier,
        remote_apply_delay_seconds=0.01,
        remote_retry_delay_seconds=0.01,
    )
    await local.start()
    await remote.start()

    assert await local.notify("tier_policy") is True
    for _ in range(20):
        if remote_tier.reload_calls >= 2:
            break
        await asyncio.sleep(0.02)

    assert remote_tier.reload_calls == 2

    await local.close()
    await remote.close()
