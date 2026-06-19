from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.db.cache_invalidation_outbox import CacheInvalidationOutboxRecord
from src.services.cache_invalidation import (
    CacheInvalidationResult,
    CacheInvalidationService,
    CacheInvalidationWorker,
    CacheInvalidationWorkerConfig,
)
from src.services.cache_invalidation_errors import CacheInvalidationBackendUnavailable


class _KeyService:
    def __init__(
        self,
        *,
        fail: bool = False,
        backend_unavailable: str | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.fail = fail
        self.backend_unavailable = backend_unavailable
        self.delay_seconds = delay_seconds
        self.org_invalidations: list[str] = []
        self.team_invalidations: list[str] = []
        self.user_invalidations: list[str] = []
        self.key_invalidations: list[str] = []

    def require_cache_invalidation_backend(self, *, scope_type: str) -> None:
        del scope_type
        if self.backend_unavailable is not None:
            raise CacheInvalidationBackendUnavailable(self.backend_unavailable)

    async def invalidate_keys_for_org(self, organization_id: str) -> int:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.org_invalidations.append(organization_id)
        return 2

    async def invalidate_keys_for_team(self, team_id: str) -> int:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.team_invalidations.append(team_id)
        return 1

    async def invalidate_keys_for_user(self, user_id: str) -> int:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.user_invalidations.append(user_id)
        return 1

    async def invalidate_key_cache_by_hash(self, token_hash: str) -> None:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.key_invalidations.append(token_hash)


class _Repository:
    def __init__(
        self,
        *,
        enqueue_fail: bool = False,
        records: list[CacheInvalidationOutboxRecord] | None = None,
        complete_error: Exception | None = None,
        complete_result: bool = True,
        retry_result: bool = True,
        failed_result: bool = True,
    ) -> None:
        self.enqueue_fail = enqueue_fail
        self.records = list(records or [])
        self.complete_error = complete_error
        self.complete_result = complete_result
        self.retry_result = retry_result
        self.failed_result = failed_result
        self.enqueues: list[dict[str, Any]] = []
        self.completed_attempts: list[str] = []
        self.completed: list[str] = []
        self.retry_attempts: list[dict[str, Any]] = []
        self.retries: list[dict[str, Any]] = []
        self.failed_attempts: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []

    async def enqueue(self, **kwargs):  # noqa: ANN003, ANN201
        if self.enqueue_fail:
            raise RuntimeError("db unavailable")
        self.enqueues.append(dict(kwargs))
        return SimpleNamespace(invalidation_id=f"invalidation-{len(self.enqueues)}")

    async def claim_due(
        self, *, worker_id: str, lease_seconds: int, limit: int = 25
    ) -> list[CacheInvalidationOutboxRecord]:
        del worker_id, lease_seconds
        claimed = self.records[:limit]
        self.records = self.records[limit:]
        return claimed

    async def mark_completed(self, invalidation_id: str, *, worker_id: str) -> bool:
        del worker_id
        self.completed_attempts.append(invalidation_id)
        if self.complete_error is not None:
            raise self.complete_error
        if not self.complete_result:
            return False
        self.completed.append(invalidation_id)
        return True

    async def mark_retry(
        self,
        invalidation_id: str,
        *,
        worker_id: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        attempt = {
            "invalidation_id": invalidation_id,
            "worker_id": worker_id,
            "error": error,
            "next_attempt_at": next_attempt_at,
        }
        self.retry_attempts.append(attempt)
        if not self.retry_result:
            return False
        self.retries.append(attempt)
        return True

    async def mark_failed(self, invalidation_id: str, *, worker_id: str, error: str) -> bool:
        attempt = {
            "invalidation_id": invalidation_id,
            "worker_id": worker_id,
            "error": error,
        }
        self.failed_attempts.append(attempt)
        if not self.failed_result:
            return False
        self.failed.append(attempt)
        return True


def _record(**overrides: Any) -> CacheInvalidationOutboxRecord:
    record = CacheInvalidationOutboxRecord(
        invalidation_id="invalidation-1",
        scope_type="organization",
        scope_id="org-1",
        reason="test",
        status="processing",
        attempt_count=1,
        max_attempts=3,
        next_attempt_at=datetime.now(tz=UTC),
    )
    return replace(record, **overrides)


@pytest.mark.asyncio
async def test_cache_invalidation_service_returns_immediate_success() -> None:
    key_service = _KeyService()
    repository = _Repository()
    service = CacheInvalidationService(key_service=key_service, repository=repository)

    result = await service.invalidate_organization("org-1", reason="tier_assignment_update")

    assert result.safe is True
    assert result.to_dict() | {"latency_ms": result.latency_ms} == {
        "attempted": True,
        "invalidated": True,
        "queued": False,
        "count": 2,
        "latency_ms": result.latency_ms,
    }
    assert key_service.org_invalidations == ["org-1"]
    assert repository.enqueues == []


@pytest.mark.asyncio
async def test_cache_invalidation_service_queues_when_backend_unavailable() -> None:
    key_service = _KeyService(backend_unavailable="redis unavailable")
    repository = _Repository()
    service = CacheInvalidationService(key_service=key_service, repository=repository)

    result = await service.invalidate_organization(
        "org-1",
        reason="tier_assignment_update",
        metadata={"assignment_id": "assignment-1"},
    )

    assert result.safe is True
    assert {
        key: result.to_dict().get(key)
        for key in ("attempted", "invalidated", "queued", "reason", "error_type", "invalidation_id")
    } == {
        "attempted": False,
        "invalidated": False,
        "queued": True,
        "reason": "cache_invalidation_backend_unavailable",
        "error_type": "CacheInvalidationBackendUnavailable",
        "invalidation_id": "invalidation-1",
    }
    assert key_service.org_invalidations == []
    assert repository.enqueues == [
        {
            "scope_type": "organization",
            "scope_id": "org-1",
            "reason": "tier_assignment_update",
            "metadata": {"assignment_id": "assignment-1"},
            "max_attempts": 10,
        }
    ]


@pytest.mark.asyncio
async def test_cache_invalidation_service_queues_after_immediate_failure() -> None:
    key_service = _KeyService(fail=True)
    repository = _Repository()
    service = CacheInvalidationService(key_service=key_service, repository=repository)

    result = await service.invalidate_organization(
        "org-1",
        reason="tier_assignment_update",
        metadata={"assignment_id": "assignment-1"},
    )

    assert result.safe is True
    assert {
        key: result.to_dict().get(key)
        for key in ("attempted", "invalidated", "queued", "reason", "error_type", "invalidation_id")
    } == {
        "attempted": True,
        "invalidated": False,
        "queued": True,
        "reason": "immediate_invalidation_failed",
        "error_type": "RuntimeError",
        "invalidation_id": "invalidation-1",
    }
    assert repository.enqueues == [
        {
            "scope_type": "organization",
            "scope_id": "org-1",
            "reason": "tier_assignment_update",
            "metadata": {"assignment_id": "assignment-1"},
            "max_attempts": 10,
        }
    ]


@pytest.mark.asyncio
async def test_cache_invalidation_service_reports_unsafe_when_enqueue_fails() -> None:
    service = CacheInvalidationService(
        key_service=_KeyService(fail=True),
        repository=_Repository(enqueue_fail=True),
    )

    result = await service.invalidate_organization("org-1", reason="tier_assignment_update")

    assert result.safe is False
    assert {
        key: result.to_dict().get(key)
        for key in (
            "attempted",
            "invalidated",
            "queued",
            "reason",
            "error_type",
            "enqueue_error_type",
        )
    } == {
        "attempted": True,
        "invalidated": False,
        "queued": False,
        "reason": "immediate_invalidation_failed",
        "error_type": "RuntimeError",
        "enqueue_error_type": "RuntimeError",
    }


@pytest.mark.asyncio
async def test_cache_invalidation_service_immediate_now_does_not_enqueue_after_failure() -> None:
    repository = _Repository()
    service = CacheInvalidationService(
        key_service=_KeyService(fail=True),
        repository=repository,
    )

    result = await service.invalidate_organization_cache_now(
        "org-1",
        reason="tier_assignment_update",
    )

    assert result.safe is False
    assert {
        key: result.to_dict().get(key)
        for key in ("attempted", "invalidated", "queued", "reason", "error_type")
    } == {
        "attempted": True,
        "invalidated": False,
        "queued": False,
        "reason": "immediate_invalidation_failed",
        "error_type": "RuntimeError",
    }
    assert repository.enqueues == []


@pytest.mark.asyncio
async def test_cache_invalidation_service_immediate_now_times_out_without_enqueueing() -> None:
    repository = _Repository()
    service = CacheInvalidationService(
        key_service=_KeyService(delay_seconds=0.05),
        repository=repository,
        immediate_timeout_seconds=0.001,
    )

    result = await service.invalidate_organization_cache_now(
        "org-1",
        reason="tier_assignment_update",
    )

    assert result.safe is False
    assert {
        key: result.to_dict().get(key)
        for key in ("attempted", "invalidated", "queued", "reason", "error_type")
    } == {
        "attempted": True,
        "invalidated": False,
        "queued": False,
        "reason": "immediate_invalidation_timeout",
        "error_type": "TimeoutError",
    }
    assert repository.enqueues == []


@pytest.mark.asyncio
async def test_cache_invalidation_result_combines_scheduled_and_immediate_details() -> None:
    scheduled = CacheInvalidationResult(
        attempted=False,
        invalidated=False,
        queued=True,
        reason="scheduled_for_worker",
        invalidation_id="invalidation-1",
    )
    immediate = CacheInvalidationResult(
        attempted=True,
        invalidated=True,
        count=2,
        latency_ms=3,
    )

    result = scheduled.with_immediate_result(immediate)

    assert result.safe is True
    assert result.to_dict() == {
        "attempted": True,
        "invalidated": True,
        "queued": True,
        "count": 2,
        "reason": "scheduled_for_worker",
        "invalidation_id": "invalidation-1",
        "immediate_attempted": True,
        "immediate_invalidated": True,
        "immediate_count": 2,
        "immediate_latency_ms": 3,
    }


@pytest.mark.asyncio
async def test_cache_invalidation_worker_completes_claimed_records() -> None:
    key_service = _KeyService()
    repository = _Repository(records=[_record()])
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
    )

    processed = await worker.process_once()

    assert processed == 1
    assert key_service.org_invalidations == ["org-1"]
    assert repository.completed == ["invalidation-1"]
    assert repository.retries == []
    assert repository.failed == []


@pytest.mark.asyncio
async def test_cache_invalidation_worker_skips_completion_log_when_completion_transition_misses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="src.services.cache_invalidation_worker")
    key_service = _KeyService()
    repository = _Repository(records=[_record()], complete_result=False)
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
    )

    processed = await worker.process_once()

    assert processed == 1
    assert key_service.org_invalidations == ["org-1"]
    assert repository.completed_attempts == ["invalidation-1"]
    assert repository.completed == []
    assert repository.retries == []
    assert repository.failed == []
    assert "cache invalidation outbox transition skipped" in caplog.text


