import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.services.limit_counter import LimitCounter, _legacy_parallel_key
from tests.batch import test_chat_fallback_lifetime as fixtures

pytestmark = pytest.mark.redis
selected_batch = fixtures.selected_batch
split_batch = fixtures.split_batch


@pytest.mark.parametrize("release_failure", [False, True])
async def test_split_cancel_releases_real_caller_leases_without_releasing_other_request(
    split_batch, monkeypatch, release_failure
):
    h, prepared, heartbeats = split_batch
    url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not url:
        pytest.fail("DELTALLM_TEST_REDIS_URL is required")
    identity = "split-test-" + uuid4().hex
    key = _legacy_parallel_key("key", identity)
    redis = Redis.from_url(url, decode_responses=True)
    limiter = LimitCounter(redis, degraded_mode="fail_closed")
    h.app.state.limit_counter = limiter
    for item in prepared:
        item.policy_auth = item.policy_auth.model_copy(
            update={
                "api_key": identity,
                "rpm_limit": None,
                "tpm_limit": None,
                "user_id": None,
                "team_id": None,
                "organization_id": None,
            }
        )
    engine = h.worker._execution_engine
    started = asyncio.Event()

    async def blocked(request):
        started.set()
        await asyncio.Event().wait()

    h.provider = blocked
    task = None
    try:
        await limiter.acquire_parallel("key", identity, 10)
        task = asyncio.create_task(engine._execute_prepared_chat_microbatch_chunk(h.job, prepared))
        await asyncio.wait_for(started.wait(), 2)
        assert int(await redis.get(key)) == 4
        if release_failure:
            release = limiter.release_legacy_parallel_lease
            attempts = 0

            async def flaky(lease):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise ConnectionError("test release interruption")
                await release(lease)

            monkeypatch.setattr(limiter, "release_legacy_parallel_lease", flaky)
            monkeypatch.setattr(
                "src.batch.worker_persistence._policy_release_retry_delay_seconds", lambda _: 0
            )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(
            item.policy_lease is None and item.policy_lease_refresher is None for item in prepared
        )
        assert all(task.done() for _, task in heartbeats)
        assert len(engine._policy_release_retry_queue()) == int(release_failure)
        await engine._drain_policy_lease_release_retries()
        assert not engine._policy_release_retry_queue()
        assert int(await redis.get(key)) == 1
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine._release_prepared_policy_leases(prepared)
        await engine._drain_policy_lease_release_retries()
        await redis.delete(key)
        await redis.aclose()
