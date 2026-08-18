from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from typing import Any

import pytest
from prometheus_client import generate_latest

from src.db.repositories import AuditEventRecord, AuditPayloadRecord
from src.metrics import get_prometheus_registry
from src.services.audit_service import (
    AuditEventInput,
    AuditIngestionOverloadedError,
    AuditPayloadInput,
    AuditService,
    RequiredAuditPersistenceError,
    enqueue_audit_event,
)
from src.db.audit_ingestion import (
    AuditBundleEnqueueResult,
    AuditEnqueueResult,
    AuditIngestionRepository,
    AuditOutboxEnvelope,
    AuditOutboxRecord,
)
from src.services.audit_service import AuditIngestionConfig
from src.telemetry.lifecycle import WorkerState
from src.telemetry.prompt_render import PromptRenderEvent


class FakeAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEventRecord] = []
        self.payloads: list[AuditPayloadRecord] = []
        self.content_toggles: dict[str, bool] = {}
        self.content_toggle_reads = 0

    async def is_content_storage_enabled_for_org(self, organization_id: str | None) -> bool:
        self.content_toggle_reads += 1
        if not organization_id:
            return False
        return self.content_toggles.get(organization_id, False)

    async def create_event(self, record: AuditEventRecord) -> AuditEventRecord:
        event_id = record.event_id or f"evt-{len(self.events) + 1}"
        stored = AuditEventRecord(**{**record.__dict__, "event_id": event_id})
        self.events.append(stored)
        return stored

    async def create_payload(self, record: AuditPayloadRecord) -> AuditPayloadRecord:
        payload_id = f"pl-{len(self.payloads) + 1}"
        stored = AuditPayloadRecord(**{**record.__dict__, "payload_id": payload_id})
        self.payloads.append(stored)
        return stored

    async def create_events_batch(self, records: list[AuditEventRecord]) -> int:
        self.events.extend(records)
        return len(records)

    async def create_payloads_batch(self, records: list[AuditPayloadRecord]) -> int:
        self.payloads.extend(records)
        return len(records)

    def with_db(self, _db):  # noqa: ANN001, ANN201
        return self


class _FailingAuditRepository(FakeAuditRepository):
    async def create_event(self, record: AuditEventRecord) -> AuditEventRecord:
        raise ConnectionError("database unavailable")


@pytest.mark.asyncio
async def test_audit_service_enforces_org_content_toggle():
    repo = FakeAuditRepository()
    repo.content_toggles = {"org-enabled": True, "org-disabled": False}
    service = AuditService(repo)

    await service.record_event_sync(
        AuditEventInput(action="CHAT_COMPLETION", organization_id="org-enabled"),
        payloads=[
            AuditPayloadInput(
                kind="prompt", content_json={"messages": [{"role": "user", "content": "hello"}]}
            )
        ],
    )
    await service.record_event_sync(
        AuditEventInput(action="CHAT_COMPLETION", organization_id="org-disabled"),
        payloads=[
            AuditPayloadInput(
                kind="prompt", content_json={"messages": [{"role": "user", "content": "secret"}]}
            )
        ],
    )

    assert len(repo.events) == 2
    assert repo.events[0].content_stored is True
    assert repo.events[1].content_stored is False
    assert repo.payloads[0].content_json is not None
    assert repo.payloads[1].content_json is None
    assert repo.payloads[1].redacted is True


@pytest.mark.asyncio
async def test_audit_content_policy_caches_negative_and_invalidates() -> None:
    repo = FakeAuditRepository()
    service = AuditService(repo)

    for _ in range(2):
        await service.record_event_sync(
            AuditEventInput(action="CHAT_COMPLETION", organization_id="org-1"),
        )
    assert repo.content_toggle_reads == 1

    repo.content_toggles["org-1"] = True
    service.invalidate_content_storage_policy("org-1")
    await service.record_event_sync(
        AuditEventInput(action="CHAT_COMPLETION", organization_id="org-1"),
        payloads=[AuditPayloadInput(kind="request", content_json={"safe": True})],
    )
    assert repo.content_toggle_reads == 2
    assert repo.events[-1].content_stored is True