@pytest.mark.asyncio
async def test_cache_invalidation_worker_retries_when_backend_unavailable() -> None:
    key_service = _KeyService(backend_unavailable="redis unavailable")
    repository = _Repository(records=[_record(attempt_count=1, max_attempts=3)])
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
    )

    processed = await worker.process_once()

    assert processed == 1
    assert key_service.org_invalidations == []
    assert repository.completed == []
    assert repository.failed == []
    assert repository.retries[0]["invalidation_id"] == "invalidation-1"
    assert repository.retries[0]["worker_id"] == "worker-1"
    assert repository.retries[0]["error"] == "redis unavailable"


@pytest.mark.asyncio
async def test_cache_invalidation_worker_logs_retry_transition_miss_without_recording_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="src.services.cache_invalidation_worker")
    key_service = _KeyService(fail=True)
    repository = _Repository(
        records=[_record(attempt_count=1, max_attempts=3)],
        retry_result=False,
    )
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
    )

    processed = await worker.process_once()

    assert processed == 1
    assert repository.completed == []
    assert repository.failed == []
    assert repository.retry_attempts[0]["invalidation_id"] == "invalidation-1"
    assert repository.retry_attempts[0]["error"] == "redis unavailable"
    assert repository.retries == []
    assert "cache invalidation outbox transition skipped" in caplog.text


