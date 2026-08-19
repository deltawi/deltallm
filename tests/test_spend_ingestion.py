import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.billing.spend import PreparedSpendEvent, SpendTrackingService
from src.billing.fallback_gate import BoundedFallbackGate, FallbackGateFull, FallbackGateTimedOut
from src.billing.money import canonical_money, money_string
from src.billing.spend_ingestion import (
    SpendIngestionConfig,
    SpendIngestionOverloadedError,
    SpendIngestionService,
    _OutboxRecord,
)
from src.db.spend_ingestion import SpendIngestionRepository
from src.telemetry.event_identity import get_or_create_billing_event_id
from src.telemetry.lifecycle import WorkerState


class _OutboxDB:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.tx_calls = 0

    @asynccontextmanager
    async def tx(self):  # noqa: ANN201
        self.tx_calls += 1
        yield self

    async def execute_raw(self, query: str, *params: object) -> int:
        self.executions.append((query, params))
        return 1

    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]:
        self.executions.append((query, params))
        if "AS duplicate" in query:
            return [{"duplicate": False, "accepted": True, "pending_count": 1}]
        if "SELECT pending_count" in query and "FOR UPDATE" in query:
            return [{"pending_count": 0}]
        if "COUNT(*)::bigint AS count" in query:
            return [{"count": 0, "oldest_age": 0.0}]
        if "SET pending_count = $1" in query:
            return [{"pending_count": params[0]}]
        if "SELECT event_id FROM completed" in query:
            event_ids = params[0] if params else []
            return [{"event_id": event_id} for event_id in event_ids]  # type: ignore[union-attr]
        if "SELECT event_id FROM transitioned" in query:
            return [{"event_id": params[0]}]
        return []


class _Writer:
    def __init__(self) -> None:
        self.spend_events: list[dict[str, object]] = []
        self.failure_events: list[dict[str, object]] = []
        self.batch_calls = 0

    def with_db(self, db):  # noqa: ANN001, ANN201
        del db
        return self

    async def log_spend_once(self, **kwargs):  # noqa: ANN003, ANN201
        self.spend_events.append(kwargs)
        return "inserted"

    async def log_request_failure_once(self, **kwargs):  # noqa: ANN003, ANN201
        self.failure_events.append(kwargs)
        return "inserted"

    async def log_batch_once(self, events):  # noqa: ANN001, ANN201
        prepared = [
            self.prepare_batch_event(event_id=event_id, event_type=event_type, payload=payload)
            for event_id, event_type, payload in events
        ]
        return await self.log_prepared_batch_once(prepared)

    def prepare_batch_event(self, *, event_id, event_type, payload):  # noqa: ANN001, ANN201
        if event_type not in {"spend", "request_failure"}:
            raise ValueError(f"unsupported spend outbox event type: {event_type}")
        return PreparedSpendEvent(
            event_id=event_id,
            event_type=event_type,
            row={"id": event_id, **payload},
            event_entry=dict(payload),
        )

    async def log_prepared_batch_once(self, events):  # noqa: ANN001, ANN201
        self.batch_calls += 1
        for event in events:
            target = self.spend_events if event.event_type == "spend" else self.failure_events
            target.append({"event_id": event.event_id, **event.event_entry})
        return {event.event_id for event in events}, {
            "api_key": 0,
            "user": 0,
            "team": 0,
            "organization": 0,
            "team_model": 0,
        }


def _spend_payload() -> dict[str, object]:
    now = datetime.now(tz=UTC)
    return {
        "request_id": "req-1",
        "api_key": "key-1",
        "user_id": None,
        "team_id": "team-1",
        "organization_id": "org-1",
        "end_user_id": None,
        "model": "model-1",
        "call_type": "completion",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "cost": 0.01,
        "start_time": now,
        "end_time": now,
    }