@pytest.mark.asyncio
async def test_audit_service_sync_write_can_use_transaction_bound_repository():
    default_repo = FakeAuditRepository()
    transactional_repo = FakeAuditRepository()
    transactional_repo.content_toggles = {"org-1": True}
    service = AuditService(default_repo)

    await service.record_event_sync(
        AuditEventInput(action="ADMIN_BATCH_WEBHOOK_REPLAY", organization_id="org-1"),
        payloads=[AuditPayloadInput(kind="response", content_json={"replayed": True})],
        repository=transactional_repo,  # type: ignore[arg-type]
    )

    assert default_repo.events == []
    assert default_repo.payloads == []
    assert [event.action for event in transactional_repo.events] == ["ADMIN_BATCH_WEBHOOK_REPLAY"]
    assert transactional_repo.payloads[0].content_json == {"replayed": True}


@pytest.mark.asyncio
async def test_audit_service_drops_non_critical_when_queue_full():
    repo = FakeAuditRepository()
    service = AuditService(repo, queue_max_size=1)

    service.record_event(AuditEventInput(action="FIRST", organization_id="org-1"), critical=False)
    service.record_event(AuditEventInput(action="SECOND", organization_id="org-1"), critical=False)
    assert service.dropped_events == 1

    await service.start()
    await asyncio.sleep(0.05)
    await service.shutdown()
    assert [event.action for event in repo.events] == ["FIRST"]


@pytest.mark.asyncio
async def test_required_audit_persists_before_legacy_enqueue_returns():
    repo = FakeAuditRepository()
    service = AuditService(repo, queue_max_size=1)

    result = await service.enqueue_event(
        AuditEventInput(action="SECOND", organization_id="org-1"),
        delivery_class="required",
    )

    assert result == "persisted"
    assert service.dropped_events == 0
    assert [event.action for event in repo.events] == ["SECOND"]


@pytest.mark.asyncio
async def test_required_audit_uses_caller_supplied_server_event_id() -> None:
    repo = FakeAuditRepository()
    service = AuditService(repo, queue_max_size=1)

    await service.enqueue_event(
        AuditEventInput(
            action="STABLE",
            organization_id="org-1",
            event_id="14d28d46-2951-52d1-9529-48f5c565de07",
        ),
        delivery_class="required",
    )

    assert repo.events[0].event_id == "14d28d46-2951-52d1-9529-48f5c565de07"


@pytest.mark.asyncio
async def test_required_legacy_audit_failure_fails_closed_without_queueing() -> None:
    service = AuditService(_FailingAuditRepository(), queue_max_size=1)

    with pytest.raises(RequiredAuditPersistenceError) as raised:
        await service.enqueue_event(
            AuditEventInput(action="REQUIRED", organization_id="org-1"),
            delivery_class="required",
        )

    assert raised.value.status_code == 503
    assert raised.value.code == "audit_persistence_unavailable"
    assert service._queue.empty()


def test_synchronous_api_rejects_critical_delivery() -> None:
    service = AuditService(FakeAuditRepository())

    with pytest.raises(RuntimeError, match="must use await"):
        service.record_event(AuditEventInput(action="CRITICAL"), critical=True)


@pytest.mark.asyncio
async def test_audit_service_emits_metrics():
    repo = FakeAuditRepository()
    repo.content_toggles = {"org-1": True}
    service = AuditService(repo, queue_max_size=1)
    await service.start()
    service.record_event(AuditEventInput(action="FIRST", organization_id="org-1"), critical=False)
    service.record_event(AuditEventInput(action="SECOND", organization_id="org-1"), critical=False)
    await asyncio.sleep(0.05)
    await service.shutdown()

    metrics_text = generate_latest(get_prometheus_registry()).decode("utf-8")
    assert "deltallm_audit_queue_depth" in metrics_text
    assert "deltallm_audit_events_dropped_total" in metrics_text
    assert "deltallm_audit_ingestion_latency_seconds" in metrics_text


