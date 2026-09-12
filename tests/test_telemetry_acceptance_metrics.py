from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json

import httpx
from prisma.errors import DataError, RawQueryError, TransactionError, TransactionExpiredError
import pytest

from src.db.audit_ingestion import AuditIngestionRepository, AuditOutboxEnvelope
from src.db.spend_ingestion import SpendIngestionRepository
from src.db.telemetry_acceptance import (
    AcceptanceFailure,
    TelemetryDatabaseUnavailable,
    classify_acceptance_failure,
)
from src.metrics.prometheus import get_prometheus_registry
from src.metrics.telemetry_acceptance import AcceptancePhase


def sample(name: str, **labels: str) -> float:
    return get_prometheus_registry().get_sample_value(name, labels) or 0.0


def operation(queue: str, outcome: str, scope: str = "owned") -> float:
    return sample(
        "deltallm_telemetry_acceptance_operations_total",
        queue=queue,
        outcome=outcome,
        transaction_scope=scope,
    )


def data_error(code: str, sqlstate: str | None = None) -> DataError:
    data = {
        "user_facing_error": {
            "error_code": code,
            "message": "private database URL, key and prompt",
            "meta": {"code": sqlstate, "message": "private database URL, key and prompt"},
        }
    }
    return RawQueryError(data) if sqlstate is not None else DataError(data)


class Database:
    def __init__(
        self,
        queue: str,
        *,
        block: str | None = None,
        fail: str | None = None,
        failure: BaseException | None = None,
        status: str = "accepted",
    ) -> None:
        self.queue = queue
        self.block = block
        self.fail = fail
        self.failure = failure or data_error("P2024")
        self.status = status
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []
        self.query_count = 0

    def is_transaction(self) -> bool:
        return False

    async def checkpoint(self, stage: str) -> None:
        self.calls.append(stage)
        if self.block == stage:
            self.entered.set()
            await self.release.wait()
        if self.fail == stage:
            raise self.failure

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[Transaction]:
        await self.checkpoint("acquire")
        try:
            yield Transaction(self)
        except BaseException:
            await self.checkpoint("rollback")
            raise
        else:
            await self.checkpoint("commit")

    async def query_raw(self, query: str, *args: object) -> list[dict[str, object]]:
        self.query_count += 1
        if "pg_advisory_xact_lock" in query:
            await self.checkpoint("lock")
            return []
        await self.checkpoint("sql")
        if self.queue == "audit":
            return [
                {"event_id": item["event_id"], "status": self.status, "pending_count": 1}
                for item in json.loads(str(args[0]))
            ]
        return [
            {
                "accepted": self.status == "accepted",
                "duplicate": self.status == "duplicate",
                "pending_count": 1,
            }
        ]


class Transaction:
    def __init__(self, database: Database) -> None:
        self.database = database

    def is_transaction(self) -> bool:
        return True

    async def query_raw(self, query: str, *args: object) -> list[dict[str, object]]:
        return await self.database.query_raw(query, *args)


async def enqueue(db: Database | Transaction | None, queue: str) -> None:
    if queue == "audit":
        await AuditIngestionRepository(db).enqueue_bundle(
            envelopes=[
                AuditOutboxEnvelope(
                    event_id="event",
                    record_type="audit_event",
                    organization_id="private-tenant",
                    delivery_class="required",
                    payload={"prompt": "private content"},
                    redacted_payload={},
                    max_attempts=3,
                )
            ],
            max_pending_events=10,
            required_reserve=2,
        )
    else:
        await SpendIngestionRepository(db).enqueue(
            event_id="event",
            event_type="spend",
            payload={"private_token": "private content"},
            max_attempts=3,
            max_pending_events=10,
        )


def assert_released(queue: str) -> None:
    for phase in AcceptancePhase:
        assert (
            sample("deltallm_telemetry_acceptance_in_flight", queue=queue, phase=phase.value) == 0
        )
    assert sample("deltallm_telemetry_acceptance_serialized_bytes", queue=queue) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["audit", "spend"])
async def test_missing_database_is_explicit_and_releases_observation(queue: str) -> None:
    labels = {"queue": queue, "phase": "prepare", "reason": "database_unavailable"}
    before = sample("deltallm_telemetry_acceptance_failures_total", **labels)
    with pytest.raises(TelemetryDatabaseUnavailable):
        await enqueue(None, queue)
    assert sample("deltallm_telemetry_acceptance_failures_total", **labels) == before + 1
    assert_released(queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["audit", "spend"])
async def test_acceptance_waits_for_commit_and_keeps_existing_query_budget(queue: str) -> None:
    db = Database(queue, block="commit")
    before = operation(queue, "accepted")
    before_events = sample("deltallm_telemetry_acceptance_events_per_commit_sum", queue=queue)
    task = asyncio.create_task(enqueue(db, queue))
    try:
        await asyncio.wait_for(db.entered.wait(), 1)
        assert not task.done()
        assert operation(queue, "accepted") == before
        assert sample("deltallm_telemetry_acceptance_in_flight", queue=queue, phase="commit") == 1
        assert sample("deltallm_telemetry_acceptance_serialized_bytes", queue=queue) > 0
        db.release.set()
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert operation(queue, "accepted") == before + 1
    assert (
        sample("deltallm_telemetry_acceptance_events_per_commit_sum", queue=queue)
        == before_events + 1
    )
    assert db.calls == ["acquire", "lock", "sql", "commit"]
    assert db.query_count == 2  # one lock statement, then a fresh-snapshot enqueue statement
    assert_released(queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["audit", "spend"])
