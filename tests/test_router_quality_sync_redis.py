from __future__ import annotations

import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.router.quality import DeploymentQualityTracker, QualityOutcome
from src.router.quality.sync import QualityStatsSync

pytestmark = [pytest.mark.integration, pytest.mark.redis]


@pytest.fixture
async def quality_redis():
    url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision Redis for router quality sync tests")
        pytest.skip("DELTALLM_TEST_REDIS_URL is required")
    client = Redis.from_url(url, decode_responses=True)
    environment = "quality-" + uuid4().hex
    try:
        yield client, environment
    finally:
        keys = [key async for key in client.scan_iter(match=f"*{environment}*", count=100)]
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def test_two_processes_share_one_cluster_window(quality_redis) -> None:
    client, environment = quality_redis
    first, second = DeploymentQualityTracker(), DeploymentQualityTracker()
    first_sync = QualityStatsSync(first, client, environment=environment)
    second_sync = QualityStatsSync(second, client, environment=environment)
    first.record("dep:a", QualityOutcome.OK, first_token_ms=100)
    first.record("dep:a", QualityOutcome.EMPTY_ANSWER)
    second.record("dep:a", QualityOutcome.PROVIDER_ERROR)

    await first_sync.run_once()
    await second_sync.run_once()
    await first_sync.run_once()

    for tracker in (first, second):
        quality = tracker.snapshot().get("dep:a")
        assert tracker.snapshot().source == "cluster"
        assert (quality.calls, quality.empty_answers, quality.provider_errors) == (3, 1, 1)
        assert quality.avg_first_token_ms == 100


async def test_bucket_keys_expire(quality_redis) -> None:
    client, environment = quality_redis
    tracker = DeploymentQualityTracker()
    sync = QualityStatsSync(tracker, client, environment=environment)
    tracker.record("dep-a", QualityOutcome.OK)

    await sync.run_once()

    ttl = await client.ttl(sync.bucket_key(tracker.current_bucket()))
    assert 0 < ttl <= 1200