@pytest.mark.asyncio
async def test_outbox_enqueue_uses_server_owned_event_ids() -> None:
    db = _OutboxDB()
    service = SpendIngestionService(
        db_client=db,
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True),
    )

    await service.log_spend(**_spend_payload())
    await service.log_spend(**_spend_payload())

    admission_calls = [call for call in db.executions if "AS duplicate" in call[0]]
    assert db.tx_calls == 2
    assert len(admission_calls) == 2
    assert admission_calls[0][1][0] != admission_calls[1][1][0]
    assert "ON CONFLICT (event_id) DO NOTHING" in admission_calls[0][0]
    assert all("pg_advisory_xact_lock" not in query for query, _params in admission_calls)


@pytest.mark.asyncio
async def test_internal_enqueue_retry_reuses_explicit_server_event_id() -> None:
    db = _OutboxDB()
    service = SpendIngestionService(
        db_client=db,
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True),
    )

    await service._enqueue("spend", _spend_payload(), event_id="server-event-1")
    await service._enqueue("spend", _spend_payload(), event_id="server-event-1")

    admission_calls = [call for call in db.executions if "AS duplicate" in call[0]]
    assert admission_calls[0][1][0] == "server-event-1"
    assert admission_calls[1][1][0] == "server-event-1"


def test_request_billing_event_id_is_stable_and_ignores_client_request_id() -> None:
    first = SimpleNamespace(
        state=SimpleNamespace(),
        headers={"x-request-id": "client-controlled"},
    )
    second = SimpleNamespace(
        state=SimpleNamespace(),
        headers={"x-request-id": "client-controlled"},
    )

    first_event_id = get_or_create_billing_event_id(first)

    assert get_or_create_billing_event_id(first) == first_event_id
    assert get_or_create_billing_event_id(second) != first_event_id
    assert str(UUID(first_event_id)) == first_event_id


@pytest.mark.asyncio
async def test_outbox_replay_is_delivered_with_same_event_id() -> None:
    db = _OutboxDB()
    writer = _Writer()
    service = SpendIngestionService(
        db_client=db,
        writer=writer,  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True, worker_id="worker-1"),
    )
    payload = _spend_payload()
    payload["start_time"] = payload["start_time"].isoformat()  # type: ignore[union-attr]
    payload["end_time"] = payload["end_time"].isoformat()  # type: ignore[union-attr]

    await service._process(
        _OutboxRecord(
            event_id="event-1",
            event_type="spend",
            payload=payload,
            attempt_count=1,
        )
    )

    assert writer.spend_events[0]["event_id"] == "event-1"
    assert any("status = 'completed'" in query for query, _ in db.executions)


@pytest.mark.asyncio
async def test_claimed_records_are_persisted_with_one_batch_writer_call() -> None:
    db = _OutboxDB()
    writer = _Writer()
    service = SpendIngestionService(
        db_client=db,
        writer=writer,  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True, worker_id="worker-1"),
    )
    payload = _spend_payload()

    await service._process_batch(
        [
            _OutboxRecord("event-1", "spend", payload, 1),
            _OutboxRecord("event-2", "spend", payload, 1),
        ]
    )

    assert writer.batch_calls == 1
    assert [event["event_id"] for event in writer.spend_events] == ["event-1", "event-2"]
    acknowledgements = [
        params for query, params in db.executions if "SELECT event_id FROM completed" in query
    ]
    assert acknowledgements == [(["event-1", "event-2"], "worker-1", "unfenced-test-claim")]


class _RecordSpecificFailure(RuntimeError):
    sqlstate = "22003"


class _PoisonBatchWriter(_Writer):
    def __init__(self) -> None:
        super().__init__()
        self.attempted_batches: list[list[str]] = []

    async def log_prepared_batch_once(self, events):  # noqa: ANN001, ANN201
        event_ids = [event.event_id for event in events]
        self.attempted_batches.append(event_ids)
        if "poison" in event_ids:
            raise _RecordSpecificFailure("integer out of range")
        return await super().log_prepared_batch_once(events)