class _DurableIngress:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []
        self.redacted: list[tuple[str, int]] = []

    async def enqueue(self, **kwargs):  # noqa: ANN003, ANN201
        self.enqueued.append(kwargs)
        return AuditEnqueueResult(status="accepted", pending_count=1)

    async def enqueue_bundle(self, **kwargs):  # noqa: ANN003, ANN201
        self.enqueued.append(kwargs)
        envelopes = kwargs["envelopes"]
        return AuditBundleEnqueueResult(
            statuses={item.event_id: "accepted" for item in envelopes},
            pending_count=len(envelopes),
        )

    async def get_content_policy(self, organization_id: str) -> tuple[bool, int]:
        assert organization_id == "org-1"
        return False, 7

    async def redact_pending_for_organization(
        self,
        *,
        organization_id: str,
        policy_version: int,
    ) -> int:
        self.redacted.append((organization_id, policy_version))
        return 1


class _FailingDurableIngress(_DurableIngress):
    async def enqueue(self, **kwargs):  # noqa: ANN003, ANN201
        self.enqueued.append(kwargs)
        raise ConnectionError("database unavailable")


class _CancelledDurableIngress(_DurableIngress):
    async def enqueue(self, **kwargs):  # noqa: ANN003, ANN201
        self.enqueued.append(kwargs)
        raise asyncio.CancelledError


class _FailingCompatibilitySink:
    async def enqueue_event(self, *_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("plugin audit sink unavailable")


@pytest.mark.asyncio
async def test_best_effort_durable_enqueue_failure_is_observable_drop() -> None:
    ingress = _FailingDurableIngress()
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    result = await service.enqueue_event(
        AuditEventInput(action="OPTIONAL", organization_id="org-1"),
        delivery_class="best_effort",
    )

    assert result == "dropped"
    assert service.dropped_events == 1
    assert not service._wake.is_set()


@pytest.mark.asyncio
async def test_required_durable_enqueue_failure_remains_fail_closed() -> None:
    ingress = _FailingDurableIngress()
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    with pytest.raises(RequiredAuditPersistenceError):
        await service.enqueue_event(
            AuditEventInput(action="REQUIRED", organization_id="org-1"),
            delivery_class="required",
        )


@pytest.mark.asyncio
async def test_best_effort_durable_enqueue_preserves_cancellation() -> None:
    ingress = _CancelledDurableIngress()
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    with pytest.raises(asyncio.CancelledError):
        await service.enqueue_event(
            AuditEventInput(action="OPTIONAL", organization_id="org-1"),
            delivery_class="best_effort",
        )

    assert service.dropped_events == 0


@pytest.mark.asyncio
async def test_invalid_delivery_class_fails_before_legacy_or_durable_enqueue() -> None:
    legacy = AuditService(FakeAuditRepository())
    durable_ingress = _DurableIngress()
    durable = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=False),
    )
    durable.ingestion_repository = durable_ingress  # type: ignore[assignment]

    for service in (legacy, durable):
        with pytest.raises(ValueError, match="unsupported audit delivery class"):
            await service.enqueue_event(
                AuditEventInput(action="TYPO", organization_id="org-1"),
                delivery_class="requiredd",
            )

    assert legacy._queue.empty()
    assert durable_ingress.enqueued == []


@pytest.mark.asyncio
async def test_compatibility_sink_failure_drops_best_effort_but_closes_required() -> None:
    event = AuditEventInput(action="PLUGIN", organization_id="org-1")

    assert (
        await enqueue_audit_event(
            _FailingCompatibilitySink(),
            event,
            delivery_class="best_effort",
        )
        == "dropped"
    )
    with pytest.raises(RequiredAuditPersistenceError):
        await enqueue_audit_event(
            _FailingCompatibilitySink(),
            event,
            delivery_class="required",
        )


class _FullDurableIngress(_DurableIngress):
    async def enqueue(self, **kwargs):  # noqa: ANN003, ANN201
        self.enqueued.append(kwargs)
        return AuditEnqueueResult(status="full", pending_count=100)

    async def enqueue_bundle(self, **kwargs):  # noqa: ANN003, ANN201
        self.enqueued.append(kwargs)
        envelopes = kwargs["envelopes"]
        return AuditBundleEnqueueResult(
            statuses={item.event_id: "full" for item in envelopes},
            pending_count=100,
        )


