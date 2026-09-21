import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.config_runtime.dynamic import DynamicConfigManager
from src.services.governance_invalidation import GovernanceInvalidationService
from tests.config.test_dynamic import FakeDB

pytestmark = [pytest.mark.redis, pytest.mark.asyncio]


async def test_listener_reconnect_catches_up_durable_state_and_recovers_health():
    url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide test Redis")
        pytest.skip("test Redis required")
    name = "pr8-config-" + uuid4().hex
    redis = Redis.from_url(url, client_name=name, socket_timeout=1, max_connections=3)
    db = FakeDB()
    manager = DynamicConfigManager(db, redis, {}, poll_interval_seconds=None, defer_updates=True)
    applied = asyncio.Event()
    try:
        await manager.initialize()
        # No refresh is allowed to publish a new generation while bootstrap is
        # still constructing owners from its original snapshot.
        db.config_value = {"router_settings": {"timeout": 31}}
        await asyncio.sleep(0)
        assert manager.get_app_config().router_settings.timeout != 31
        manager.subscribe(lambda *_: applied.set())
        manager.activate_updates()
        async with asyncio.timeout(5):
            await applied.wait()
            while not manager.worker_health.ready:
                await asyncio.sleep(0.01)
        assert manager.get_app_config().router_settings.timeout == 31
        # Idle polling must not trip the one-second socket timeout.
        await asyncio.sleep(1.2)
        assert manager.worker_health.ready
        applied.clear()
        db.config_value = {"router_settings": {"timeout": 32}}
        clients = await redis.client_list()
        owned = [row for row in clients if row["name"] == name and "P" in row["flags"]]
        assert len(owned) == 1
        await redis.client_kill_filter(_id=owned[0]["id"])
        async with asyncio.timeout(5):
            while manager.worker_health.ready:
                await asyncio.sleep(0.01)
            await applied.wait()
            while not manager.worker_health.ready:
                await asyncio.sleep(0.01)
        assert manager.get_app_config().router_settings.timeout == 32
    finally:
        await manager.close()
        await redis.aclose()


async def test_policy_listener_stays_unready_until_missed_changes_are_refreshed():
    url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide test Redis")
        pytest.skip("test Redis required")
    name = "pr8-governance-" + uuid4().hex
    redis = Redis.from_url(url, client_name=name, socket_timeout=1, max_connections=3)
    gate, entered = asyncio.Event(), asyncio.Event()
    gate.set()
    revision = 1
    observed = 0

    async def reload_policy():
        nonlocal observed
        entered.set()
        await gate.wait()
        observed = revision

    service = GovernanceInvalidationService(redis_client=redis, route_group_reload=reload_policy)
    try:
        await service.start()
        assert service.worker_health.ready and observed == 1
        gate.clear()
        entered.clear()
        revision = 2
        clients = await redis.client_list()
        owned = [row for row in clients if row["name"] == name and "P" in row["flags"]]
        assert len(owned) == 1
        await redis.client_kill_filter(_id=owned[0]["id"])
        async with asyncio.timeout(5):
            await entered.wait()
        assert not service.worker_health.ready
        assert observed == 1
        gate.set()
        async with asyncio.timeout(5):
            while not service.worker_health.ready:
                await asyncio.sleep(0.01)
        assert observed == 2
    finally:
        gate.set()
        await service.close()
        await redis.aclose()
