from __future__ import annotations

import asyncio
from datetime import date

import pytest

from src.services.spend_reporting_cache import (
    ReportingQueryTimedOut,
    ReportingRefreshBusy,
    SpendReportingCache,
    reporting_cache_ttl,
)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.expires_at: dict[str, float] = {}
        self.now = 0.0
        self.renewals = 0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _expire(self, key: str) -> None:
        expires_at = self.expires_at.get(key)
        if expires_at is not None and expires_at <= self.now:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)

    async def get(self, key: str):
        self._expire(key)
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl
        self.expires_at[key] = self.now + ttl

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False):
        self._expire(key)
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expires_at[key] = self.now + ex
        return True

    async def expire(self, key: str, ttl: int) -> bool:
        self._expire(key)
        if key not in self.values:
            return False
        self.expires_at[key] = self.now + ttl
        return True

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.expires_at.pop(key, None)

    async def eval(self, script: str, key_count: int, *args) -> int:
        if "SETEX" in script:
            assert key_count == 2
            lock_key, cache_key, token, ttl, payload = args
            self._expire(lock_key)
            if self.values.get(lock_key) != token:
                return 0
            await self.setex(cache_key, int(ttl), payload)
            return 1

        key, token = args[:2]
        self._expire(key)
        if self.values.get(key) != token:
            return 0
        if "EXPIRE" in script:
            self.renewals += 1
            return int(await self.expire(key, int(args[2])))
        self.values.pop(key, None)
        self.expires_at.pop(key, None)
        return 1


class _FailingRedis:
    async def get(self, key: str):
        del key
        raise RuntimeError("redis unavailable")

    async def setex(self, key: str, ttl: int, value: str) -> None:
        del key, ttl, value
        raise RuntimeError("redis unavailable")

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False):
        del key, value, ex, nx
        raise RuntimeError("redis unavailable")


class _HangingRedis:
    async def get(self, key: str):
        del key
        await asyncio.Event().wait()

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False):
        del key, value, ex, nx
        await asyncio.Event().wait()


def test_reporting_cache_ttl_scales_with_range_cost() -> None:
    assert reporting_cache_ttl(date(2026, 8, 1), date(2026, 8, 10)) == 30
    assert reporting_cache_ttl(date(2026, 1, 1), date(2026, 4, 1)) == 60
    assert reporting_cache_ttl(date(2025, 1, 1), date(2026, 8, 10)) == 300
    assert reporting_cache_ttl(None, None) == 300


def test_reporting_cache_key_is_stable_and_scope_sensitive() -> None:
    first = SpendReportingCache.key({"scope": ["org-1"], "group_by": "user"})
    reordered = SpendReportingCache.key({"group_by": "user", "scope": ["org-1"]})
    other_scope = SpendReportingCache.key({"scope": ["org-2"], "group_by": "user"})

    assert first == reordered
    assert first != other_scope
    assert "org-1" not in first


@pytest.mark.asyncio
async def test_reporting_cache_round_trips_json_response() -> None:
    redis = _Redis()
    cache = SpendReportingCache(redis)
    key = cache.key({"endpoint": "summary"})
    response = {"total_spend": 1.25, "total_requests": 4}

    await cache.set(key, response, 60)

    assert await cache.get(key) == response
    assert redis.ttls[key] == 60


@pytest.mark.asyncio
async def test_reporting_cache_fails_open_when_redis_is_unavailable() -> None:
    cache = SpendReportingCache(_FailingRedis())
    key = cache.key({"endpoint": "summary"})

    assert await cache.get(key) is None
    await cache.set(key, {"total_spend": 1}, 30)


@pytest.mark.asyncio
async def test_reporting_cache_fails_open_when_redis_operations_stall() -> None:
    cache = SpendReportingCache(
        _HangingRedis(),
        redis_operation_timeout_seconds=0.01,
        load_execution_timeout_seconds=0.2,
    )
    key = cache.key({"endpoint": "summary", "scope": "stalled-redis"})
    loader_calls = 0

    async def loader() -> dict[str, int]:
        nonlocal loader_calls
        loader_calls += 1
        return {"total_requests": 1}

    result = await asyncio.wait_for(cache.get_or_load(key, 60, loader), timeout=0.15)

    assert result.status == "fail_open"
    assert result.value == {"total_requests": 1}
    assert loader_calls == 1
    assert cache.load_limiter.active == 0


