from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.batch.completion_outbox import BatchCompletionOutboxWorker, BatchCompletionOutboxWorkerConfig
from src.batch.models import BatchCompletionOutboxRecord, BatchCompletionOutboxStatus


def _build_record(
    *,
    completion_id: str = "completion-1",
    attempt_count: int = 0,
    max_attempts: int = 5,
    status: str = BatchCompletionOutboxStatus.QUEUED,
    payload_overrides: dict | None = None,
) -> BatchCompletionOutboxRecord:
    payload = {
        "request_id": "batch:b1:i1",
        "batch_id": "b1",
        "item_id": "i1",
        "api_key": "key-a",
        "user_id": "user-1",
        "team_id": "team-1",
        "organization_id": "org-1",
        "model": "text-embedding-3-small",
        "call_type": "embedding_batch",
        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        "billed_cost": 0.01,
        "provider_cost": 0.02,
        "api_provider": "openai",
        "api_base": "http://localhost:9090/v1",
        "deployment_model": "text-embedding-3-small",
        "execution_mode": "managed_internal",
        "completed_at": datetime.now(tz=UTC).isoformat(),
    }
    if payload_overrides:
        payload.update(payload_overrides)
    now = datetime.now(tz=UTC)
    return BatchCompletionOutboxRecord(
        completion_id=completion_id,
        batch_id="b1",
        item_id=str(payload["item_id"]),
        payload_json=payload,
        status=status,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        next_attempt_at=now,
        last_error=None,
        created_at=now,
        updated_at=now,
        processed_at=None,
        locked_by=None,
        lease_expires_at=None,
    )


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        del exc_type, exc, tb
        return False


class _FakeDB:
    def tx(self) -> _FakeTx:
        return _FakeTx()


