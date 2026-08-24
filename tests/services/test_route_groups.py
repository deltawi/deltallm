from __future__ import annotations

import asyncio

import pytest

from src.config import AppConfig
from src.db.route_groups import RouteGroupRuntimeSnapshot
from src.services.route_groups import (
    RouteGroupRuntimeCache,
    StaleRouteGroupSnapshotError,
    load_route_group_snapshot,
    load_route_group_snapshot_result,
    load_route_groups,
)


class _FakeRouteGroupRepository:
    def __init__(self, groups: list[dict] | None = None, *, fail: bool = False) -> None:
        self.groups = groups or []
        self.database_initialized = bool(self.groups)
        self.fail = fail
        self.calls = 0
        self.revision = 1

    async def get_runtime_revision(self) -> int:
        if self.fail:
            raise RuntimeError("db unavailable")
        return self.revision

    async def load_runtime_snapshot(self) -> RouteGroupRuntimeSnapshot:
        if self.fail:
            raise RuntimeError("db unavailable")
        self.calls += 1
        return RouteGroupRuntimeSnapshot(
            self.revision,
            list(self.groups),
            database_initialized=self.database_initialized,
        )


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.setex_calls = 0
        self.delete_calls = 0
        self.fail_delete = False
        self.fail_setex = False

    async def get(self, key: str):  # noqa: ANN201
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        del ttl
        if self.fail_setex:
            raise RuntimeError("redis unavailable")
        self.values[key] = value
        self.setex_calls += 1

    async def delete(self, key: str) -> None:
        if self.fail_delete:
            raise RuntimeError("redis unavailable")
        self.values.pop(key, None)
        self.delete_calls += 1


@pytest.mark.asyncio
async def test_load_route_groups_prefers_db_records_when_available():
    cfg = AppConfig.model_validate(
        {
            "router_settings": {
                "route_groups": [
                    {
                        "key": "cfg-group",
                        "members": [{"deployment_id": "cfg-dep"}],
                    }
                ]
            }
        }
    )
    repo = _FakeRouteGroupRepository(
        groups=[
            {
                "key": "db-group",
                "enabled": True,
                "strategy": "weighted",
                "members": [{"deployment_id": "db-dep", "enabled": True}],
            }
        ]
    )

    groups, source = await load_route_groups(repo, cfg)

    assert source == "db"
    assert len(groups) == 1
    assert groups[0]["key"] == "db-group"


@pytest.mark.asyncio
async def test_load_route_groups_falls_back_to_config_on_db_error():
    cfg = AppConfig.model_validate(
        {
            "router_settings": {
                "route_groups": [
                    {
                        "key": "cfg-group",
                        "members": [{"deployment_id": "cfg-dep"}],
                    }
                ]
            }
        }
    )
    repo = _FakeRouteGroupRepository(fail=True)

    groups, source = await load_route_groups(repo, cfg)

    assert source == "config"
    assert len(groups) == 1
    assert groups[0]["key"] == "cfg-group"


@pytest.mark.asyncio
async def test_route_group_load_marks_database_fallback_for_reconciliation():
    cfg = AppConfig.model_validate(
        {"router_settings": {"route_groups": [{"key": "cfg-group", "members": []}]}}
    )

    result = await load_route_group_snapshot_result(
        _FakeRouteGroupRepository(fail=True),
        cfg,
    )

    assert result.source == "config_db_unavailable"
    assert result.snapshot.revision == 0
    assert result.database_available is False
    assert result.requires_reconciliation is True


@pytest.mark.asyncio
async def test_runtime_reload_keeps_authoritative_empty_database_snapshot():
    cfg = AppConfig.model_validate(
        {
            "router_settings": {
                "route_groups": [{"key": "config-only", "members": [{"deployment_id": "cfg-dep"}]}]
            }
        }
    )
    repo = _FakeRouteGroupRepository()

    snapshot, source = await load_route_group_snapshot(
        repo,
        cfg,
        allow_config_fallback=False,
    )

    assert source == "db"
    assert snapshot.groups == []


@pytest.mark.asyncio
async def test_startup_keeps_intentionally_emptied_database_snapshot():
    cfg = AppConfig.model_validate(
        {
            "router_settings": {
                "route_groups": [{"key": "config-only", "members": [{"deployment_id": "cfg-dep"}]}]
            }
        }
    )
    repo = _FakeRouteGroupRepository()
    repo.revision = 2
    repo.database_initialized = True

    snapshot, source = await load_route_group_snapshot(repo, cfg)

    assert source == "db"
    assert snapshot.revision == 2
    assert snapshot.groups == []