class _PromptAcceptedAuditFullIngress(_DurableIngress):
    async def enqueue_bundle(self, **kwargs):  # noqa: ANN003, ANN201
        self.enqueued.append(kwargs)
        envelopes = kwargs["envelopes"]
        return AuditBundleEnqueueResult(
            statuses={
                item.event_id: "accepted" if item.record_type == "prompt_render" else "full"
                for item in envelopes
            },
            pending_count=90,
        )


class _PromptPolicyIngress(_FullDurableIngress):
    def __init__(self, *, enabled: bool, full: bool = False) -> None:
        super().__init__()
        self.enabled = enabled
        self.full = full
        self.locks: list[str] = []

    def with_db(self, _db: Any) -> _PromptPolicyIngress:
        return self

    async def enqueue(self, **kwargs):  # noqa: ANN003, ANN201
        self.enqueued.append(kwargs)
        return AuditEnqueueResult(
            status="full" if self.full else "accepted",
            pending_count=100 if self.full else 1,
        )

    async def lock_content_policy(self, organization_id: str) -> None:
        self.locks.append(organization_id)

    async def get_content_policy(self, organization_id: str) -> tuple[bool, int]:
        assert organization_id == "org-1"
        return self.enabled, 8


class _PromptRenderRepository:
    def __init__(self, db: Any) -> None:
        self.prisma = db
        self.payloads: list[dict[str, Any]] = []

    def with_db(self, _db: Any) -> _PromptRenderRepository:
        return self

    async def create_render_log(self, **payload: Any) -> None:
        self.payloads.append(payload)


def _prompt_render_event() -> PromptRenderEvent:
    return PromptRenderEvent(
        prompt_render_log_id="render-1",
        status="success",
        organization_id="org-1",
        variables={"secret": "value"},
    )


class _IdleDurableWorkerIngress:
    def __init__(self) -> None:
        self.reconcile_calls = 0

    async def reconcile_capacity(self) -> int:
        self.reconcile_calls += 1
        return 0

    async def claim_batch(self, **_kwargs: Any) -> list[AuditOutboxRecord]:
        return []

    async def pending_stats(self) -> tuple[int, float]:
        return 0, 0.0


class _PublishingRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.messages.append((channel, payload))