@pytest.mark.asyncio
async def test_cache_invalidation_worker_retries_when_record_times_out() -> None:
    key_service = _KeyService(delay_seconds=0.05)
    repository = _Repository(records=[_record(attempt_count=1, max_attempts=3)])
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
        config=CacheInvalidationWorkerConfig(record_timeout_seconds=0.001),
    )

    processed = await worker.process_once()

    assert processed == 1
    assert key_service.org_invalidations == []
    assert repository.completed == []
    assert repository.failed == []
    assert repository.retries[0]["invalidation_id"] == "invalidation-1"
    assert repository.retries[0]["worker_id"] == "worker-1"
    assert repository.retries[0]["error"] == "cache invalidation record timed out"


@pytest.mark.asyncio
async def test_cache_invalidation_worker_preserves_completion_timeout_error() -> None:
    key_service = _KeyService()
    repository = _Repository(
        records=[_record(attempt_count=1, max_attempts=3)],
        complete_error=TimeoutError("db timeout"),
    )
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
        config=CacheInvalidationWorkerConfig(record_timeout_seconds=1),
    )

    processed = await worker.process_once()

    assert processed == 1
    assert key_service.org_invalidations == ["org-1"]
    assert repository.completed == []
    assert repository.failed == []
    assert repository.retries[0]["error"] == "db timeout"


