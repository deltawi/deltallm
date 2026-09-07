import asyncio
import ast
from pathlib import Path
import json

import pytest

from src.metrics.route_group_cache import RouteGroupCacheFailureReason
from src.router.router import build_route_group_policies
from src.services.route_groups import RouteGroupRuntimeCache
from tests.services.test_route_groups import (
    _FakeRedis,
    _FakeRouteGroupRepository,
    _runtime_cache_key,
)


def test_cache_contract_structure_stays_bounded():
    root = Path(__file__).parents[2]
    source = (root / "src/services/route_group_cache_contract.py").read_text()
    assert len(source.splitlines()) < 500
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 80


INVALID_GROUP_FIELDS = [
    {"mode": "unknown"},
    {"strategy": "unknown"},
    {"selector": {"kind": "llm-tier"}, "policy_semantics_version": 3},
    {"policy_json": {"selector": {"kind": "llm-tier"}}, "policy_semantics_version": 3},
    {"context": {"mode": ["invalid"]}},
    {"context": {"mode": "invalid"}},
    {"context": {"unknown_capacity": "invalid"}},
    {"context": {"default_output_tokens": True}},
    {"context": {"safety_margin_tokens": -1}},
    {"timeouts": {"global_ms": "invalid"}},
    {"timeouts": {"global_ms": False}},
    {"timeouts": {"global_ms": 0}},
    {"timeouts": {"global_seconds": float("inf")}},
    {"timeouts": {"global_seconds": "NaN"}},
    {"timeouts": {"global_seconds": -1}},
    {"retry": {"max_attempts": "invalid"}},
    {"retry": {"max_attempts": True}},
    {"retry": {"max_attempts": -1}},
    {"retry": {"retryable_error_classes": "timeout"}},
    {"retry": {"retryable_error_classes": [1]}},
    {"retry": {"retryable_error_classes": ["unknown"]}},
    {"members": [{"deployment_id": "dep-a", "weight": 0}]},
    {"members": [{"deployment_id": "dep-a", "priority": -1}]},
]


def invalid_envelope(fields):
    return json.dumps(
        {
            "schema_version": 2,
            "selector_activation_state": "inactive",
            "revision": 1,
            "database_initialized": True,
            "groups": [{"key": "corrupt", "members": [], **fields}],
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", INVALID_GROUP_FIELDS)
async def test_malformed_owned_fields_reload_entire_snapshot_and_repair(fields, caplog):
    redis = _FakeRedis()
    redis.values[_runtime_cache_key(1)] = invalid_envelope(fields)
    repository = _FakeRouteGroupRepository([{"key": "durable", "members": []}])
    cache = RouteGroupRuntimeCache(redis)
    snapshot, source = await cache.get_snapshot(repository)
    assert source == "db"
    assert snapshot.groups == repository.groups
    assert repository.calls == 1
    assert redis.setex_calls == 1
    assert any(record.cache_miss_reason == "invalid_payload" for record in caplog.records)
    assert cache.last_failure_reason is None
    second, source = await RouteGroupRuntimeCache(redis).get_snapshot(repository)
    assert source == "l2_cache"
    assert second == snapshot
    assert repository.calls == 1


@pytest.mark.asyncio
async def test_valid_historical_nested_settings_keep_effective_behavior_and_opaque_fields():
    group = {
        "key": "history",
        "mode": "chat",
        "strategy": "weighted",
        "members": [],
        "timeouts": {"global_ms": "1000", "opaque": {"server": 1}},
        "retry": {"max_attempts": "2", "retryable_error_classes": ["timeout"], "opaque": 1},
        "context": {"default_output_tokens": "123", "opaque": {"server": 1}},
    }
    repository = _FakeRouteGroupRepository([group])
    redis = _FakeRedis()
    cache = RouteGroupRuntimeCache(redis)
    durable, _ = await cache.get_snapshot(repository)
    cached, source = await RouteGroupRuntimeCache(redis).get_snapshot(repository)
    assert source == "l2_cache"
    assert cached.groups == durable.groups == [group]
    assert build_route_group_policies(cached.groups) == build_route_group_policies(durable.groups)


@pytest.mark.asyncio
async def test_failed_repair_is_redacted_degraded_and_does_not_fail_durable_read(caplog):
    redis = _FakeRedis()
    redis.fail_setex = True
    redis.values[_runtime_cache_key(1)] = invalid_envelope({"context": {"mode": ["private-input"]}})
    repository = _FakeRouteGroupRepository([{"key": "durable", "members": []}])
    cache = RouteGroupRuntimeCache(redis)
    _, source = await cache.get_snapshot(repository)
    assert source == "db"
    assert cache.last_failure_reason is RouteGroupCacheFailureReason.WRITE_UNAVAILABLE
    assert "private-input" not in caplog.text
    redis.fail_setex = False
    await cache.invalidate()
    await cache.get_snapshot(repository)
    assert cache.last_failure_reason is None


@pytest.mark.asyncio
async def test_l1_l2_and_corrupt_miss_dependency_call_budget():
    class Repository(_FakeRouteGroupRepository):
        revision_reads = 0

        async def get_runtime_revision(self):
            self.revision_reads += 1
            return await super().get_runtime_revision()

    class Redis(_FakeRedis):
        reads = 0

        async def get(self, key):
            self.reads += 1
            return await super().get(key)

    repository = Repository([{"key": "budget", "members": []}])
    redis = Redis()
    cache = RouteGroupRuntimeCache(redis)
    await cache.get_snapshot(repository)
    assert (repository.revision_reads, repository.calls, redis.reads, redis.setex_calls) == (
        1,
        1,
        1,
        1,
    )
    snapshot, source = await cache.get_snapshot(repository)
    assert source == "l1_cache"
    assert (repository.revision_reads, repository.calls, redis.reads, redis.setex_calls) == (
        2,
        1,
        1,
        1,
    )
    snapshot.groups.clear()
    await cache.invalidate()
    snapshot, source = await cache.get_snapshot(repository)
    assert source == "l2_cache" and snapshot.groups
    assert (repository.revision_reads, repository.calls, redis.reads, redis.setex_calls) == (
        3,
        1,
        2,
        1,
    )
    await cache.invalidate()
    redis.values[_runtime_cache_key(1)] = "invalid"
    await cache.get_snapshot(repository)
    assert (repository.revision_reads, repository.calls, redis.reads, redis.setex_calls) == (
        4,
        2,
        3,
        2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "setex"])
async def test_cache_cancellation_propagates_without_retry(operation):
    class Redis(_FakeRedis):
        async def get(self, key):
            if operation == "get":
                raise asyncio.CancelledError
            return await super().get(key)

        async def setex(self, key, ttl, value):
            raise asyncio.CancelledError

    redis = Redis()
    repository = _FakeRouteGroupRepository([{"key": "cancel", "members": []}])
    with pytest.raises(asyncio.CancelledError):
        await RouteGroupRuntimeCache(redis).get_snapshot(repository)
    assert repository.calls == (0 if operation == "get" else 1)
    assert not redis.values