@pytest.mark.asyncio
async def test_normal_cache_hit_avoids_loader_and_forced_refresh_replaces_entry() -> None:
    redis = _Redis()
    cache = SpendReportingCache(redis)
    key = cache.key({"endpoint": "summary", "scope": "org-1"})
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"total_requests": calls}

    first = await cache.get_or_load(key, 60, loader)
    cached = await cache.get_or_load(key, 60, loader)
    refreshed = await cache.get_or_load(key, 60, loader, force_refresh=True)

    assert first.status == "miss"
    assert cached.status == "hit"
    assert refreshed.status == "forced_refresh"
    assert refreshed.value == {"total_requests": 2}
    assert calls == 2


@pytest.mark.asyncio
async def test_identical_local_misses_share_one_loader_task() -> None:
    redis = _Redis()
    cache = SpendReportingCache(redis)
    key = cache.key({"endpoint": "report", "scope": "org-1"})
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        loader_started.set()
        await release_loader.wait()
        return {"request_count": 12}

    first = asyncio.create_task(cache.get_or_load(key, 60, loader))
    await loader_started.wait()
    second = asyncio.create_task(cache.get_or_load(key, 60, loader))
    await asyncio.sleep(0)
    release_loader.set()
    results = await asyncio.gather(first, second)

    assert calls == 1
    assert {result.status for result in results} == {"miss", "coalesced_local"}
    assert all(result.value == {"request_count": 12} for result in results)


@pytest.mark.asyncio
async def test_separate_workers_coalesce_through_redis_lock() -> None:
    redis = _Redis()
    first_cache = SpendReportingCache(redis, wait_timeout_seconds=1, poll_interval_seconds=0.01)
    second_cache = SpendReportingCache(redis, wait_timeout_seconds=1, poll_interval_seconds=0.01)
    key = first_cache.key({"endpoint": "report", "scope": "org-1"})
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        loader_started.set()
        await release_loader.wait()
        return {"request_count": 7}

    first = asyncio.create_task(first_cache.get_or_load(key, 60, loader))
    await loader_started.wait()
    second = asyncio.create_task(second_cache.get_or_load(key, 60, loader))
    await asyncio.sleep(0.03)
    release_loader.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert calls == 1
    assert first_result.status == "miss"
    assert second_result.status == "coalesced_distributed"
    assert second_result.value == first_result.value


@pytest.mark.asyncio
async def test_long_loader_renews_distributed_lock_before_original_lease_expires() -> None:
    redis = _Redis()
    first_cache = SpendReportingCache(
        redis,
        lock_ttl_seconds=5,
        lock_renewal_interval_seconds=0.01,
        wait_timeout_seconds=1,
        poll_interval_seconds=0.01,
    )
    second_cache = SpendReportingCache(
        redis,
        lock_ttl_seconds=5,
        lock_renewal_interval_seconds=0.01,
        wait_timeout_seconds=1,
        poll_interval_seconds=0.01,
    )
    key = first_cache.key({"endpoint": "report", "scope": "org-slow"})
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        loader_started.set()
        await release_loader.wait()
        return {"request_count": 11}

    first = asyncio.create_task(first_cache.get_or_load(key, 60, loader))
    await loader_started.wait()
    redis.advance(4)
    await asyncio.sleep(0.03)
    assert redis.renewals > 0

    # The original lease would now be expired, but the heartbeat extended it.
    redis.advance(2)
    second = asyncio.create_task(second_cache.get_or_load(key, 60, loader))
    await asyncio.sleep(0.03)
    release_loader.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert calls == 1
    assert first_result.status == "miss"
    assert second_result.status == "coalesced_distributed"