@pytest.mark.asyncio
async def test_poison_spend_record_isolated_without_retrying_neighbors() -> None:
    db = _OutboxDB()
    writer = _PoisonBatchWriter()
    service = SpendIngestionService(
        db_client=db,
        writer=writer,  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True, worker_id="worker-1"),
    )
    payload = _spend_payload()

    await service._process_batch(
        [
            _OutboxRecord("good-1", "spend", payload, 1),
            _OutboxRecord("poison", "spend", payload, 1),
            _OutboxRecord("good-2", "spend", payload, 1),
        ]
    )

    completed_ids = [
        event_id
        for query, params in db.executions
        if "SELECT event_id FROM completed" in query
        for event_id in params[0]  # type: ignore[union-attr]
    ]
    retried_ids = [
        params[0] for query, params in db.executions if "SELECT event_id FROM transitioned" in query
    ]
    assert completed_ids == ["good-1", "good-2"]
    assert retried_ids == ["poison"]
    assert writer.attempted_batches[0] == ["good-1", "poison", "good-2"]


@pytest.mark.asyncio
async def test_invalid_spend_payload_is_retried_before_bulk_transaction() -> None:
    db = _OutboxDB()
    service = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        config=SpendIngestionConfig(enabled=True, worker_id="worker-1"),
    )
    invalid = _spend_payload()
    invalid["usage"] = "not-an-object"

    await service._process_batch(
        [
            _OutboxRecord("good", "spend", _spend_payload(), 1),
            _OutboxRecord("invalid", "spend", invalid, 1),
        ]
    )

    acknowledgements = [
        params[0] for query, params in db.executions if "SELECT event_id FROM completed" in query
    ]
    retried_ids = [
        params[0] for query, params in db.executions if "SELECT event_id FROM transitioned" in query
    ]
    assert acknowledgements == [["good"]]
    assert retried_ids == ["invalid"]


class _FullOutboxDB(_OutboxDB):
    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]:
        self.executions.append((query, params))
        if "AS duplicate" in query:
            return [{"duplicate": False, "accepted": False, "pending_count": 10}]
        return await super().query_raw(query, *params)


@pytest.mark.asyncio
async def test_full_outbox_uses_bounded_synchronous_batch_fallback() -> None:
    db = _FullOutboxDB()
    writer = _Writer()
    service = SpendIngestionService(
        db_client=db,
        writer=writer,  # type: ignore[arg-type]
        config=SpendIngestionConfig(
            enabled=True,
            max_pending_events=10,
            overload_policy="sync_fallback",
        ),
    )

    await service.log_spend(**_spend_payload())

    assert writer.batch_calls == 1
    assert len(writer.spend_events) == 1


@pytest.mark.asyncio
async def test_fallback_gate_rejects_requests_beyond_bounded_waiters() -> None:
    gate = BoundedFallbackGate(concurrency=1, max_waiters=1)
    await gate.acquire(timeout_seconds=1)
    waiter = asyncio.create_task(gate.acquire(timeout_seconds=1))
    while gate.waiters != 1:
        await asyncio.sleep(0)

    with pytest.raises(FallbackGateFull):
        await gate.acquire(timeout_seconds=1)

    await gate.release()
    await waiter
    await gate.release()
    assert gate.active == 0
    assert gate.waiters == 0


@pytest.mark.asyncio
async def test_fallback_gate_bounds_queue_wait_time() -> None:
    gate = BoundedFallbackGate(concurrency=1, max_waiters=1)
    await gate.acquire(timeout_seconds=1)

    with pytest.raises(FallbackGateTimedOut):
        await gate.acquire(timeout_seconds=0.001)

    await gate.release()
    assert gate.active == 0
    assert gate.waiters == 0


class _StuckFallbackWriter(_Writer):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def log_batch_once(self, events):  # noqa: ANN001, ANN201
        del events
        self.started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_synchronous_fallback_has_execution_deadline() -> None:
    writer = _StuckFallbackWriter()
    service = SpendIngestionService(
        db_client=_FullOutboxDB(),
        writer=writer,  # type: ignore[arg-type]
        config=SpendIngestionConfig(
            enabled=True,
            overload_policy="sync_fallback",
            fallback_execution_timeout_seconds=0.01,
        ),
    )

    with pytest.raises(SpendIngestionOverloadedError, match="execution deadline"):
        await service.log_spend(**_spend_payload())

    assert writer.started.is_set()
    assert service._fallback_gate.active == 0