class _FakeRepository:
    def __init__(
        self,
        records: dict[str, BatchCompletionOutboxRecord] | None = None,
        *,
        prisma=None,  # noqa: ANN001
        sent_calls: list[str] | None = None,
        retry_calls: list[dict] | None = None,
        failed_calls: list[dict] | None = None,
    ) -> None:
        self.records = records or {}
        self.prisma = prisma
        self.sent_calls = [] if sent_calls is None else sent_calls
        self.retry_calls = [] if retry_calls is None else retry_calls
        self.failed_calls = [] if failed_calls is None else failed_calls

    def with_prisma(self, prisma) -> _FakeRepository:  # noqa: ANN001
        return _FakeRepository(
            self.records,
            prisma=prisma,
            sent_calls=self.sent_calls,
            retry_calls=self.retry_calls,
            failed_calls=self.failed_calls,
        )

    async def claim_completion_outbox_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 25,
    ) -> list[BatchCompletionOutboxRecord]:
        del limit
        now = datetime.now(tz=UTC)
        claimed: list[BatchCompletionOutboxRecord] = []
        for completion_id, record in list(self.records.items()):
            eligible = (
                record.status in {BatchCompletionOutboxStatus.QUEUED, BatchCompletionOutboxStatus.RETRYING}
                and record.next_attempt_at is not None
                and record.next_attempt_at <= now
            ) or (
                record.status == BatchCompletionOutboxStatus.PROCESSING
                and record.lease_expires_at is not None
                and record.lease_expires_at < now
            )
            if not eligible:
                continue
            claimed_record = replace(
                record,
                status=BatchCompletionOutboxStatus.PROCESSING,
                attempt_count=record.attempt_count + 1,
                locked_by=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
            self.records[completion_id] = claimed_record
            claimed.append(claimed_record)
        return claimed

    async def mark_completion_outbox_sent(self, completion_id: str, *, worker_id: str) -> bool:
        record = self.records[completion_id]
        if record.status != BatchCompletionOutboxStatus.PROCESSING or record.locked_by != worker_id:
            return False
        self.records[completion_id] = replace(
            record,
            status=BatchCompletionOutboxStatus.SENT,
            last_error=None,
            processed_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            locked_by=None,
            lease_expires_at=None,
        )
        self.sent_calls.append(completion_id)
        return True

    async def mark_completion_outbox_retry(
        self,
        completion_id: str,
        *,
        worker_id: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        record = self.records[completion_id]
        if record.status != BatchCompletionOutboxStatus.PROCESSING or record.locked_by != worker_id:
            return False
        self.records[completion_id] = replace(
            record,
            status=BatchCompletionOutboxStatus.RETRYING,
            last_error=error,
            next_attempt_at=next_attempt_at,
            updated_at=datetime.now(tz=UTC),
            locked_by=None,
            lease_expires_at=None,
        )
        self.retry_calls.append(
            {
                "completion_id": completion_id,
                "error": error,
                "next_attempt_at": next_attempt_at,
            }
        )
        return True

    async def mark_completion_outbox_failed(self, completion_id: str, *, worker_id: str, error: str) -> bool:
        record = self.records[completion_id]
        if record.status != BatchCompletionOutboxStatus.PROCESSING or record.locked_by != worker_id:
            return False
        self.records[completion_id] = replace(
            record,
            status=BatchCompletionOutboxStatus.FAILED,
            last_error=error,
            updated_at=datetime.now(tz=UTC),
            locked_by=None,
            lease_expires_at=None,
        )
        self.failed_calls.append({"completion_id": completion_id, "error": error})
        return True

    async def renew_completion_outbox_lease(self, *, completion_id: str, worker_id: str, lease_seconds: int) -> bool:
        record = self.records[completion_id]
        if record.status != BatchCompletionOutboxStatus.PROCESSING or record.locked_by != worker_id:
            return False
        self.records[completion_id] = replace(
            record,
            lease_expires_at=datetime.now(tz=UTC) + timedelta(seconds=lease_seconds),
            updated_at=datetime.now(tz=UTC),
        )
        return True


class _FakeSpendTrackingService:
    def __init__(
        self,
        *,
        fail: bool = False,
        outcome: str = "inserted",
        calls: list[dict] | None = None,
        bound_dbs: list[object] | None = None,
    ) -> None:
        self.fail = fail
        self.outcome = outcome
        self.calls = [] if calls is None else calls
        self.bound_dbs = [] if bound_dbs is None else bound_dbs
        self._db = None

    def with_db(self, db) -> _FakeSpendTrackingService:  # noqa: ANN001
        clone = _FakeSpendTrackingService(
            fail=self.fail,
            outcome=self.outcome,
            calls=self.calls,
            bound_dbs=self.bound_dbs,
        )
        clone._db = db
        return clone

    async def log_spend_once(self, **kwargs) -> str:
        call = dict(kwargs)
        call["db"] = self._db
        self.calls.append(call)
        if self._db is not None:
            self.bound_dbs.append(self._db)
        if self.fail:
            raise RuntimeError("spend log unavailable")
        return self.outcome


@pytest.mark.asyncio
async def test_completion_outbox_worker_marks_sent_and_records_spend_once(monkeypatch: pytest.MonkeyPatch) -> None:
    request_calls: list[dict] = []
    usage_calls: list[dict] = []
    spend_calls: list[dict] = []
    monkeypatch.setattr("src.batch.completion_outbox.increment_request", lambda **kwargs: request_calls.append(kwargs))
    monkeypatch.setattr("src.batch.completion_outbox.increment_usage", lambda **kwargs: usage_calls.append(kwargs))
    monkeypatch.setattr("src.batch.completion_outbox.increment_spend", lambda **kwargs: spend_calls.append(kwargs))

    record = _build_record()
    repository = _FakeRepository({record.completion_id: record}, prisma=_FakeDB())
    spend_tracking_service = _FakeSpendTrackingService()
    worker = BatchCompletionOutboxWorker(
        app=SimpleNamespace(state=SimpleNamespace(spend_tracking_service=spend_tracking_service)),
        repository=repository,  # type: ignore[arg-type]
        config=BatchCompletionOutboxWorkerConfig(max_batch_size=10, max_concurrency=1),
    )

    processed = await worker.process_once()

    assert processed == 1
    assert repository.records[record.completion_id].status == BatchCompletionOutboxStatus.SENT
    assert repository.sent_calls == [record.completion_id]
    assert len(spend_tracking_service.calls) == 1
    assert spend_tracking_service.calls[0]["event_id"] == record.completion_id
    assert spend_tracking_service.calls[0]["request_id"] == "batch:b1:i1"
    assert spend_tracking_service.bound_dbs and spend_tracking_service.bound_dbs[0] is not None
    assert request_calls == [
        {
            "model": "text-embedding-3-small",
            "api_provider": "openai",
            "api_key": "key-a",
            "user": "user-1",
            "team": "team-1",
            "status_code": 200,
        }
    ]
    assert usage_calls == [
        {
            "model": "text-embedding-3-small",
            "api_provider": "openai",
            "api_key": "key-a",
            "user": "user-1",
            "team": "team-1",
            "prompt_tokens": 5,
            "completion_tokens": 0,
        }
    ]
    assert spend_calls == [
        {
            "model": "text-embedding-3-small",
            "api_provider": "openai",
            "api_key": "key-a",
            "user": "user-1",
            "team": "team-1",
            "spend": 0.01,
        }
    ]
    assert repository.retry_calls == []
    assert repository.failed_calls == []


@pytest.mark.asyncio
async def test_completion_outbox_worker_marks_retry_on_transient_failure() -> None:
    record = _build_record(max_attempts=2)
    repository = _FakeRepository({record.completion_id: record})
    worker = BatchCompletionOutboxWorker(
        app=SimpleNamespace(state=SimpleNamespace(spend_tracking_service=_FakeSpendTrackingService(fail=True))),
        repository=repository,  # type: ignore[arg-type]
        config=BatchCompletionOutboxWorkerConfig(max_batch_size=10, max_concurrency=1, retry_initial_seconds=7),
    )

    processed = await worker.process_once()

    assert processed == 1
    stored = repository.records[record.completion_id]
    assert stored.status == BatchCompletionOutboxStatus.RETRYING
    assert stored.attempt_count == 1
    assert stored.last_error == "spend log unavailable"
    assert repository.retry_calls and repository.retry_calls[0]["completion_id"] == record.completion_id
    assert repository.failed_calls == []


@pytest.mark.asyncio
async def test_completion_outbox_worker_marks_failed_after_max_attempts() -> None:
    record = _build_record(max_attempts=1)
    repository = _FakeRepository({record.completion_id: record})
    worker = BatchCompletionOutboxWorker(
        app=SimpleNamespace(state=SimpleNamespace(spend_tracking_service=_FakeSpendTrackingService(fail=True))),
        repository=repository,  # type: ignore[arg-type]
        config=BatchCompletionOutboxWorkerConfig(max_batch_size=10, max_concurrency=1),
    )

    processed = await worker.process_once()

    assert processed == 1
    stored = repository.records[record.completion_id]
    assert stored.status == BatchCompletionOutboxStatus.FAILED
    assert stored.attempt_count == 1
    assert stored.last_error == "spend log unavailable"
    assert repository.retry_calls == []
    assert repository.failed_calls == [{"completion_id": record.completion_id, "error": "spend log unavailable"}]


@pytest.mark.asyncio
async def test_completion_outbox_worker_treats_duplicate_spend_event_as_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    request_calls: list[dict] = []
    monkeypatch.setattr("src.batch.completion_outbox.increment_request", lambda **kwargs: request_calls.append(kwargs))
    monkeypatch.setattr("src.batch.completion_outbox.increment_usage", lambda **kwargs: None)
    monkeypatch.setattr("src.batch.completion_outbox.increment_spend", lambda **kwargs: None)

    record = _build_record()
    repository = _FakeRepository({record.completion_id: record}, prisma=_FakeDB())
    worker = BatchCompletionOutboxWorker(
        app=SimpleNamespace(state=SimpleNamespace(spend_tracking_service=_FakeSpendTrackingService(outcome="duplicate"))),
        repository=repository,  # type: ignore[arg-type]
        config=BatchCompletionOutboxWorkerConfig(max_batch_size=10, max_concurrency=1),
    )

    processed = await worker.process_once()

    assert processed == 1
    assert repository.records[record.completion_id].status == BatchCompletionOutboxStatus.SENT
    assert repository.retry_calls == []
    assert repository.failed_calls == []
    assert len(request_calls) == 1


@pytest.mark.asyncio
async def test_completion_outbox_worker_reclaims_expired_processing_record() -> None:
    now = datetime.now(tz=UTC)
    record = replace(
        _build_record(status=BatchCompletionOutboxStatus.PROCESSING, attempt_count=1),
        locked_by="worker-old",
        lease_expires_at=now - timedelta(seconds=1),
        updated_at=now,
    )
    repository = _FakeRepository({record.completion_id: record}, prisma=_FakeDB())
    spend_tracking_service = _FakeSpendTrackingService()
    worker = BatchCompletionOutboxWorker(
        app=SimpleNamespace(state=SimpleNamespace(spend_tracking_service=spend_tracking_service)),
        repository=repository,  # type: ignore[arg-type]
        config=BatchCompletionOutboxWorkerConfig(worker_id="worker-new", max_batch_size=10, max_concurrency=1),
    )

    processed = await worker.process_once()

    stored = repository.records[record.completion_id]
    assert processed == 1
    assert stored.status == BatchCompletionOutboxStatus.SENT
    assert repository.sent_calls == [record.completion_id]