@pytest.mark.asyncio
async def test_durable_required_audit_enqueues_raw_and_redacted_envelopes() -> None:
    repo = FakeAuditRepository()
    ingress = _DurableIngress()
    service = AuditService(
        repo,
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    result = await service.enqueue_event(
        AuditEventInput(action="CHAT_COMPLETION", organization_id="org-1"),
        payloads=[AuditPayloadInput(kind="request", content_json={"secret": "value"})],
        delivery_class="required",
    )

    assert result == "accepted"
    stored = ingress.enqueued[0]
    assert stored["payload"]["payloads"][0]["content_json"] == {"secret": "value"}
    assert stored["redacted_payload"]["payloads"][0]["content_json"] is None
    assert stored["redacted_payload"]["payloads"][0]["redacted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_enabled", "expected_variables", "expected_redacted"),
    [
        (True, {"secret": "value"}, False),
        (False, None, True),
    ],
)
async def test_legacy_prompt_render_persists_with_authoritative_policy_before_return(
    content_enabled: bool,
    expected_variables: dict[str, str] | None,
    expected_redacted: bool,
) -> None:
    db = object()
    prompt_repository = _PromptRenderRepository(db)
    ingress = _PromptPolicyIngress(enabled=content_enabled)
    service = AuditService(
        FakeAuditRepository(),
        db_client=db,
        prompt_repository=prompt_repository,  # type: ignore[arg-type]
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    await service.start()
    result = await service.enqueue_prompt_render(_prompt_render_event())
    await service._queue.join()
    await service.shutdown()

    assert result == "persisted"
    assert ingress.locks == ["org-1"]
    assert prompt_repository.payloads[0]["variables"] == expected_variables
    assert prompt_repository.payloads[0]["variables_redacted"] is expected_redacted


@pytest.mark.asyncio
async def test_prompt_render_full_fails_closed_without_bypassing_capacity() -> None:
    db = object()
    prompt_repository = _PromptRenderRepository(db)
    ingress = _PromptPolicyIngress(enabled=False, full=True)
    service = AuditService(
        FakeAuditRepository(),
        db_client=db,
        prompt_repository=prompt_repository,  # type: ignore[arg-type]
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    with pytest.raises(AuditIngestionOverloadedError):
        await service.enqueue_prompt_render(_prompt_render_event())

    assert ingress.locks == []
    assert prompt_repository.payloads == []


@pytest.mark.asyncio
async def test_durable_prompt_render_and_audit_share_one_policy_aware_bundle() -> None:
    ingress = _PromptAcceptedAuditFullIngress()
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    result = await service.enqueue_prompt_render(_prompt_render_event())

    assert result == "accepted"
    assert service.dropped_events == 1
    assert len(ingress.enqueued) == 1
    envelopes = ingress.enqueued[0]["envelopes"]
    assert [item.record_type for item in envelopes] == ["prompt_render", "audit_event"]
    assert envelopes[0].payload["variables"] == {"secret": "value"}
    assert envelopes[0].redacted_payload["variables"] is None
    assert "variables" not in envelopes[1].payload


class _BundleTransaction:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def is_transaction(self) -> bool:
        return True

    async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        if "AS queue_locks" in query:
            return [{"queue_locks": 1, "policy_locks": 1}]
        return [
            {"event_id": "audit-1", "status": "full", "pending_count": 90},
            {"event_id": "render-1", "status": "accepted", "pending_count": 90},
        ]


class _BundleQueryDatabase:
    def __init__(self) -> None:
        self.transaction = _BundleTransaction()
        self.tx_calls = 0

    @asynccontextmanager
    async def tx(self):  # noqa: ANN201
        self.tx_calls += 1
        yield self.transaction


@pytest.mark.asyncio
async def test_audit_repository_serializes_bundle_for_one_atomic_capacity_decision() -> None:
    db = _BundleQueryDatabase()
    repository = AuditIngestionRepository(db)
    envelopes = [
        AuditOutboxEnvelope(
            event_id="render-1",
            record_type="prompt_render",
            organization_id="org-1",
            delivery_class="required",
            payload={"variables": {"secret": "value"}},
            redacted_payload={"variables": None},
            max_attempts=10,
        ),
        AuditOutboxEnvelope(
            event_id="audit-1",
            record_type="audit_event",
            organization_id="org-1",
            delivery_class="best_effort",
            payload={"event": {"action": "PROMPT_RESOLUTION_REQUEST"}},
            redacted_payload={"event": {"action": "PROMPT_RESOLUTION_REQUEST"}},
            max_attempts=10,
        ),
    ]

    result = await repository.enqueue_bundle(
        envelopes=envelopes,
        max_pending_events=100,
        required_reserve=10,
    )

    assert result.statuses == {"audit-1": "full", "render-1": "accepted"}
    assert result.pending_count == 90
    assert db.tx_calls == 1
    assert len(db.transaction.calls) == 2
    lock_query, lock_params = db.transaction.calls[0]
    assert "pg_advisory_xact_lock" in lock_query
    assert "deltallm:audit-ingestion-capacity" in lock_query
    assert "deltallm:audit-content-policy:" in lock_query
    assert lock_params == ("org-1",)
    query, params = db.transaction.calls[1]
    serialized = json.loads(params[0])
    assert [item["event_id"] for item in serialized] == ["render-1", "audit-1"]
    assert params[1:] == ("org-1", 100, 10)
    assert "pg_advisory_xact_lock" not in query
    assert "pending_count = pending_count + (SELECT COUNT(*) FROM inserted)" in query
    assert "CASE WHEN policy.enabled" in query


@pytest.mark.asyncio
async def test_distributed_policy_invalidation_only_publishes_after_atomic_redaction() -> None:
    ingress = _DurableIngress()
    redis = _PublishingRedis()
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        redis_client=redis,
        ingestion_config=AuditIngestionConfig(enabled=True),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    await service.invalidate_content_storage_policy_distributed("org-1")

    assert ingress.redacted == []
    assert len(redis.messages) == 1
    assert '"organization_id": "org-1"' in redis.messages[0][1]


class _TransactionClient:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.nested_transaction_attempts = 0

    def is_transaction(self) -> bool:
        return True

    def tx(self):  # noqa: ANN201
        self.nested_transaction_attempts += 1
        raise AssertionError("transaction clients must not open nested transactions")

    async def query_raw(self, query: str, *_params):  # noqa: ANN002, ANN201
        self.queries.append(query)
        return []


class _TransactionBoundAuditRepository(FakeAuditRepository):
    def __init__(self, db: _TransactionClient) -> None:
        super().__init__()
        self.prisma = db
        self.content_toggles["org-1"] = True


@pytest.mark.asyncio
async def test_content_write_reuses_transaction_client_and_takes_policy_lock() -> None:
    db = _TransactionClient()
    repo = _TransactionBoundAuditRepository(db)
    service = AuditService(repo, db_client=db)  # type: ignore[arg-type]

    await service.record_event_sync(
        AuditEventInput(action="CHAT_COMPLETION", organization_id="org-1"),
        payloads=[AuditPayloadInput(kind="request", content_json={"secret": "value"})],
        repository=repo,  # type: ignore[arg-type]
    )

    assert db.nested_transaction_attempts == 0
    assert any("pg_advisory_xact_lock" in query for query in db.queries)
    assert repo.payloads[0].content_json == {"secret": "value"}


@pytest.mark.asyncio
async def test_policy_listener_reconnects_after_failure() -> None:
    service = AuditService(FakeAuditRepository(), redis_client=object())
    listener = service.policy_invalidation
    attempts = 0

    async def consume_once() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("redis disconnected")
        service.invalidate_content_storage_policy("org-1")
        listener._closed = True

    service._content_policy_cache["org-1"] = (float("inf"), True)
    listener._consume_once = consume_once  # type: ignore[method-assign]

    await listener._listen()

    assert attempts == 2
    assert "org-1" not in service._content_policy_cache


def _outbox_audit_record(event_id: str, organization_id: str) -> AuditOutboxRecord:
    item = {
        "event": {
            "action": "CHAT_COMPLETION",
            "organization_id": organization_id,
        },
        "payloads": [{"kind": "request", "content_json": {"secret": event_id}}],
        "critical": True,
    }
    redacted = {
        "event": item["event"],
        "payloads": [{"kind": "request", "content_json": None, "redacted": True}],
        "critical": True,
    }
    return AuditOutboxRecord(
        event_id=event_id,
        record_type="audit_event",
        organization_id=organization_id,
        delivery_class="required",
        payload=item,
        redacted_payload=redacted,
        policy_version=1,
        attempt_count=1,
    )


class _BatchIngress:
    def __init__(self) -> None:
        self.locks: list[str] = []
        self.operations: list[str] = []
        self.redacted_event_ids: list[str] = []
        self.completed_event_ids: list[str] = []

    def with_db(self, _db):  # noqa: ANN001, ANN201
        return self

    async def lock_content_policies(
        self,
        organization_ids: list[str],
    ) -> None:
        self.operations.append("lock")
        self.locks.extend(organization_ids)

    async def get_content_policies(
        self,
        organization_ids: list[str],
    ) -> dict[str, tuple[bool, int]]:
        self.operations.append("read")
        return {
            organization_id: (organization_id == "org-b", 2) for organization_id in organization_ids
        }

    async def redact_claimed_records(
        self,
        *,
        event_ids: list[str],
        worker_id: str,
        claim_token: str,
    ) -> int:
        assert worker_id == "worker-1"
        assert claim_token == "unfenced-test-claim"
        self.redacted_event_ids.extend(event_ids)
        return len(event_ids)

    async def mark_completed(
        self,
        *,
        event_ids: list[str],
        worker_id: str,
        claim_token: str,
    ) -> int:
        assert worker_id == "worker-1"
        assert claim_token == "unfenced-test-claim"
        self.completed_event_ids.extend(event_ids)
        return len(event_ids)


@pytest.mark.asyncio
async def test_durable_batch_locks_orgs_in_order_and_scrubs_disabled_rows() -> None:
    repo = FakeAuditRepository()
    service = AuditService(
        repo,
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True, worker_id="worker-1"),
    )
    ingress = _BatchIngress()
    service.ingestion_repository = ingress  # type: ignore[assignment]
    records = [
        _outbox_audit_record("event-b", "org-b"),
        _outbox_audit_record("event-a", "org-a"),
    ]

    await service._commit_durable_records(records)

    assert ingress.locks == ["org-a", "org-b"]
    assert ingress.operations == ["lock", "read"]
    assert ingress.redacted_event_ids == ["event-a"]
    assert ingress.completed_event_ids == ["event-b", "event-a"]
    assert [payload.content_json for payload in repo.payloads] == [
        {"secret": "event-b"},
        None,
    ]


@pytest.mark.asyncio
async def test_poison_record_isolated_without_retrying_neighbors() -> None:
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True),
    )
    records = [
        _outbox_audit_record("good-1", "org-a"),
        _outbox_audit_record("poison", "org-a"),
        _outbox_audit_record("good-2", "org-a"),
    ]
    committed: list[str] = []
    retried: list[str] = []

    async def commit(subset: list[AuditOutboxRecord]) -> None:
        if any(record.event_id == "poison" for record in subset):
            raise ValueError("invalid payload")
        committed.extend(record.event_id for record in subset)

    async def retry(record: AuditOutboxRecord, _exc: Exception) -> None:
        retried.append(record.event_id)

    service._commit_durable_records = commit  # type: ignore[method-assign]
    service._mark_durable_retry = retry  # type: ignore[method-assign]

    await service._commit_durable_with_isolation(records)

    assert committed == ["good-1", "good-2"]
    assert retried == ["poison"]