class _ExactBatchDB(_OutboxDB):
    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]:
        self.executions.append((query, params))
        if "INSERT INTO deltallm_spendlog_events" in query:
            return [{"id": "event-b"}, {"id": "event-a"}]
        return []


@pytest.mark.asyncio
async def test_spend_batch_aggregates_money_exactly_and_binds_numeric_arrays() -> None:
    db = _ExactBatchDB()
    service = SpendTrackingService(db)
    first = _spend_payload()
    first["cost_exact"] = "0.100000000000000000"
    second = _spend_payload()
    second["cost_exact"] = "0.200000000000000000"

    prepared = [
        service.prepare_batch_event(event_id="event-a", event_type="spend", payload=first),
        service.prepare_batch_event(event_id="event-b", event_type="spend", payload=second),
    ]
    inserted, _ = await service.log_prepared_batch_once(prepared)

    assert inserted == {"event-a", "event-b"}
    key_update = next(
        (query, params)
        for query, params in db.executions
        if "UPDATE deltallm_verificationtoken" in query
    )
    assert "$2::numeric[]" in key_update[0]
    assert key_update[1][1] == ["0.300000000000000000"]
    assert canonical_money("0.1") + canonical_money("0.2") == Decimal("0.300000000000000000")


def test_money_canonicalization_supports_full_numeric_38_18_domain() -> None:
    assert money_string("99999999999999999999.999999999999999999") == (
        "99999999999999999999.999999999999999999"
    )
    with pytest.raises(ValueError, match=r"NUMERIC\(38,18\)"):
        canonical_money("100000000000000000000")


@pytest.mark.asyncio
async def test_fail_closed_overload_returns_service_unavailable_error() -> None:
    service = SpendIngestionService(
        db_client=_FullOutboxDB(),
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(
            enabled=True,
            max_pending_events=10,
            overload_policy="fail_closed",
        ),
    )

    with pytest.raises(SpendIngestionOverloadedError) as raised:
        await service.log_spend(**_spend_payload())

    assert raised.value.status_code == 503
    assert raised.value.code == "spend_ingestion_capacity"


class _FailingTransaction:
    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]:
        del query, params
        return [{"id": "event-1"}]

    async def execute_raw(self, query: str, *params: object) -> int:
        del query, params
        raise RuntimeError("ledger update failed")


class _TransactionalDB:
    def __init__(self) -> None:
        self.rolled_back = False

    @asynccontextmanager
    async def tx(self):  # noqa: ANN201
        try:
            yield _FailingTransaction()
        except Exception:
            self.rolled_back = True
            raise


class _RealisticTransactionClient:
    def __init__(self) -> None:
        self.tx_calls = 0
        self.queries: list[str] = []
        self.executions: list[str] = []

    def is_transaction(self) -> bool:
        return True

    def tx(self):  # noqa: ANN201
        self.tx_calls += 1
        raise AssertionError("transaction client must not open a nested transaction")

    async def query_raw(self, query: str, *_params: object) -> list[dict[str, object]]:
        self.queries.append(query)
        return [{"id": "event-1"}]

    async def execute_raw(self, query: str, *_params: object) -> int:
        self.executions.append(query)
        return 1


class _FailingRealisticTransactionClient(_RealisticTransactionClient):
    async def execute_raw(self, query: str, *_params: object) -> int:
        self.executions.append(query)
        raise RuntimeError("transaction ledger update failed")


@pytest.mark.asyncio
async def test_spend_event_and_ledger_failure_roll_back_together() -> None:
    db = _TransactionalDB()
    service = SpendTrackingService(db)

    with pytest.raises(RuntimeError, match="ledger update failed"):
        await service.log_spend_once(event_id="event-1", **_spend_payload())

    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_realistic_transaction_client_does_not_open_nested_transaction() -> None:
    tx = _RealisticTransactionClient()
    service = SpendTrackingService(tx)

    outcome = await service.log_spend_once(event_id="event-1", **_spend_payload())

    assert outcome == "inserted"
    assert tx.tx_calls == 0
    assert len(tx.queries) == 1
    assert tx.executions


