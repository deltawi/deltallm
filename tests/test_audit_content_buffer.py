"""The request path hands accounting v2 bodies to a bounded buffer and never waits."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

from src.billing.accounting.accounting_finalization import (
    accounting_audit_envelope,
    accounting_finalization_audit_event_id,
)
from src.chat.audit import emit_text_audit_event
from src.db.audit.audit_ingestion import AuditBundleEnqueueResult, AuditOutboxEnvelope
from src.db.audit.repository import AuditRepository
from src.routers.audit_helpers import emit_audit_event
from src.services.audit.audit_service import (
    AuditEventInput,
    AuditIngestionConfig,
    AuditPayloadInput,
    AuditService,
)
from src.services.audit.content_attachment import (
    AUDIT_CONTENT_RECORD_TYPE,
    audit_content_record_id,
    audit_content_target,
)
from src.services.audit.content_buffer import AuditContentBuffer, AuditContentItem
from tests.test_accounting_request_ids import _operation as accounting_operation

pytestmark = pytest.mark.hermetic


class _Ingestion:
    """Fake outbox: records each bundle statement."""

    def __init__(self, service: _Service) -> None:
        self.service = service

    async def enqueue_bundle(self, *, envelopes, max_pending_events, required_reserve):
        service = self.service
        service.reserves.append(required_reserve)
        if service.enqueue_delay:
            await asyncio.sleep(service.enqueue_delay)
        if service.enqueue_error is not None:
            raise service.enqueue_error
        service.bundles.append(list(envelopes))
        return AuditBundleEnqueueResult(
            statuses={envelope.event_id: "accepted" for envelope in envelopes},
            pending_count=len(envelopes),
        )


class _Service(AuditService):
    """A real AuditService for policy cache and payload logic, with fake I/O."""

    def __init__(self, policies: dict[str, bool] | None = None) -> None:
        super().__init__(
            AuditRepository(None),
            ingestion_config=AuditIngestionConfig(
                enabled=True, worker_enabled=False, payload_max_bytes=4096
            ),
        )
        self.policies = dict(policies or {})
        self.policy_reads: list[set[str]] = []
        self.bundles: list[list[AuditOutboxEnvelope]] = []
        self.reserves: list[int] = []
        self.enqueue_error: Exception | None = None
        self.enqueue_delay = 0.0
        self.fake_ingestion = _Ingestion(self)

    @property
    def worker_ingestion_repository(self):
        return self.fake_ingestion

    async def content_storage_policies(self, organization_ids: set[str]) -> dict[str, bool]:
        self.policy_reads.append(set(organization_ids))
        return {org: self.policies.get(org, False) for org in organization_ids}

    def cache_policy(self, organization_id: str, enabled: bool) -> None:
        self._remember_content_policy(
            organization_id, enabled, now=asyncio.get_running_loop().time()
        )


def _item(organization_id: str = "org-1", *, text: str = "hello") -> AuditContentItem:
    return AuditContentItem(
        target_event_id=str(uuid4()),
        event=AuditEventInput(action="CHAT_COMPLETION_REQUEST", organization_id=organization_id),
        payloads=[
            AuditPayloadInput(kind="request", content_json={"messages": [text]}),
            AuditPayloadInput(kind="response", content_json={"reply": text}),
        ],
        enqueued_at=datetime.now(UTC),
    )


async def test_offer_keeps_items_until_the_flush_and_sends_one_bundle_per_org():
    service = _Service({"org-1": True, "org-2": True})
    buffer = AuditContentBuffer(service)
    items = [_item("org-1"), _item("org-2"), _item("org-1")]

    assert all(buffer.offer(item) for item in items)
    assert service.bundles == []

    await buffer.flush()

    assert service.policy_reads == [{"org-1", "org-2"}]
    assert sorted(len(bundle) for bundle in service.bundles) == [1, 2]
    envelope = service.bundles[0][0]
    assert envelope.record_type == AUDIT_CONTENT_RECORD_TYPE
    assert envelope.delivery_class == "best_effort"
    target = audit_content_target(envelope.payload)
    assert envelope.event_id == audit_content_record_id(target.event_id)
    # The redacted copy carries no content.
    assert all(item["content_json"] is None for item in envelope.redacted_payload["payloads"])
    # Best-effort capacity: every statement keeps the required reserve free.
    assert service.reserves == [service.ingestion_config.required_reserve] * 2
    assert buffer.pending_items == 0


async def test_cached_policy_off_drops_at_once_without_any_io():
    service = _Service()
    service.cache_policy("org-off", False)
    buffer = AuditContentBuffer(service)

    assert buffer.offer(_item("org-off")) is False
    await buffer.flush()

    assert buffer.pending_items == 0
    assert service.policy_reads == [] and service.bundles == []


async def test_uncached_policy_is_resolved_by_the_flusher():
    service = _Service({"org-on": True, "org-off": False})
    buffer = AuditContentBuffer(service)

    buffer.offer(_item("org-on"))
    buffer.offer(_item("org-off"))
    await buffer.flush()

    assert [bundle[0].organization_id for bundle in service.bundles] == ["org-on"]


async def test_full_buffer_drops_new_items():
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service, max_items=2)

    assert [buffer.offer(_item()) for _ in range(3)] == [True, True, False]
    assert buffer.pending_items == 2


async def test_bundles_respect_the_byte_and_item_limits():
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service, bundle_max_items=2, bundle_max_bytes=10_000_000)
    for _ in range(5):
        buffer.offer(_item())

    await buffer.flush()

    assert [len(bundle) for bundle in service.bundles] == [2, 2, 1]

    small = _Service({"org-1": True})
    one_per_bundle = AuditContentBuffer(small, bundle_max_bytes=1)
    for _ in range(3):
        one_per_bundle.offer(_item())
    await one_per_bundle.flush()
    assert [len(bundle) for bundle in small.bundles] == [1, 1, 1]


async def test_flush_byte_limit_drops_the_excess():
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service, max_flush_bytes=1)

    buffer.offer(_item())
    await buffer.flush()

    assert service.bundles == []


async def test_a_second_item_for_the_same_event_is_dropped_not_the_whole_bundle():
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)
    first, other = _item(), _item()
    second = AuditContentItem(
        target_event_id=first.target_event_id,
        event=first.event,
        payloads=first.payloads,
        enqueued_at=first.enqueued_at,
    )
    for item in (first, second, other):
        buffer.offer(item)

    await buffer.flush()

    sent = [audit_content_target(envelope.payload).event_id for envelope in service.bundles[0]]
    assert sent == [first.target_event_id, other.target_event_id]


async def test_large_bodies_are_compacted_like_other_audit_payloads():
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)
    buffer.offer(_item(text="hello world " * 1_000))

    await buffer.flush()

    stored = service.bundles[0][0].payload["payloads"][0]["content_json"]
    assert stored["omitted"] == "payload_too_large"


async def test_enqueue_failure_sheds_the_flush_without_retry():
    service = _Service({"org-1": True, "org-2": True})
    service.enqueue_error = RuntimeError("pool full")
    buffer = AuditContentBuffer(service)
    buffer.offer(_item("org-1"))
    buffer.offer(_item("org-2"))

    await buffer.flush()
    await buffer.flush()

    assert service.bundles == []
    assert buffer.pending_items == 0


async def test_a_full_queue_stops_the_flush_after_one_statement():
    service = _Service({"org-1": True, "org-2": True})
    original = service.fake_ingestion.enqueue_bundle

    async def full(*, envelopes, max_pending_events, required_reserve):
        await original(
            envelopes=envelopes,
            max_pending_events=max_pending_events,
            required_reserve=required_reserve,
        )
        return AuditBundleEnqueueResult(
            statuses={envelope.event_id: "full" for envelope in envelopes}, pending_count=10
        )

    service.fake_ingestion.enqueue_bundle = full
    buffer = AuditContentBuffer(service)
    buffer.offer(_item("org-1"))
    buffer.offer(_item("org-2"))

    await buffer.flush()

    assert len(service.bundles) == 1


async def test_policy_read_failure_drops_the_flush():
    service = _Service()
    service.content_storage_policies = AsyncMock(side_effect=RuntimeError("down"))
    buffer = AuditContentBuffer(service)
    buffer.offer(_item())

    await buffer.flush()

    assert buffer.pending_items == 0 and service.bundles == []


async def test_background_flush_runs_on_its_interval_and_stop_flushes_the_rest():
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service, flush_interval_seconds=0.01)
    buffer.start()
    try:
        buffer.offer(_item())
        for _ in range(100):
            if service.bundles:
                break
            await asyncio.sleep(0.01)
        assert len(service.bundles) == 1
    finally:
        await buffer.stop()
    stopped = _Service({"org-1": True})
    late = AuditContentBuffer(stopped, flush_interval_seconds=60)
    late.start()
    late.offer(_item())
    await late.stop()
    assert len(stopped.bundles) == 1
    assert late.offer(_item()) is False


async def test_a_full_buffer_drains_to_idle_and_takes_items_again():
    # Bounded-state rule: after load ends, the store returns to idle without a restart.
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service, max_items=50, flush_interval_seconds=0.01)
    buffer.start()
    try:
        accepted = sum(buffer.offer(_item()) for _ in range(80))
        assert 50 <= accepted < 80
        for _ in range(200):
            if buffer.pending_items == 0 and sum(map(len, service.bundles)) == accepted:
                break
            await asyncio.sleep(0.01)
        assert buffer.pending_items == 0
        assert sum(map(len, service.bundles)) == accepted
        assert buffer.offer(_item()) is True
    finally:
        await buffer.stop()


async def test_stop_lets_a_running_send_finish_and_cancels_a_hung_one():
    def _SlowService(delay: float) -> _Service:
        service = _Service({"org-1": True})
        service.enqueue_delay = delay
        return service

    slow = _SlowService(0.05)
    buffer = AuditContentBuffer(slow, early_flush_items=1)
    buffer.start()
    buffer.offer(_item())
    await asyncio.sleep(0.01)  # the flusher is now inside the send
    await buffer.stop(flush_timeout_seconds=1.0)
    assert len(slow.bundles) == 1

    hung = _SlowService(60)
    buffer = AuditContentBuffer(hung, early_flush_items=1)
    buffer.start()
    buffer.offer(_item())
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    await buffer.stop(flush_timeout_seconds=0.05)
    assert asyncio.get_running_loop().time() - started < 1
    assert hung.bundles == []


def test_target_id_matches_the_finalization_audit_event():
    operation = accounting_operation()
    operation_id = operation.reservation.operation_id
    from uuid import uuid5

    envelope = accounting_audit_envelope(
        operation,
        event_id=uuid5(operation_id, "provider-finalization:v2"),
        status="success",
        metadata={},
    )

    assert accounting_finalization_audit_event_id(operation_id) == envelope["event_id"]
    assert UUID(envelope["event_id"])


def _request(*, buffer: AuditContentBuffer | None, operation: object | None) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            audit_content_buffer=buffer,
            audit_service=buffer.audit_service if buffer is not None else None,
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [(b"x-request-id", b"req-1")],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "app": app,
        }
    )
    if operation is not None:
        request.state.spend_operation_handle = operation
    return request


def _auth(organization_id: str = "org-1") -> SimpleNamespace:
    return SimpleNamespace(api_key="key-1", user_id="user-1", organization_id=organization_id)


async def test_chat_audit_with_accounting_v2_offers_content_and_writes_nothing(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", enqueue)
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)
    operation = accounting_operation()

    await emit_text_audit_event(
        request=_request(buffer=buffer, operation=operation),
        auth=_auth(),
        action="CHAT_COMPLETION_REQUEST",
        model="m",
        status="success",
        request_start=perf_counter(),
        request_data={"messages": ["hi"]},
        response_data={"reply": "hello"},
        input_tokens=3,
        output_tokens=1,
    )

    enqueue.assert_not_awaited()
    assert buffer.pending_items == 1
    item = buffer._items[0]
    operation_id = operation.reservation.operation_id
    assert item.target_event_id == accounting_finalization_audit_event_id(operation_id)
    assert item.event.metadata["accounting_operation_id"] == str(operation_id)
    assert item.event.request_id == "req-1"
    assert [payload.kind for payload in item.payloads] == ["request", "response"]


async def test_chat_audit_with_cached_policy_off_builds_nothing(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", enqueue)
    service = _Service()
    service.cache_policy("org-1", False)
    buffer = AuditContentBuffer(service)

    await emit_text_audit_event(
        request=_request(buffer=buffer, operation=accounting_operation()),
        auth=_auth(),
        action="CHAT_COMPLETION_REQUEST",
        model="m",
        status="success",
        request_start=perf_counter(),
        request_data={"messages": ["hi"]},
        response_data=None,
    )

    enqueue.assert_not_awaited()
    assert buffer.pending_items == 0


async def test_chat_audit_without_accounting_v2_keeps_the_required_event(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", enqueue)
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)

    await emit_text_audit_event(
        request=_request(buffer=buffer, operation=None),
        auth=_auth(),
        action="CHAT_COMPLETION_REQUEST",
        model="m",
        status="success",
        request_start=perf_counter(),
        request_data={"messages": ["hi"]},
        response_data={"reply": "hello"},
    )

    enqueue.assert_awaited_once()
    assert buffer.pending_items == 0


async def test_shared_data_plane_audit_offers_content_with_accounting_v2(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr("src.routers.audit_helpers.enqueue_audit_event", enqueue)
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)

    await emit_audit_event(
        request=_request(buffer=buffer, operation=accounting_operation()),
        request_start=perf_counter(),
        action="IMAGE_GENERATION_REQUEST",
        status="success",
        actor_type="api_key",
        organization_id="org-1",
        request_payload={"prompt": "cat"},
        response_payload={"data": []},
    )

    enqueue.assert_not_awaited()
    assert buffer.pending_items == 1


async def test_no_buffer_means_no_work_with_accounting_v2(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr("src.routers.audit_helpers.enqueue_audit_event", enqueue)

    await emit_audit_event(
        request=_request(buffer=None, operation=accounting_operation()),
        request_start=perf_counter(),
        action="IMAGE_GENERATION_REQUEST",
        status="success",
        actor_type="api_key",
        organization_id="org-1",
        request_payload={"prompt": "cat"},
    )

    enqueue.assert_not_awaited()