@pytest.mark.asyncio
async def test_infrastructure_failure_retries_batch_without_recursive_query_storm() -> None:
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True),
    )
    records = [
        _outbox_audit_record("event-1", "org-a"),
        _outbox_audit_record("event-2", "org-b"),
    ]
    attempts = 0
    retried: list[str] = []

    async def commit(_subset: list[AuditOutboxRecord]) -> None:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("database unavailable")

    async def retry(record: AuditOutboxRecord, _exc: Exception) -> None:
        retried.append(record.event_id)

    service._commit_durable_records = commit  # type: ignore[method-assign]
    service._mark_durable_retry = retry  # type: ignore[method-assign]

    await service._commit_durable_with_isolation(records)

    assert attempts == 1
    assert retried == ["event-1", "event-2"]


@pytest.mark.asyncio
async def test_required_render_capacity_error_is_service_unavailable_without_fallback() -> None:
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True),
    )
    service.ingestion_repository = _FullDurableIngress()  # type: ignore[assignment]

    with pytest.raises(AuditIngestionOverloadedError) as raised:
        await service.enqueue_prompt_render(_prompt_render_event())

    assert raised.value.status_code == 503
    assert raised.value.code == "audit_ingestion_capacity"


@pytest.mark.asyncio
async def test_durable_worker_loop_continues_after_unexpected_iteration_failure() -> None:
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True),
    )
    calls = 0

    async def iteration() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database unavailable")
        service._closed = True

    service._durable_worker_iteration = iteration  # type: ignore[method-assign]
    service._durable_worker_running = True

    await service._durable_worker_loop()

    assert calls == 2