@pytest.mark.parametrize("stage", ["acquire", "lock", "sql", "commit"])
async def test_failure_is_attributed_without_logging_database_error_text(
    queue: str, stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    db = Database(queue, fail=stage)
    before = operation(queue, "error")
    accepted = operation(queue, "accepted")
    labels = {"queue": queue, "phase": stage, "reason": "pool_timeout"}
    failures = sample("deltallm_telemetry_acceptance_failures_total", **labels)
    with pytest.raises(DataError) as raised:
        await enqueue(db, queue)
    assert raised.value is db.failure
    assert operation(queue, "error") == before + 1
    assert operation(queue, "accepted") == accepted
    assert sample("deltallm_telemetry_acceptance_failures_total", **labels) == failures + 1
    record = next(r for r in caplog.records if r.message.startswith("telemetry acceptance failed"))
    assert record.phase == stage
    assert record.reason == "pool_timeout"
    assert record.exc_info is None
    assert "private" not in repr(record.__dict__)
    assert_released(queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["audit", "spend"])
@pytest.mark.parametrize("stage", ["acquire", "lock", "sql", "commit"])
async def test_cancellation_releases_metrics_and_preserves_transaction_cleanup(
    queue: str, stage: str
) -> None:
    db = Database(queue, block=stage)
    before = operation(queue, "cancelled")
    accepted = operation(queue, "accepted")
    task = asyncio.create_task(enqueue(db, queue))
    try:
        await asyncio.wait_for(db.entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        db.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert operation(queue, "cancelled") == before + 1
    assert operation(queue, "accepted") == accepted
    if stage in {"lock", "sql"}:
        assert db.calls[-1] == "rollback"
    assert_released(queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["audit", "spend"])
async def test_external_transaction_does_not_report_commit(queue: str) -> None:
    db = Database(queue)
    before = operation(queue, "accepted", "external")
    owned = operation(queue, "accepted")
    commits = sample("deltallm_telemetry_acceptance_events_per_commit_count", queue=queue)
    await enqueue(Transaction(db), queue)
    assert db.calls == ["lock", "sql"]
    assert operation(queue, "accepted", "external") == before + 1
    assert operation(queue, "accepted") == owned
    assert sample("deltallm_telemetry_acceptance_events_per_commit_count", queue=queue) == commits
    assert_released(queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["audit", "spend"])
@pytest.mark.parametrize("status", ["duplicate", "full"])
async def test_duplicate_and_capacity_outcomes_are_not_new_accepted_events(
    queue: str, status: str
) -> None:
    db = Database(queue, status=status)
    before = operation(queue, status)
    events = sample("deltallm_telemetry_acceptance_events_per_commit_sum", queue=queue)
    await enqueue(db, queue)
    assert operation(queue, status) == before + 1
    assert sample("deltallm_telemetry_acceptance_events_per_commit_sum", queue=queue) == events
    assert_released(queue)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (data_error("P2024"), AcceptanceFailure.POOL_TIMEOUT),
        (data_error("P2010", "55P03"), AcceptanceFailure.LOCK_TIMEOUT),
        (data_error("P2010", "57014"), AcceptanceFailure.STATEMENT_CANCELLED),
        (data_error("P2010", "08006"), AcceptanceFailure.CONNECTION),
        (data_error("P1001"), AcceptanceFailure.CONNECTION),
        (data_error("P1008"), AcceptanceFailure.DEADLINE),
        (data_error("P2028"), AcceptanceFailure.TRANSACTION_ERROR),
        (TransactionExpiredError("private"), AcceptanceFailure.TRANSACTION_EXPIRED),
        (TransactionError("private"), AcceptanceFailure.TRANSACTION_ERROR),
        (httpx.PoolTimeout("private"), AcceptanceFailure.CLIENT_POOL_TIMEOUT),
        (httpx.ConnectError("private"), AcceptanceFailure.CONNECTION),
        (TimeoutError("private"), AcceptanceFailure.DEADLINE),
        (asyncio.CancelledError(), AcceptanceFailure.CANCELLED),
        (RuntimeError("P2024 private"), AcceptanceFailure.UNKNOWN),
        (data_error("attacker-controlled"), AcceptanceFailure.UNKNOWN),
        (
            DataError({"user_facing_error": {"error_code": [], "meta": []}}),
            AcceptanceFailure.UNKNOWN,
        ),
    ],
)
def test_failure_classification_uses_only_structured_codes(
    exc: BaseException, expected: AcceptanceFailure
) -> None:
    assert classify_acceptance_failure(exc) == expected