@pytest.mark.asyncio
async def test_lost_lock_owner_does_not_overwrite_newer_cached_data() -> None:
    redis = _Redis()
    cache = SpendReportingCache(
        redis,
        lock_renewal_interval_seconds=0.01,
    )
    key = cache.key({"endpoint": "summary", "scope": "org-replaced"})
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()

    async def loader() -> dict[str, int]:
        loader_started.set()
        await release_loader.wait()
        return {"total_requests": 1}

    request = asyncio.create_task(cache.get_or_load(key, 60, loader))
    await loader_started.wait()
    redis.values[f"{key}:lock"] = "replacement-owner"
    await cache.set(key, {"total_requests": 99}, 60)
    release_loader.set()
    result = await request

    assert result.status == "lease_lost_uncached"
    assert result.value == {"total_requests": 1}
    assert await cache.get(key) == {"total_requests": 99}


@pytest.mark.asyncio
async def test_reporting_loader_concurrency_is_bounded_across_distinct_keys() -> None:
    redis = _Redis()
    cache = SpendReportingCache(
        redis,
        max_concurrent_loads=2,
        load_queue_timeout_seconds=1,
    )
    release_loaders = asyncio.Event()
    two_loaders_started = asyncio.Event()
    active = 0
    maximum_active = 0

    async def loader() -> dict[str, int]:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 2:
            two_loaders_started.set()
        await release_loaders.wait()
        active -= 1
        return {"request_count": 1}

    tasks = [
        asyncio.create_task(cache.get_or_load(cache.key({"report": index}), 60, loader))
        for index in range(4)
    ]
    await two_loaders_started.wait()
    await asyncio.sleep(0.02)
    assert maximum_active == 2
    release_loaders.set()
    await asyncio.gather(*tasks)
    assert maximum_active == 2


@pytest.mark.asyncio
async def test_uncached_reporting_work_uses_the_shared_capacity_guard() -> None:
    cache = SpendReportingCache(
        None,
        max_concurrent_loads=1,
        load_queue_timeout_seconds=0.02,
    )
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()

    async def held_loader() -> dict[str, int]:
        loader_started.set()
        await release_loader.wait()
        return {"request_count": 1}

    first = asyncio.create_task(cache.run_uncached(held_loader))
    await loader_started.wait()

    with pytest.raises(ReportingRefreshBusy):
        await cache.run_uncached(lambda: asyncio.sleep(0, result={"request_count": 2}))

    assert cache.load_limiter.active == 1
    release_loader.set()
    assert await first == {"request_count": 1}
    assert cache.load_limiter.active == 0


@pytest.mark.asyncio
async def test_reporting_loader_timeout_releases_capacity_lock_and_inflight_state() -> None:
    redis = _Redis()
    cache = SpendReportingCache(
        redis,
        max_concurrent_loads=1,
        load_execution_timeout_seconds=0.02,
        lock_renewal_interval_seconds=0.01,
    )
    timed_out_key = cache.key({"report": "stalled"})
    loader_started = asyncio.Event()
    loader_cancelled = asyncio.Event()

    async def stalled_loader() -> dict[str, int]:
        loader_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            loader_cancelled.set()

    request = asyncio.create_task(cache.get_or_load(timed_out_key, 60, stalled_loader))
    await loader_started.wait()
    with pytest.raises(ReportingQueryTimedOut):
        await request
    await asyncio.sleep(0)

    assert loader_cancelled.is_set()
    assert cache.load_limiter.active == 0
    assert f"{timed_out_key}:lock" not in redis.values
    assert await cache.get(timed_out_key) is None

    succeeding_key = cache.key({"report": "healthy"})
    result = await cache.get_or_load(
        succeeding_key,
        60,
        lambda: asyncio.sleep(0, result={"request_count": 1}),
    )
    assert result.status == "miss"


@pytest.mark.asyncio
async def test_coalesced_callers_receive_one_shared_loader_timeout() -> None:
    cache = SpendReportingCache(
        _FailingRedis(),
        load_execution_timeout_seconds=0.02,
    )
    key = cache.key({"report": "coalesced-timeout"})
    loader_started = asyncio.Event()
    calls = 0

    async def stalled_loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        loader_started.set()
        await asyncio.Event().wait()
        return {"request_count": 0}

    first = asyncio.create_task(cache.get_or_load(key, 60, stalled_loader))
    await loader_started.wait()
    second = asyncio.create_task(cache.get_or_load(key, 60, stalled_loader))
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert calls == 1
    assert all(isinstance(result, ReportingQueryTimedOut) for result in results)
    assert cache.load_limiter.active == 0