@pytest.mark.asyncio
async def test_unrelated_routing_revision_does_not_hide_config_route_groups():
    cfg = AppConfig.model_validate(
        {
            "router_settings": {
                "route_groups": [{"key": "config-only", "members": [{"deployment_id": "cfg-dep"}]}]
            }
        }
    )
    repo = _FakeRouteGroupRepository()
    repo.revision = 7
    repo.database_initialized = False

    snapshot, source = await load_route_group_snapshot(repo, cfg)

    assert source == "config"
    assert snapshot.revision == 7
    assert [group["key"] for group in snapshot.groups] == ["config-only"]


@pytest.mark.asyncio
async def test_load_route_groups_uses_l1_cache_on_subsequent_calls():
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repo = _FakeRouteGroupRepository(
        groups=[
            {
                "key": "db-group",
                "enabled": True,
                "strategy": "weighted",
                "members": [{"deployment_id": "db-dep", "enabled": True}],
            }
        ]
    )
    redis = _FakeRedis()
    cache = RouteGroupRuntimeCache(redis, l1_ttl_seconds=60, l2_ttl_seconds=300)

    first_groups, first_source = await load_route_groups(repo, cfg, route_group_cache=cache)
    second_groups, second_source = await load_route_groups(repo, cfg, route_group_cache=cache)

    assert first_source == "db"
    assert second_source == "l1_cache"
    assert first_groups[0]["key"] == "db-group"
    assert second_groups[0]["key"] == "db-group"
    assert repo.calls == 1
    assert redis.setex_calls == 1


@pytest.mark.asyncio
async def test_load_route_groups_uses_l2_cache_after_l1_expiry():
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repo = _FakeRouteGroupRepository(
        groups=[
            {
                "key": "db-group",
                "enabled": True,
                "strategy": "weighted",
                "members": [{"deployment_id": "db-dep", "enabled": True}],
            }
        ]
    )
    redis = _FakeRedis()
    cache = RouteGroupRuntimeCache(redis, l1_ttl_seconds=1, l2_ttl_seconds=300)

    await load_route_groups(repo, cfg, route_group_cache=cache)
    cache._l1_entry = None
    groups, source = await load_route_groups(repo, cfg, route_group_cache=cache)

    assert source == "l2_cache"
    assert groups[0]["key"] == "db-group"
    assert repo.calls == 1


@pytest.mark.asyncio
async def test_route_group_cache_invalidation_forces_db_reload():
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repo = _FakeRouteGroupRepository(
        groups=[
            {
                "key": "db-group",
                "enabled": True,
                "strategy": "weighted",
                "members": [{"deployment_id": "db-dep", "enabled": True}],
            }
        ]
    )
    redis = _FakeRedis()
    cache = RouteGroupRuntimeCache(redis, l1_ttl_seconds=60, l2_ttl_seconds=300)

    await load_route_groups(repo, cfg, route_group_cache=cache)
    repo.revision = 2
    await cache.invalidate(required_revision=2)
    _, source = await load_route_groups(repo, cfg, route_group_cache=cache)

    assert source == "db"
    assert repo.calls == 2
    assert redis.delete_calls == 0


@pytest.mark.asyncio
async def test_route_group_cache_invalidation_bypasses_stale_l2_during_redis_outage():
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repo = _FakeRouteGroupRepository(
        groups=[{"key": "db-group-v1", "enabled": True, "members": []}]
    )
    redis = _FakeRedis()
    cache = RouteGroupRuntimeCache(redis, l1_ttl_seconds=60, l2_ttl_seconds=300)
    await load_route_groups(repo, cfg, route_group_cache=cache)
    repo.groups = [{"key": "db-group-v2", "enabled": True, "members": []}]
    repo.revision = 2
    redis.fail_delete = True

    assert await cache.invalidate(required_revision=2) is True
    groups, source = await load_route_groups(repo, cfg, route_group_cache=cache)

    assert source == "db"
    assert groups[0]["key"] == "db-group-v2"
    assert repo.calls == 2