@pytest.mark.asyncio
async def test_cache_invalidation_worker_fails_when_record_times_out_at_max_attempts() -> None:
    key_service = _KeyService(delay_seconds=0.05)
    repository = _Repository(records=[_record(attempt_count=3, max_attempts=3)])
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
        config=CacheInvalidationWorkerConfig(record_timeout_seconds=0.001),
    )

    processed = await worker.process_once()

    assert processed == 1
    assert key_service.org_invalidations == []
    assert repository.completed == []
    assert repository.retries == []
    assert repository.failed == [
        {
            "invalidation_id": "invalidation-1",
            "worker_id": "worker-1",
            "error": "cache invalidation record timed out",
        }
    ]


@pytest.mark.asyncio
async def test_cache_invalidation_worker_logs_failed_transition_miss_without_recording_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="src.services.cache_invalidation_worker")
    key_service = _KeyService(fail=True)
    repository = _Repository(
        records=[_record(attempt_count=3, max_attempts=3)],
        failed_result=False,
    )
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
    )

    processed = await worker.process_once()

    assert processed == 1
    assert repository.completed == []
    assert repository.retries == []
    assert repository.failed_attempts == [
        {
            "invalidation_id": "invalidation-1",
            "worker_id": "worker-1",
            "error": "redis unavailable",
        }
    ]
    assert repository.failed == []
    assert "cache invalidation outbox transition skipped" in caplog.text


@pytest.mark.asyncio
async def test_cache_invalidation_worker_fails_backend_unavailable_after_max_attempts() -> None:
    key_service = _KeyService(backend_unavailable="database unavailable")
    repository = _Repository(records=[_record(attempt_count=3, max_attempts=3)])
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
    )

    processed = await worker.process_once()

    assert processed == 1
    assert key_service.org_invalidations == []
    assert repository.completed == []
    assert repository.retries == []
    assert repository.failed == [
        {
            "invalidation_id": "invalidation-1",
            "worker_id": "worker-1",
            "error": "database unavailable",
        }
    ]


@pytest.mark.asyncio
async def test_cache_invalidation_worker_retries_before_max_attempts() -> None:
    key_service = _KeyService(fail=True)
    repository = _Repository(records=[_record(attempt_count=1, max_attempts=3)])
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
        config=CacheInvalidationWorkerConfig(retry_initial_seconds=5, retry_max_seconds=60),
    )

    processed = await worker.process_once()

    assert processed == 1
    assert repository.completed == []
    assert repository.failed == []
    assert repository.retries[0]["invalidation_id"] == "invalidation-1"
    assert repository.retries[0]["worker_id"] == "worker-1"
    assert repository.retries[0]["error"] == "redis unavailable"


@pytest.mark.asyncio
async def test_cache_invalidation_worker_fails_after_max_attempts() -> None:
    key_service = _KeyService(fail=True)
    repository = _Repository(records=[_record(attempt_count=3, max_attempts=3)])
    worker = CacheInvalidationWorker(
        repository=repository,
        key_service=key_service,
        worker_id="worker-1",
    )

    processed = await worker.process_once()

    assert processed == 1
    assert repository.completed == []
    assert repository.retries == []
    assert repository.failed == [
        {
            "invalidation_id": "invalidation-1",
            "worker_id": "worker-1",
            "error": "redis unavailable",
        }
    ]


@pytest.mark.asyncio
async def test_cache_invalidation_worker_survives_transient_cycle_failure() -> None:
    worker = CacheInvalidationWorker(
        repository=_Repository(),
        key_service=_KeyService(),
        worker_id="worker-1",
        config=CacheInvalidationWorkerConfig(poll_interval_seconds=0),
    )
    calls = 0

    async def _process_once() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db unavailable")
        worker.stop()
        return 1

    worker.process_once = _process_once  # type: ignore[method-assign]

    await worker.run()

    assert calls == 2