@pytest.mark.asyncio
async def test_existing_transaction_client_keeps_ledger_failures_strict() -> None:
    tx = _FailingRealisticTransactionClient()
    service = SpendTrackingService(tx)

    with pytest.raises(RuntimeError, match="transaction ledger update failed"):
        await service.log_spend_once(event_id="event-1", **_spend_payload())

    assert tx.tx_calls == 0
    assert len(tx.queries) == 1


class _CleanupRepository:
    def __init__(self, results: list[int]) -> None:
        self.results = list(results)
        self.calls = 0

    async def cleanup_terminal(self, **_kwargs):  # noqa: ANN003, ANN201
        self.calls += 1
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_cleanup_drains_multiple_pages_within_run_budget() -> None:
    service = SpendIngestionService(
        db_client=object(),
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(
            enabled=True,
            cleanup_batch_size=1000,
            cleanup_max_batches_per_run=10,
            cleanup_time_budget_seconds=10,
        ),
    )
    repository = _CleanupRepository([1000, 1000, 20])
    service.repository = repository  # type: ignore[assignment]

    deleted = await service._cleanup_terminal()

    assert deleted == 2020
    assert repository.calls == 3


@pytest.mark.asyncio
async def test_cleanup_task_runs_while_ingestion_is_idle() -> None:
    db = _OutboxDB()
    service = SpendIngestionService(
        db_client=db,
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(
            enabled=True,
            flush_interval_seconds=0.01,
            cleanup_interval_seconds=0.01,
        ),
    )

    await service.start()
    await asyncio.sleep(0.15)
    await service.shutdown()

    cleanup_queries = [
        query
        for query, _ in db.executions
        if "DELETE FROM deltallm_spend_ingestion_outbox" in query
    ]
    assert cleanup_queries
    assert "make_interval(hours => $1::int)" in cleanup_queries[0]
    assert "FOR UPDATE SKIP LOCKED" in cleanup_queries[0]


@pytest.mark.asyncio
async def test_spend_worker_role_can_be_enabled_and_disabled_live() -> None:
    service = SpendIngestionService(
        db_client=_OutboxDB(),
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(
            enabled=True,
            worker_enabled=False,
            flush_interval_seconds=0.01,
        ),
    )

    await service.start()
    assert service._worker is None

    await service.reconfigure(replace(service.config, worker_enabled=True))
    assert service._worker is not None
    assert service._running is True

    await service.reconfigure(replace(service.config, worker_enabled=False))
    assert service._worker is None
    assert service._cleanup_task is None
    assert service._running is False


@pytest.mark.asyncio
async def test_spend_start_is_idempotent_after_worker_becomes_ready() -> None:
    db = _OutboxDB()
    service = SpendIngestionService(
        db_client=db,
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True, flush_interval_seconds=0.01),
    )

    await service.start()
    worker = service._worker
    await service.start()

    assert service._worker is worker
    reconciliation_locks = [query for query, _ in db.executions if "pg_advisory_xact_lock" in query]
    assert len(reconciliation_locks) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_spend_shutdown_cancels_stuck_database_work_at_deadline() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    service = SpendIngestionService(
        db_client=_OutboxDB(),
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(
            enabled=True,
            flush_interval_seconds=0.01,
            shutdown_drain_timeout_seconds=0.02,
        ),
    )

    async def stuck_iteration() -> None:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    service._worker_iteration = stuck_iteration  # type: ignore[method-assign]
    await service.start()
    await entered.wait()
    worker = service._worker

    await asyncio.wait_for(service.shutdown(), timeout=0.1)

    assert service.worker_health.state is WorkerState.DISABLED
    assert worker is not None and not worker.done()
    release.set()
    await asyncio.wait_for(worker, timeout=0.1)


