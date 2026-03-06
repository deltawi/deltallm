from __future__ import annotations

import pytest

from src.config import AppConfig
from src.services.route_groups import RouteGroupRuntimeCache, load_route_groups


class _FakeRouteGroupRepository:
    def __init__(self, groups: list[dict] | None = None, *, fail: bool = False) -> None:
        self.groups = groups or []
        self.fail = fail
        self.calls = 0

    async def list_runtime_groups(self) -> list[dict]:
        if self.fail:
            raise RuntimeError("db unavailable")
        self.calls += 1
        return list(self.groups)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.setex_calls = 0
        self.delete_calls = 0

    async def get(self, key: str):  # noqa: ANN201
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        del ttl
        self.values[key] = value
        self.setex_calls += 1

    async def delete(self, key: str) -> None:
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
async def test_load_route_groups_uses_l1_cache_on_subsequent_calls():
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repo = _FakeRouteGroupRepository(
        groups=[{"key": "db-group", "enabled": True, "strategy": "weighted", "members": [{"deployment_id": "db-dep", "enabled": True}]}]
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
        groups=[{"key": "db-group", "enabled": True, "strategy": "weighted", "members": [{"deployment_id": "db-dep", "enabled": True}]}]
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
        groups=[{"key": "db-group", "enabled": True, "strategy": "weighted", "members": [{"deployment_id": "db-dep", "enabled": True}]}]
    )
    redis = _FakeRedis()
    cache = RouteGroupRuntimeCache(redis, l1_ttl_seconds=60, l2_ttl_seconds=300)

    await load_route_groups(repo, cfg, route_group_cache=cache)
    await cache.invalidate()
    _, source = await load_route_groups(repo, cfg, route_group_cache=cache)

    assert source == "db"
    assert repo.calls == 2
    assert redis.delete_calls == 1
