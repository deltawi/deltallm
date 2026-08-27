from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from scripts.router_redis_schema_cutover import (
    delete_legacy_router_keys,
    resolve_redis_url,
    scan_legacy_router_state,
)


def test_resolve_redis_url_reads_and_trims_secret_file(tmp_path: Path) -> None:
    redis_url_file = tmp_path / "redis-url"
    redis_url_file.write_text("  redis://localhost:6379/0\n", encoding="utf-8")

    assert (
        resolve_redis_url(redis_url=None, redis_url_file=str(redis_url_file))
        == "redis://localhost:6379/0"
    )


def test_resolve_redis_url_rejects_empty_value() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        resolve_redis_url(redis_url="", redis_url_file=None)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_legacy_router_state_scan_and_delete_are_bounded() -> None:
    redis_url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("DELTALLM_TEST_REDIS_URL is not configured")

    redis_client = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    suffix = uuid4().hex
    active_key = f"active_requests:cutover-{suffix}"
    health_key = f"health:cutover-{suffix}"
    try:
        await redis_client.set(active_key, "2")
        await redis_client.hset(health_key, mapping={"healthy": "true"})

        state = await scan_legacy_router_state(
            redis_client,
            scan_count=10,
            max_scan_pages_per_pattern=100,
            max_keys=1000,
        )

        assert active_key in state.keys
        assert health_key in state.keys
        assert state.active_request_count >= 2

        deleted = await delete_legacy_router_keys(
            redis_client,
            (active_key, health_key),
            delete_batch_size=1,
        )
        assert deleted == 2
        assert await redis_client.exists(active_key, health_key) == 0
    finally:
        await redis_client.delete(active_key, health_key)
        await redis_client.aclose()
