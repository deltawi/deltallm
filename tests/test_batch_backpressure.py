from __future__ import annotations

import json

import pytest

from src.batch.backpressure import BatchBackpressureCoordinator


class _RedisRecorder:
    def __init__(
        self,
        *,
        fail: bool = False,
        fail_get: bool | None = None,
        fail_setex: bool | None = None,
    ) -> None:
        self.fail_get = fail if fail_get is None else fail_get
        self.fail_setex = fail if fail_setex is None else fail_setex
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if self.fail_setex:
            raise RuntimeError("redis unavailable")
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key: str) -> str | None:
        if self.fail_get:
            raise RuntimeError("redis unavailable")
        return self.values.get(key)


@pytest.mark.asyncio
async def test_model_group_deferral_uses_hashed_redis_key_and_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.batch.backpressure.time.time", lambda: 1000)
    redis = _RedisRecorder()
    coordinator = BatchBackpressureCoordinator(
        redis_client=redis,
        enabled=True,
        min_delay_seconds=5,
        max_delay_seconds=300,
    )

    deferral = await coordinator.defer_model_group(
        "group:text-embedding-3-small",
        delay_seconds=30,
        reason="no_healthy_deployments",
    )

    assert deferral is not None
    assert deferral.model_group == "group:text-embedding-3-small"
    assert deferral.reason == "no_healthy_deployments"
    assert deferral.until_epoch_seconds == 1030
    assert len(redis.values) == 1
    key = next(iter(redis.values))
    assert key.startswith("batch:backpressure:model_group:")
    assert "text-embedding" not in key
    assert redis.ttls[key] == 35
    assert json.loads(redis.values[key]) == {
        "reason": "no_healthy_deployments",
        "until": 1030,
        "last_seen": 1000,
    }
    assert await coordinator.is_model_group_deferred("group:text-embedding-3-small") is True


@pytest.mark.asyncio
async def test_model_group_deferral_clamps_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.batch.backpressure.time.time", lambda: 1000)
    redis = _RedisRecorder()
    coordinator = BatchBackpressureCoordinator(
        redis_client=redis,
        enabled=True,
        min_delay_seconds=5,
        max_delay_seconds=60,
    )

    deferral = await coordinator.defer_model_group("group:m", delay_seconds=1, reason="overload")

    assert deferral is not None
    assert deferral.until_epoch_seconds == 1005
    assert next(iter(redis.ttls.values())) == 10
    assert json.loads(next(iter(redis.values.values())))["until"] == 1005


@pytest.mark.asyncio
async def test_local_model_group_deferral_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1000
    monkeypatch.setattr("src.batch.backpressure.time.time", lambda: now)
    coordinator = BatchBackpressureCoordinator(
        redis_client=None,
        enabled=True,
        min_delay_seconds=5,
        max_delay_seconds=300,
    )

    await coordinator.defer_model_group("group:m", delay_seconds=5, reason="no_healthy_deployments")

    assert await coordinator.is_model_group_deferred("group:m") is True
    now = 1006
    assert await coordinator.is_model_group_deferred("group:m") is False


@pytest.mark.asyncio
async def test_redis_failure_falls_back_to_local_deferral(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.batch.backpressure.time.time", lambda: 1000)
    coordinator = BatchBackpressureCoordinator(
        redis_client=_RedisRecorder(fail=True),
        enabled=True,
        min_delay_seconds=5,
        max_delay_seconds=300,
    )

    await coordinator.defer_model_group("group:m", delay_seconds=20, reason="no_healthy_deployments")

    deferral = await coordinator.get_model_group_deferral("group:m")
    assert deferral is not None
    assert deferral.reason == "no_healthy_deployments"
    assert deferral.until_epoch_seconds == 1020


@pytest.mark.asyncio
async def test_successful_redis_write_mirrors_local_deferral_for_read_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.batch.backpressure.time.time", lambda: 1000)
    redis = _RedisRecorder()
    coordinator = BatchBackpressureCoordinator(
        redis_client=redis,
        enabled=True,
        min_delay_seconds=5,
        max_delay_seconds=300,
    )

    written = await coordinator.defer_model_group("group:m", delay_seconds=20, reason="no_healthy_deployments")
    redis.fail_get = True

    assert await coordinator.get_model_group_deferral("group:m") == written


@pytest.mark.asyncio
async def test_redis_miss_after_write_failure_uses_local_deferral(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.batch.backpressure.time.time", lambda: 1000)
    redis = _RedisRecorder(fail_setex=True)
    coordinator = BatchBackpressureCoordinator(
        redis_client=redis,
        enabled=True,
        min_delay_seconds=5,
        max_delay_seconds=300,
    )

    written = await coordinator.defer_model_group("group:m", delay_seconds=20, reason="no_healthy_deployments")
    redis.fail_setex = False

    assert await coordinator.get_model_group_deferral("group:m") == written


@pytest.mark.asyncio
async def test_redis_active_deferral_wins_over_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1000
    monkeypatch.setattr("src.batch.backpressure.time.time", lambda: now)
    redis = _RedisRecorder()
    coordinator = BatchBackpressureCoordinator(
        redis_client=redis,
        enabled=True,
        min_delay_seconds=5,
        max_delay_seconds=300,
    )

    await coordinator.defer_model_group("group:m", delay_seconds=20, reason="local")
    key = next(iter(redis.values))
    redis.values[key] = json.dumps(
        {"reason": "redis", "until": 1040, "last_seen": 1001},
        separators=(",", ":"),
    )

    deferral = await coordinator.get_model_group_deferral("group:m")

    assert deferral is not None
    assert deferral.reason == "redis"
    assert deferral.until_epoch_seconds == 1040


@pytest.mark.asyncio
async def test_disabled_model_group_deferral_is_noop() -> None:
    redis = _RedisRecorder()
    coordinator = BatchBackpressureCoordinator(
        redis_client=redis,
        enabled=False,
        min_delay_seconds=5,
        max_delay_seconds=300,
    )

    deferral = await coordinator.defer_model_group("group:m", delay_seconds=20, reason="no_healthy_deployments")

    assert deferral is None
    assert redis.values == {}
    assert await coordinator.is_model_group_deferred("group:m") is False