@pytest.mark.asyncio
async def test_spend_worker_health_reports_unexpected_task_exit() -> None:
    service = SpendIngestionService(
        db_client=_OutboxDB(),
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True, flush_interval_seconds=0.01),
    )
    await service.start()
    assert service.worker_health.state is WorkerState.READY
    assert service._worker is not None
    service._worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await service._worker

    assert service.worker_health.state is WorkerState.FAILED
    await service.shutdown()


@pytest.mark.asyncio
async def test_spend_ingestion_mode_change_requires_restart() -> None:
    service = SpendIngestionService(
        db_client=_OutboxDB(),
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
    )

    with pytest.raises(RuntimeError, match="requires a restart"):
        await service.reconfigure(replace(service.config, enabled=False))


@pytest.mark.asyncio
async def test_worker_loop_continues_after_unexpected_iteration_failure() -> None:
    service = SpendIngestionService(
        db_client=object(),
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True),
    )
    calls = 0

    async def iteration() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database unavailable")
        service._running = False

    service._worker_iteration = iteration  # type: ignore[method-assign]
    service._running = True

    await service._worker_loop()

    assert calls == 2


@pytest.mark.asyncio
async def test_retry_transition_failure_leaves_record_for_lease_recovery() -> None:
    service = SpendIngestionService(
        db_client=object(),
        writer=_Writer(),  # type: ignore[arg-type]
        config=SpendIngestionConfig(enabled=True),
    )

    async def fail_retry(_record, _exc):  # noqa: ANN001, ANN202
        raise RuntimeError("database unavailable")

    service._mark_retry = fail_retry  # type: ignore[method-assign]

    await service._safe_mark_retry(
        _OutboxRecord("event-1", "spend", _spend_payload(), 1),
        RuntimeError("processing failed"),
    )


class _CapacityTransaction:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def is_transaction(self) -> bool:
        return True

    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]:
        self.queries.append(query)
        if "FOR UPDATE" in query:
            return [{"pending_count": 99}]
        if "COUNT(*)" in query:
            return [{"count": 3}]
        if "UPDATE deltallm_telemetry_ingestion_capacity" in query:
            return [{"pending_count": params[0]}]
        return [{"locked": ""}]


class _CapacityRoot:
    def __init__(self) -> None:
        self.tx_client = _CapacityTransaction()
        self.tx_calls = 0

    @asynccontextmanager
    async def tx(self):  # noqa: ANN201
        self.tx_calls += 1
        yield self.tx_client


@pytest.mark.asyncio
async def test_capacity_reconciliation_locks_then_counts_in_fresh_statement() -> None:
    db = _CapacityRoot()

    reconciled = await SpendIngestionRepository(db).reconcile_capacity()

    assert reconciled == 3
    assert db.tx_calls == 1
    assert "pg_advisory_xact_lock" in db.tx_client.queries[0]
    assert "FOR UPDATE" in db.tx_client.queries[1]
    assert "COUNT(*)" in db.tx_client.queries[2]
    assert "UPDATE deltallm_telemetry_ingestion_capacity" in db.tx_client.queries[3]


@pytest.mark.asyncio
async def test_exhausted_spend_event_is_blocked_without_releasing_capacity() -> None:
    db = _OutboxDB()
    record = _OutboxRecord(
        "event-1",
        "spend",
        _spend_payload(),
        attempt_count=2,
        max_attempts=2,
        claim_token="claim-1",
    )

    terminal = await SpendIngestionRepository(db).mark_retry(
        record=record,
        worker_id="worker-1",
        error="poison payload",
    )

    assert terminal is True
    query, params = db.executions[-1]
    assert params[2] == "blocked"
    assert params[-1] == "claim-1"
    assert "telemetry_ingestion_capacity" not in query


@pytest.mark.asyncio
async def test_spend_replay_preserves_capacity_and_payload_identity() -> None:
    db = _OutboxDB()

    replayed = await SpendIngestionRepository(db).replay_blocked(
        event_id="event-1",
        replayed_by="admin-1",
    )

    assert replayed is False
    query, params = db.executions[-1]
    assert "status IN ('blocked', 'failed')" in query
    assert "payload_json" not in query
    assert "pending_count" not in query
    assert params == ("event-1", "admin-1")