@pytest.mark.asyncio
async def test_audit_worker_role_can_be_enabled_and_disabled_live() -> None:
    ingress = _IdleDurableWorkerIngress()
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(
            enabled=True,
            worker_enabled=False,
            flush_interval_seconds=0.01,
        ),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    await service.start()
    assert service._worker_task is None

    await service.reconfigure(replace(service.ingestion_config, worker_enabled=True))
    assert service._worker_task is not None
    assert service._durable_worker_running is True

    await service.reconfigure(replace(service.ingestion_config, worker_enabled=False))
    assert service._worker_task is None
    assert service._cleanup_task is None
    assert service._durable_worker_running is False
    assert ingress.reconcile_calls == 2

    await service.shutdown()


@pytest.mark.asyncio
async def test_audit_start_is_idempotent_when_worker_role_is_disabled() -> None:
    ingress = _IdleDurableWorkerIngress()
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    await service.start()
    await service.start()

    assert ingress.reconcile_calls == 1
    assert service.worker_health.state is WorkerState.DISABLED
    await service.shutdown()


@pytest.mark.asyncio
async def test_durable_audit_shutdown_cancels_stuck_database_work_at_deadline() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    ingress = _IdleDurableWorkerIngress()
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(
            enabled=True,
            flush_interval_seconds=0.01,
            shutdown_drain_timeout_seconds=0.02,
        ),
    )
    service.ingestion_repository = ingress  # type: ignore[assignment]

    async def stuck_iteration() -> None:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    service._durable_worker_iteration = stuck_iteration  # type: ignore[method-assign]
    await service.start()
    await entered.wait()
    worker = service._worker_task

    await asyncio.wait_for(service.shutdown(), timeout=0.1)

    assert service.worker_health.state is WorkerState.DISABLED
    assert worker is not None and not worker.done()
    release.set()
    await asyncio.wait_for(worker, timeout=0.1)


