from __future__ import annotations

import asyncio
from dataclasses import fields
from pathlib import Path

import pytest
import yaml
from redis.exceptions import ConnectionError as RedisConnectionError

from src.config import GeneralSettings, Settings
from src.config_runtime.dynamic import DynamicConfigManager, DynamicConfigRestartRequiredError
from src.redis_runtime import AllocatedRedisPool, RedisLimits, build_redis_client

pytestmark = [pytest.mark.hermetic, pytest.mark.asyncio]


class FakeConnection:
    release = None

    def __init__(self, **kwargs):
        self.retry = kwargs.get("retry")

    async def connect(self):
        if self.release is not None:
            await self.release.wait()

    async def can_read_destructive(self):
        return False

    async def disconnect(self, **kwargs):
        pass

    async def re_auth(self):
        pass


async def test_connection_lock_cannot_accumulate_unbounded_callers():
    barrier = asyncio.Event()

    class SlowConnection(FakeConnection):
        release = barrier

    pool = AllocatedRedisPool(
        allocation="critical",
        acquisition_timeout=1,
        max_connections=4,
        connection_class=SlowConnection,
    )
    tasks = [asyncio.create_task(pool.get_connection()) for _ in range(100)]
    try:
        async with asyncio.timeout(1):
            while sum(t.done() for t in tasks) != 96:
                await asyncio.sleep(0)
        assert pool.gate.active == 4 and pool.gate.waiters == 0
        for task in tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(r, RedisConnectionError) for r in results) == 96
        assert pool.gate.active == 0
        barrier.set()
        connection = await pool.get_connection()
        await pool.release(connection)
        assert pool.gate.active == 0
    finally:
        barrier.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pool.aclose()


async def test_acquisition_timeout_reclaims_driver_connection_and_slot():
    class SlowConnection(FakeConnection):
        release = asyncio.Event()

    pool = AllocatedRedisPool(
        allocation="critical",
        acquisition_timeout=0.01,
        max_connections=1,
        connection_class=SlowConnection,
    )
    try:
        with pytest.raises(RedisConnectionError, match="deadline"):
            await pool.get_connection()
        assert pool.gate.active == 0
        assert not pool._in_use_connections
    finally:
        await pool.aclose()


async def test_bulk_exhaustion_does_not_consume_critical_allocation():
    pools = [
        AllocatedRedisPool(
            allocation=allocation,
            acquisition_timeout=1,
            max_connections=1,
            connection_class=FakeConnection,
        )
        for allocation in ("critical", "bulk")
    ]
    critical, bulk = pools
    try:
        held = await bulk.get_connection()
        with pytest.raises(RedisConnectionError, match="full"):
            await bulk.get_connection()
        live = await critical.get_connection()
        assert critical.gate.active == bulk.gate.active == 1
        await critical.release(live)
        await bulk.release(held)
    finally:
        for pool in pools:
            await pool.aclose()
    with pytest.raises(RedisConnectionError, match="closed"):
        await critical.get_connection()


async def test_url_cannot_disable_typed_limits_and_bulk_endpoint_is_independent():
    settings = Settings(
        redis_url="rediss://fixture:fixture@critical:6380/2?max_connections=999999&socket_timeout=0&retry_on_timeout=true"
    )
    general = GeneralSettings(redis_bulk_url="redis://bulk:6379/3")
    critical = build_redis_client(settings, general, allocation="critical")
    bulk = build_redis_client(settings, general, allocation="bulk")
    try:
        assert critical.connection_pool.max_connections == 64
        assert bulk.connection_pool.max_connections == 16
        options = critical.connection_pool.connection_kwargs
        assert options["host"] == "critical" and options["db"] == 2
        assert options["socket_timeout"] == options["socket_connect_timeout"] == 1
        assert options["retry_on_timeout"] is False
        assert options["retry"]._retries == 0
        assert bulk.connection_pool.connection_kwargs["host"] == "bulk"
    finally:
        await critical.aclose()
        await bulk.aclose()
    assert critical.connection_pool.closed and bulk.connection_pool.closed


@pytest.mark.parametrize("name", [f.name for f in fields(RedisLimits())])
async def test_limits_config_defaults_environment_and_restart(name, monkeypatch):
    field = "redis_" + name
    defaults = RedisLimits()
    default = getattr(defaults, name)
    for model in (GeneralSettings, Settings):
        assert getattr(model(), field) == default
        with pytest.raises(ValueError):
            model.model_validate({field: 0})
    for path, keys in (
        ("config.example.yaml", ["general_settings"]),
        ("deploy/kubernetes/helm/values.yaml", ["config", "general_settings"]),
    ):
        data = yaml.safe_load(Path(path).read_text())
        for key in keys:
            data = data[key]
        assert data[field] == default
    monkeypatch.setenv("DELTALLM_" + field.upper(), str(default * 2))
    assert getattr(RedisLimits.from_settings(GeneralSettings(), Settings()), name) == default * 2
    assert (
        RedisLimits.from_settings(GeneralSettings.model_validate({field: default}), Settings())
        == defaults
    )
    manager = DynamicConfigManager(None, None, {})
    await manager.initialize()
    try:
        with pytest.raises(DynamicConfigRestartRequiredError, match=field):
            await manager.update_config({"general_settings": {field: default}}, updated_by="test")
    finally:
        await manager.close()


async def test_existing_endpoint_selection_is_preserved_while_durable_limits_apply():
    initial = GeneralSettings(redis_url="redis://file-redis:6379/0")
    durable = GeneralSettings(
        redis_url="redis://old-durable-value:6379/0", redis_critical_max_connections=7
    )
    client = build_redis_client(
        Settings(), durable, allocation="critical", endpoint_settings=initial
    )
    try:
        assert client.connection_pool.connection_kwargs["host"] == "file-redis"
        assert client.connection_pool.max_connections == 7
    finally:
        await client.aclose()
