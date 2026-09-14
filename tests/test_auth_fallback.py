from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from src.db.repositories import KeyRecord, KeyRepository
from src.metrics.prometheus import get_prometheus_registry
from src.models.errors import AuthenticationError, AuthenticationUnavailableError
from src.services.auth_fallback import AuthFallbackLimits
from src.services.key_service import KeyService
from tests.test_key_service import RecordingRedis

pytestmark = [pytest.mark.hermetic, pytest.mark.asyncio]


class BlockingRepository:
    def __init__(self) -> None:
        self.calls = 0
        self.release = asyncio.Event()

    async def get_by_token(self, token_hash: str) -> KeyRecord:
        self.calls += 1
        await self.release.wait()
        return KeyRecord(token=token_hash, metadata={"mutable": {"value": 1}})


async def until(predicate) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


def service(repo, redis=None, **limits) -> KeyService:
    return KeyService(
        repository=repo,
        redis_client=redis,
        salt="test-salt",
        fallback_limits=replace(AuthFallbackLimits(), **limits),
    )


async def test_same_key_is_collapsed_and_mutable_auth_is_not_shared() -> None:
    repo, cache = BlockingRepository(), RecordingRedis()
    auth = service(repo, cache, max_active=2, max_waiters=8)
    tasks = [asyncio.create_task(auth.validate_key("sk-one")) for _ in range(10)]
    try:
        await until(lambda: auth.fallback.callers == 10 and repo.calls == 1)
        assert repo.calls == auth.fallback.size == 1
        repo.release.set()
        results = await asyncio.gather(*tasks)
        results[0].metadata["mutable"]["value"] = 99
        assert results[1].metadata["mutable"]["value"] == 1
        assert len(cache.store) == 1
        assert auth.fallback.size == auth.fallback.callers == auth.fallback.gate.active == 0
    finally:
        repo.release.set()
        await auth.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_distinct_key_flood_has_finite_tasks_callers_and_sql() -> None:
    repo = BlockingRepository()
    auth = service(repo, max_active=2, max_waiters=2, queue_timeout_ms=1000)
    tasks = [asyncio.create_task(auth.validate_key(f"sk-{i}")) for i in range(100)]
    try:
        await until(lambda: repo.calls == 2)
        assert auth.fallback.size == auth.fallback.callers == 4
        assert auth.fallback.gate.active == auth.fallback.gate.waiters == 2
        repo.release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(result, AuthenticationUnavailableError) for result in results) == 96
        assert repo.calls == 4
        assert auth.fallback.size == auth.fallback.gate.active == auth.fallback.gate.waiters == 0
    finally:
        repo.release.set()
        await auth.close()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("termination", ["timeout", "cancel"])
async def test_caller_leaving_does_not_release_unfinished_native_work(termination: str) -> None:
    repo = BlockingRepository()
    cache = RecordingRedis()
    auth = service(repo, cache, max_active=1, max_waiters=0, timeout_seconds=0.02)
    task = asyncio.create_task(auth.validate_key("sk-first"))
    try:
        await until(lambda: repo.calls == 1)
        if termination == "cancel":
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if termination == "cancel" else AuthenticationUnavailableError
        ):
            await task
        assert auth.fallback.size == auth.fallback.gate.active == 1
        assert auth.fallback.callers == 0
        with pytest.raises(AuthenticationUnavailableError):
            await auth.validate_key("sk-second")
        assert repo.calls == 1
        if termination == "timeout":
            assert not cache.store
        repo.release.set()
        await until(lambda: auth.fallback.size == 0)
        assert auth.fallback.gate.active == 0
        if termination == "timeout":
            assert not cache.store  # Overdue SQL cannot refresh stale auth.
    finally:
        repo.release.set()
        await auth.close()
        await asyncio.gather(task, return_exceptions=True)