@pytest.mark.asyncio
async def test_legacy_audit_reconfigure_keeps_queue_worker_running() -> None:
    service = AuditService(FakeAuditRepository())
    await service.start()
    original_worker = service._worker_task

    await asyncio.wait_for(
        service.reconfigure(replace(service.ingestion_config, worker_enabled=False)),
        timeout=0.1,
    )

    assert service._worker_task is original_worker
    assert original_worker is not None and not original_worker.done()
    await service.shutdown()


@pytest.mark.asyncio
async def test_durable_retry_transition_failure_leaves_lease_for_recovery() -> None:
    service = AuditService(
        FakeAuditRepository(),
        db_client=object(),
        ingestion_config=AuditIngestionConfig(enabled=True),
    )

    async def fail_retry(_record: AuditOutboxRecord, _exc: Exception) -> None:
        raise RuntimeError("database unavailable")

    service._mark_durable_retry = fail_retry  # type: ignore[method-assign]

    await service._safe_mark_durable_retry(
        _outbox_audit_record("event-1", "org-a"),
        RuntimeError("processing failed"),
    )


class _AuditTransitionDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, query: str, *params: object) -> list[dict[str, str]]:
        self.calls.append((query, params))
        if "SELECT event_id FROM transitioned" in query:
            return [{"event_id": str(params[0])}]
        return []


@pytest.mark.asyncio
async def test_exhausted_required_audit_is_blocked_and_remains_capacity_accounted() -> None:
    db = _AuditTransitionDB()
    record = _outbox_audit_record("event-1", "org-a")
    record = replace(record, attempt_count=2, max_attempts=2, claim_token="claim-1")

    terminal = await AuditIngestionRepository(db).mark_retry(
        record=record,
        worker_id="worker-1",
        error="poison payload",
    )

    assert terminal is True
    query, params = db.calls[-1]
    assert params[2] == "blocked"
    assert params[-1] == "claim-1"
    assert "WHEN $3 = 'failed'" in query
    assert "WHEN $3 = 'blocked'" not in query.split("adjusted AS", 1)[1]


@pytest.mark.asyncio
async def test_exhausted_best_effort_audit_is_failed_and_releases_capacity() -> None:
    db = _AuditTransitionDB()
    record = replace(
        _outbox_audit_record("event-1", "org-a"),
        delivery_class="best_effort",
        attempt_count=2,
        max_attempts=2,
        claim_token="claim-1",
    )

    terminal = await AuditIngestionRepository(db).mark_retry(
        record=record,
        worker_id="worker-1",
        error="optional payload failed",
    )

    assert terminal is True
    query, params = db.calls[-1]
    assert params[2] == "failed"
    assert "pending_count - CASE" in query
