from __future__ import annotations

import asyncio

import pytest

from src.router.runtime_generation import RoutingRuntimeAppliedState
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
        self.broker.subscribe_count += 1
        if self.broker.subscribe_count >= 2:
            self.broker.resubscribed.set()

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
        self.subscribe_count = 0
        self.resubscribed = asyncio.Event()

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
async def test_governance_invalidation_listener_restarts_after_disconnect() -> None:
    redis = _FakeRedis()
    service = GovernanceInvalidationService(redis_client=redis)
    await service.start()
    try:
        await redis.subscribers[0].queue.put({"type": "stop"})
        await asyncio.wait_for(redis.resubscribed.wait(), timeout=1)

        assert service._pubsub_task is not None
        assert not service._pubsub_task.done()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_governance_invalidation_listener_recovers_when_pubsub_factory_fails() -> None:
    class FactoryFailRedis(_FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.factory_calls = 0
            self.recovered = asyncio.Event()

        def pubsub(self) -> _FakePubSub:
            self.factory_calls += 1
            if self.factory_calls == 1:
                raise RuntimeError("pubsub factory unavailable")
            self.recovered.set()
            return super().pubsub()

    redis = FactoryFailRedis()
    service = GovernanceInvalidationService(redis_client=redis)
    await service.start()
    try:
        await asyncio.wait_for(redis.recovered.wait(), timeout=1)

        assert redis.subscribe_count == 1
        assert service._pubsub_task is not None
        assert not service._pubsub_task.done()
    finally:
        await service.close()


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
    route_group_reload_calls = 0

    async def reload_route_groups() -> None:
        nonlocal route_group_reload_calls
        route_group_reload_calls += 1

    local = GovernanceInvalidationService(redis_client=redis, remote_apply_delay_seconds=0.01)
    remote = GovernanceInvalidationService(
        redis_client=redis,
        callable_target_grant_service=remote_callable,
        tier_policy_service=remote_tier,
        mcp_registry_service=remote_registry,
        mcp_governance_service=remote_mcp,
        route_group_reload=reload_route_groups,
        remote_apply_delay_seconds=0.01,
    )
    await local.start()
    await remote.start()

    await local.notify("callable_target")
    await local.notify("tier_policy")
    await local.notify("mcp")
    await local.notify("route_groups")
    await asyncio.sleep(0.05)

    assert remote_callable.reload_calls == 0
    assert remote_tier.reload_calls == 1
    assert remote_registry.invalidate_calls == 1
    assert remote_mcp.reload_calls == 1
    assert route_group_reload_calls == 1

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


@pytest.mark.asyncio
async def test_route_group_revision_poll_recovers_without_pubsub() -> None:
    class RevisionSource:
        revision = 1

        async def get_runtime_revision(self) -> int:
            return self.revision

    source = RevisionSource()
    applied_revision = 1
    reloaded = asyncio.Event()

    async def reload_route_groups() -> None:
        nonlocal applied_revision
        applied_revision = source.revision
        reloaded.set()

    service = GovernanceInvalidationService(
        redis_client=None,
        route_group_reload=reload_route_groups,
        route_group_revision_source=source,
        route_group_applied_revision=lambda: applied_revision,
        route_group_poll_interval_seconds=0.01,
    )
    await service.start()
    try:
        source.revision = 2
        await asyncio.wait_for(reloaded.wait(), timeout=1)

        assert applied_revision == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_route_group_revision_poll_recovers_degraded_revision_zero() -> None:
    class RevisionSource:
        async def get_runtime_revision(self) -> int:
            return 0

    applied_state = RoutingRuntimeAppliedState(
        revision=0,
        source="config_db_unavailable",
        requires_reconciliation=True,
    )
    reloaded = asyncio.Event()

    async def reload_route_groups() -> None:
        nonlocal applied_state
        applied_state = RoutingRuntimeAppliedState(
            revision=0,
            source="database",
            requires_reconciliation=False,
        )
        reloaded.set()

    service = GovernanceInvalidationService(
        redis_client=None,
        route_group_reload=reload_route_groups,
        route_group_revision_source=RevisionSource(),
        routing_applied_state=lambda: applied_state,
        route_group_poll_interval_seconds=0.01,
    )
    await service.start()
    try:
        await asyncio.wait_for(reloaded.wait(), timeout=1)
        assert applied_state.requires_reconciliation is False
    finally:
        await service.close()