async def test_shutdown_closes_admission_and_observes_owned_tasks() -> None:
    repo = BlockingRepository()
    auth = service(repo)
    task = asyncio.create_task(auth.validate_key("sk-key"))
    await until(lambda: repo.calls == 1)
    await auth.close()
    await asyncio.gather(task, return_exceptions=True)
    assert auth.fallback.closed
    assert auth.fallback.size == auth.fallback.gate.active == auth.fallback.callers == 0
    with pytest.raises(AuthenticationUnavailableError):
        await auth.validate_key("sk-key")


async def test_local_invalidation_fences_pending_lookup_without_replacing_sql() -> None:
    repo, cache = BlockingRepository(), RecordingRedis()
    auth = service(repo, cache)
    task = asyncio.create_task(auth.validate_key("sk-key"))
    try:
        await until(lambda: repo.calls == 1)
        await auth.invalidate_key_cache_by_hash(auth.hash_key("sk-key"))
        with pytest.raises(AuthenticationUnavailableError):
            await auth.validate_key("sk-key")
        assert repo.calls == 1
        repo.release.set()
        with pytest.raises(AuthenticationUnavailableError):
            await task
        assert not cache.store
        result = await auth.validate_key("sk-key")
        assert result.api_key == auth.hash_key("sk-key")
        assert repo.calls == 2
    finally:
        repo.release.set()
        await auth.close()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "failure",
    [
        "read",
        "write",
        "read_timeout",
        "write_timeout",
        "malformed",
        "oversized",
        "mismatched_identity",
        "partial",
    ],
)
async def test_cache_failure_uses_durable_auth_with_bounded_cache_io(failure: str) -> None:
    repo = BlockingRepository()
    repo.release.set()

    class Cache(RecordingRedis):
        async def get(self, key):
            if failure == "read":
                raise ConnectionError("private connection details")
            if failure == "read_timeout":
                await asyncio.Event().wait()
            return await super().get(key)

        async def setex(self, key, ttl, value):
            if failure == "write":
                raise ConnectionError("private connection details")
            if failure == "write_timeout":
                await asyncio.Event().wait()
            return await super().setex(key, ttl, value)

    cache = Cache()
    auth = service(repo, cache, cache_timeout_seconds=0.005)
    if failure in {"malformed", "oversized", "mismatched_identity", "partial"}:
        cache.store[auth._cache_key(auth.hash_key("sk-key"))] = {
            "partial": json.dumps({"api_key": auth.hash_key("sk-key")}),
            "malformed": "{bad",
            "oversized": "x" * 200000,
            "mismatched_identity": json.dumps({"api_key": "different-key"}),
        }[failure]
    result = await auth.validate_key("sk-key")
    assert result.api_key == auth.hash_key("sk-key")
    assert repo.calls == 1
    await auth.close()


async def test_cached_expiry_is_checked_even_if_redis_retains_entry() -> None:
    repo = BlockingRepository()
    repo.release.set()
    cache = RecordingRedis()
    auth = service(repo, cache)
    result = await auth.validate_key("sk-key")
    result.expires = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    cache.store[auth._cache_key(auth.hash_key("sk-key"))] = result.model_dump_json()
    with pytest.raises(AuthenticationError, match="expired"):
        await auth.validate_key("sk-key")
    assert repo.calls == 1


async def test_missing_database_is_unavailable_instead_of_invalid_identity() -> None:
    auth = service(KeyRepository(None))
    with pytest.raises(AuthenticationUnavailableError) as error:
        await auth.validate_key("sk-key")
    assert error.value.affects_deployment_health is False
    assert error.value.retry_after == 1


async def test_metrics_do_not_label_credentials_or_exception_text() -> None:
    for metric in get_prometheus_registry().collect():
        if not metric.name.startswith(("deltallm_auth_fallback", "deltallm_ingress")):
            continue
        for sample in metric.samples:
            assert set(sample.labels) <= {"allocation", "reason", "phase", "outcome", "le"}
            assert all(
                "sk-" not in value and "private" not in value for value in sample.labels.values()
            )