@pytest.mark.asyncio
async def test_route_group_cache_forces_db_until_stale_l2_is_replaced():
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repo = _FakeRouteGroupRepository(
        groups=[{"key": "db-group-v1", "enabled": True, "members": []}]
    )
    redis = _FakeRedis()
    cache = RouteGroupRuntimeCache(redis, l1_ttl_seconds=60, l2_ttl_seconds=300)
    await load_route_groups(repo, cfg, route_group_cache=cache)
    repo.groups = [{"key": "db-group-v2", "enabled": True, "members": []}]
    repo.revision = 2
    redis.fail_delete = True
    redis.fail_setex = True

    assert await cache.invalidate(required_revision=2) is True
    first_groups, first_source = await load_route_groups(repo, cfg, route_group_cache=cache)
    redis.fail_setex = False
    second_groups, second_source = await load_route_groups(repo, cfg, route_group_cache=cache)

    assert first_source == "db"
    assert second_source == "l1_cache"
    assert first_groups[0]["key"] == "db-group-v2"
    assert second_groups[0]["key"] == "db-group-v2"
    assert repo.calls == 2


@pytest.mark.asyncio
async def test_two_replica_caches_reload_from_durable_state_after_invalidation():
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repo = _FakeRouteGroupRepository(
        groups=[{"key": "db-group-v1", "enabled": True, "members": []}]
    )
    redis = _FakeRedis()
    first = RouteGroupRuntimeCache(redis, l1_ttl_seconds=60, l2_ttl_seconds=300)
    second = RouteGroupRuntimeCache(redis, l1_ttl_seconds=60, l2_ttl_seconds=300)
    await load_route_groups(repo, cfg, route_group_cache=first)
    await load_route_groups(repo, cfg, route_group_cache=second)
    repo.groups = [{"key": "db-group-v2", "enabled": True, "members": []}]
    repo.revision = 2

    await first.invalidate(required_revision=2)
    first_groups, first_source = await load_route_groups(repo, cfg, route_group_cache=first)
    await second.invalidate(required_revision=2)
    second_groups, second_source = await load_route_groups(repo, cfg, route_group_cache=second)

    assert first_source == "db"
    assert second_source == "l2_cache"
    assert first_groups[0]["key"] == "db-group-v2"
    assert second_groups[0]["key"] == "db-group-v2"


@pytest.mark.asyncio
async def test_older_in_flight_database_load_cannot_overwrite_newer_generation():
    class BarrierRepository(_FakeRouteGroupRepository):
        def __init__(self) -> None:
            super().__init__([{"key": "route-v1", "members": []}])
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def load_runtime_snapshot(self) -> RouteGroupRuntimeSnapshot:
            revision = self.revision
            groups = list(self.groups)
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                await self.release_first.wait()
            return RouteGroupRuntimeSnapshot(revision, groups)

    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repository = BarrierRepository()
    cache = RouteGroupRuntimeCache(_FakeRedis())
    older = asyncio.create_task(cache.get_snapshot(repository))
    await repository.first_started.wait()

    repository.revision = 2
    repository.groups = [{"key": "route-v2", "members": []}]
    await cache.invalidate(required_revision=2)
    newest, newest_source = await load_route_groups(repository, cfg, route_group_cache=cache)
    repository.release_first.set()

    with pytest.raises(StaleRouteGroupSnapshotError):
        await older
    observed, observed_source = await load_route_groups(
        repository,
        cfg,
        route_group_cache=cache,
    )

    assert newest_source == "db"
    assert newest[0]["key"] == "route-v2"
    assert observed_source == "l1_cache"
    assert observed[0]["key"] == "route-v2"


@pytest.mark.asyncio
async def test_invalidation_during_redis_write_discards_loaded_snapshot():
    class BarrierRedis(_FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.write_started = asyncio.Event()
            self.release_write = asyncio.Event()

        async def setex(self, key: str, ttl: int, value: str) -> None:
            self.write_started.set()
            await self.release_write.wait()
            await super().setex(key, ttl, value)

    repository = _FakeRouteGroupRepository(groups=[{"key": "route-v1", "members": []}])
    redis = BarrierRedis()
    cache = RouteGroupRuntimeCache(redis)
    load = asyncio.create_task(cache.get_snapshot(repository))
    await redis.write_started.wait()

    repository.revision = 2
    await cache.invalidate(required_revision=2)
    redis.release_write.set()

    with pytest.raises(StaleRouteGroupSnapshotError):
        await load