@pytest.mark.asyncio
async def test_live_concurrency_reconfiguration_preserves_active_accounting() -> None:
    cache = SpendReportingCache(
        None,
        max_concurrent_loads=2,
        load_queue_timeout_seconds=1,
        load_execution_timeout_seconds=1,
    )
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    third_started = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    release_third = asyncio.Event()

    async def held_loader(started: asyncio.Event, release: asyncio.Event) -> dict[str, int]:
        started.set()
        await release.wait()
        return {"request_count": 1}

    first = asyncio.create_task(cache.get_or_load(
        cache.key({"report": 1}),
        60,
        lambda: held_loader(first_started, release_first),
    ))
    second = asyncio.create_task(cache.get_or_load(
        cache.key({"report": 2}),
        60,
        lambda: held_loader(second_started, release_second),
    ))
    await asyncio.gather(first_started.wait(), second_started.wait())
    assert cache.load_limiter.active == 2

    await cache.reconfigure(
        max_concurrent_loads=1,
        global_max_concurrent_loads=1,
        load_queue_timeout_seconds=1,
        load_execution_timeout_seconds=1,
        redis_operation_timeout_seconds=0.5,
    )
    third = asyncio.create_task(cache.get_or_load(
        cache.key({"report": 3}),
        60,
        lambda: held_loader(third_started, release_third),
    ))
    release_first.set()
    await first
    await asyncio.sleep(0.02)
    assert not third_started.is_set()

    release_second.set()
    await second
    await third_started.wait()
    assert cache.load_limiter.active == 1

    await cache.reconfigure(
        max_concurrent_loads=2,
        global_max_concurrent_loads=3,
        load_queue_timeout_seconds=0.25,
        load_execution_timeout_seconds=0.5,
        redis_operation_timeout_seconds=0.1,
    )
    assert cache.load_limiter.limit == 2
    assert cache.load_queue_timeout_seconds == 0.25
    assert cache.load_execution_timeout_seconds == 0.5
    assert cache.global_max_concurrent_loads == 3
    assert cache.redis_operation_timeout_seconds == 0.1
    release_third.set()
    await third
    assert cache.load_limiter.active == 0


@pytest.mark.asyncio
async def test_raising_live_concurrency_limit_wakes_a_waiting_loader() -> None:
    cache = SpendReportingCache(
        None,
        max_concurrent_loads=1,
        load_queue_timeout_seconds=1,
        load_execution_timeout_seconds=1,
    )
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def held_loader(started: asyncio.Event) -> dict[str, int]:
        started.set()
        await release.wait()
        return {"request_count": 1}

    first = asyncio.create_task(cache.get_or_load(
        cache.key({"report": 1}),
        60,
        lambda: held_loader(first_started),
    ))
    second = asyncio.create_task(cache.get_or_load(
        cache.key({"report": 2}),
        60,
        lambda: held_loader(second_started),
    ))
    await first_started.wait()
    await asyncio.sleep(0.02)
    assert not second_started.is_set()

    await cache.reconfigure(
        max_concurrent_loads=2,
        global_max_concurrent_loads=2,
        load_queue_timeout_seconds=1,
        load_execution_timeout_seconds=1,
        redis_operation_timeout_seconds=0.5,
    )
    await second_started.wait()
    assert cache.load_limiter.active == 2
    release.set()
    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_active_reporting_load_keeps_an_immutable_execution_budget() -> None:
    cache = SpendReportingCache(
        None,
        global_max_concurrent_loads=4,
        load_execution_timeout_seconds=0.5,
    )
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()
    observed_budgets = []

    async def loader() -> dict[str, int]:
        observed_budgets.append(cache.active_load_budget)
        loader_started.set()
        await release_loader.wait()
        observed_budgets.append(cache.active_load_budget)
        return {"request_count": 1}

    task = asyncio.create_task(cache.run_uncached(loader))
    await loader_started.wait()
    await cache.reconfigure(
        max_concurrent_loads=1,
        global_max_concurrent_loads=1,
        load_queue_timeout_seconds=0.1,
        load_execution_timeout_seconds=0.05,
        redis_operation_timeout_seconds=0.1,
    )
    release_loader.set()
    await task

    assert [budget.execution_timeout_seconds for budget in observed_budgets] == [0.5, 0.5]
    assert [budget.global_max_concurrent_loads for budget in observed_budgets] == [4, 4]


@pytest.mark.asyncio
async def test_concurrent_forced_refresh_waits_for_new_envelope_even_when_value_is_identical() -> None:
    redis = _Redis()
    first_cache = SpendReportingCache(redis, wait_timeout_seconds=1, poll_interval_seconds=0.01)
    second_cache = SpendReportingCache(redis, wait_timeout_seconds=1, poll_interval_seconds=0.01)
    key = first_cache.key({"endpoint": "summary", "scope": "org-1"})
    await first_cache.set(key, {"total_requests": 7}, 60)
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        loader_started.set()
        await release_loader.wait()
        return {"total_requests": 7}

    first = asyncio.create_task(first_cache.get_or_load(key, 60, loader, force_refresh=True))
    await loader_started.wait()
    second = asyncio.create_task(second_cache.get_or_load(key, 60, loader, force_refresh=True))
    await asyncio.sleep(0.03)
    release_loader.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert calls == 1
    assert first_result.status == "forced_refresh"
    assert second_result.status == "coalesced_distributed"
    assert second_result.value == {"total_requests": 7}


@pytest.mark.asyncio
async def test_active_distributed_refresh_returns_busy_instead_of_duplicate_query() -> None:
    redis = _Redis()
    cache = SpendReportingCache(redis, wait_timeout_seconds=0.05, poll_interval_seconds=0.01)
    key = cache.key({"endpoint": "summary", "scope": "org-1"})
    redis.values[f"{key}:lock"] = "another-worker"
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"total_requests": 1}

    with pytest.raises(ReportingRefreshBusy):
        await cache.get_or_load(key, 60, loader)

    assert calls == 0


@pytest.mark.asyncio
async def test_loader_failure_is_not_cached_and_does_not_leave_inflight_task() -> None:
    redis = _Redis()
    cache = SpendReportingCache(redis)
    key = cache.key({"endpoint": "summary", "scope": "org-retry"})
    calls = 0

    async def failing_loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        raise RuntimeError("database unavailable")

    async def succeeding_loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"total_requests": 9}

    with pytest.raises(RuntimeError, match="database unavailable"):
        await cache.get_or_load(key, 60, failing_loader)
    await asyncio.sleep(0)
    result = await cache.get_or_load(key, 60, succeeding_loader)

    assert calls == 2
    assert result.status == "miss"
    assert result.value == {"total_requests": 9}


@pytest.mark.asyncio
async def test_redis_failure_still_coalesces_within_worker() -> None:
    cache = SpendReportingCache(_FailingRedis())
    key = cache.key({"endpoint": "summary"})
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        loader_started.set()
        await release_loader.wait()
        return {"total_requests": 3}

    first = asyncio.create_task(cache.get_or_load(key, 60, loader))
    await loader_started.wait()
    second = asyncio.create_task(cache.get_or_load(key, 60, loader))
    await asyncio.sleep(0)
    release_loader.set()
    results = await asyncio.gather(first, second)

    assert calls == 1
    assert {result.status for result in results} == {"fail_open", "coalesced_local"}


@pytest.mark.asyncio
async def test_redis_failure_still_bounds_distinct_reporting_queries() -> None:
    cache = SpendReportingCache(
        _FailingRedis(),
        max_concurrent_loads=2,
        load_queue_timeout_seconds=1,
    )
    release_loaders = asyncio.Event()
    two_loaders_started = asyncio.Event()
    active = 0
    maximum_active = 0

    async def loader() -> dict[str, int]:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 2:
            two_loaders_started.set()
        await release_loaders.wait()
        active -= 1
        return {"request_count": 1}

    tasks = [
        asyncio.create_task(cache.get_or_load(cache.key({"report": index}), 60, loader))
        for index in range(4)
    ]
    await two_loaders_started.wait()
    await asyncio.sleep(0.02)
    assert maximum_active == 2
    release_loaders.set()

    results = await asyncio.gather(*tasks)

    assert maximum_active == 2
    assert {result.status for result in results} == {"fail_open"}
