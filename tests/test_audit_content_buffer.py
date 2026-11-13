"""The request path hands accounting v2 bodies to a bounded buffer and never waits."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import gc
from hashlib import sha256
import json
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

from src.billing.accounting.accounting_finalization import (
    accounting_audit_envelope,
    accounting_finalization_audit_event_id,
)
from src.chat.audit import emit_text_audit_event
from src.chat.preflight import TextPreflightResult
from src.db.audit.audit_ingestion import (
    AuditBundleEnqueueResult,
    AuditOutboxEnvelope,
    audit_envelope_wire,
)
from src.db.audit.repository import AuditRepository
from src.routers.audit_helpers import _preflight_request_snapshot, emit_audit_event
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
from src.services.audit.content_buffer import (
    AUDIT_CONTENT_MAX_REQUEST_TEXT_CHARS,
    AuditContentBuffer,
    AuditContentItem,
    AuditRequestSnapshot,
    _json_text,
    _measure,
    _with_request_body,
    approximate_chars,
    audit_request_snapshot,
)
from src.rate_limit_policy import json_text
from src.services.audit.payload_compaction import DEFAULT_AUDIT_PAYLOAD_MAX_BYTES, _compact
from tests.test_accounting_request_ids import _operation as accounting_operation

pytestmark = pytest.mark.hermetic


class _Ingestion:
    """Fake outbox: records each bundle statement."""

    def __init__(self, service: _Service) -> None:
        self.service = service

    async def enqueue_bundle(
        self, *, envelopes, max_pending_events, required_reserve, serialized_envelopes=None
    ):
        service = self.service
        service.reserves.append(required_reserve)
        if service.enqueue_delay:
            await asyncio.sleep(service.enqueue_delay)
        if service.enqueue_error is not None:
            raise service.enqueue_error
        service.sent.append((serialized_envelopes, list(envelopes)))
        return AuditBundleEnqueueResult(
            statuses={envelope.event_id: "accepted" for envelope in envelopes},
            pending_count=len(envelopes),
        )


class _Service(AuditService):
    """A real AuditService for policy cache and payload logic, with fake I/O."""

    def __init__(
        self, policies: dict[str, bool] | None = None, *, payload_max_bytes: int = 4096
    ) -> None:
        super().__init__(
            AuditRepository(None),
            ingestion_config=AuditIngestionConfig(
                enabled=True, worker_enabled=False, payload_max_bytes=payload_max_bytes
            ),
        )
        self.policies = dict(policies or {})
        self.policy_reads: list[set[str]] = []
        # Each statement sent: its text, and the envelopes that name its records.
        self.sent: list[tuple[str | None, list[AuditOutboxEnvelope]]] = []
        self.reserves: list[int] = []
        self.enqueue_error: Exception | None = None
        self.enqueue_delay = 0.0
        self.fake_ingestion = _Ingestion(self)

    @property
    def statements(self) -> list[str]:
        return [text for text, _ in self.sent if text is not None]

    @property
    def bundles(self) -> list[list[AuditOutboxEnvelope]]:
        """The records as stored, read back from each statement's text.

        Read when a test looks, so the test's own parse is not part of a flush.
        """

        bundles = []
        for text, envelopes in self.sent:
            if text is not None:
                stored = [AuditOutboxEnvelope(**wire) for wire in json.loads(text)]
                assert [item.event_id for item in stored] == [item.event_id for item in envelopes]
                envelopes = stored
            bundles.append(envelopes)
        return bundles

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


async def test_large_bodies_count_until_their_send_ends():
    service = _Service({"org-1": True})
    service.enqueue_delay = 0.05
    image = "A" * 1_000_000  # base64 data, before compaction
    size = sum(approximate_chars(payload.content_json) for payload in _item(text=image).payloads)
    buffer = AuditContentBuffer(service, max_held_chars=size * 2)

    assert buffer.offer(_item(text=image)) is True
    assert buffer.offer(_item(text=image)) is True
    assert buffer.offer(_item(text=image)) is False  # buffer_bytes
    sending = asyncio.create_task(buffer.flush())
    await asyncio.sleep(0.01)
    # The first two are in flight and still in memory, so there is no room yet.
    assert buffer.offer(_item(text=image)) is False
    await sending
    assert buffer.offer(_item(text=image)) is True


def test_size_estimate_adds_up_string_lengths_and_a_little_per_value():
    # Braces, the key with its quotes and separators, brackets and ", " for three items,
    # the string with its quotes, and 8 for each other scalar.
    assert approximate_chars({"a": ["xy", 1, None]}) == 2 + (1 + 6) + (2 + 6) + 4 + 8 + 8
    assert approximate_chars({"data": "A" * 1_000_000}) == 2 + (4 + 6) + 1_000_002
    # A float counts its longest text.
    assert approximate_chars([0.5]) == (2 + 2) + 24


class _NeverWalked(list):
    def __iter__(self):
        raise AssertionError("the size estimate iterated a list over its work limit")


def test_size_estimate_stops_before_a_list_over_its_work_limit():
    assert approximate_chars([1, 2, 3], max_values=4) is not None
    assert approximate_chars([1, 2, 3, 4], max_values=4) is None
    # The long list is refused by its length, before any of its values is visited.
    assert approximate_chars(_NeverWalked([0.5] * 1_500_000)) is None
    assert approximate_chars({"data": [1, _NeverWalked([0.5] * 100)]}, max_values=10) is None


async def test_a_large_embeddings_response_costs_bounded_work_on_the_request_path():
    """512 inputs of 3,072 floats: about 1.5 million values in the response."""

    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)
    inputs = [f"input {index}" for index in range(64)]
    response = {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": [0.5] * 3072}
            for index in range(512)
        ],
    }
    item = AuditContentItem(
        target_event_id=str(uuid4()),
        event=AuditEventInput(action="EMBEDDING_REQUEST", organization_id="org-1"),
        payloads=[
            AuditPayloadInput(kind="request", content_json={"input": inputs}),
            AuditPayloadInput(kind="response", content_json=response),
        ],
        enqueued_at=datetime.now(UTC),
    )

    assert buffer.offer(item) is True
    await buffer.flush()

    request, stored_response = service.bundles[0][0].payload["payloads"]
    # The request is kept. The response surely exceeds the payload limit, so it is
    # replaced on the request path by the placeholder that compaction would make.
    assert request["content_json"] == {"input": inputs}
    placeholder = stored_response["content_json"]
    assert placeholder["omitted"] == "payload_too_large"
    assert placeholder["max_bytes"] == service.ingestion_config.payload_max_bytes
    assert placeholder["approx_size_bytes"] > 1_000_000


async def test_bundles_respect_the_byte_and_item_limits():
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service, bundle_max_items=2, bundle_max_bytes=10_000_000)
    for _ in range(5):
        buffer.offer(_item())

    await buffer.flush()

    assert [len(bundle) for bundle in service.bundles] == [2, 2, 1]

    # A limit that fits one record (and not two) gives one record per statement.
    small = _Service({"org-1": True})
    items = [_item() for _ in range(3)]
    largest = max(_statement_bytes([_envelope_of(small, item)]) for item in items)
    one_per_bundle = AuditContentBuffer(small, bundle_max_bytes=largest)
    for item in items:
        one_per_bundle.offer(item)
    await one_per_bundle.flush()
    assert [len(bundle) for bundle in small.bundles] == [1, 1, 1]
    assert all(_statement_bytes(bundle) <= largest for bundle in small.bundles)


def _envelope_of(service: _Service, item: AuditContentItem) -> AuditOutboxEnvelope:
    return AuditContentBuffer(service)._envelope(item)


def _statement_bytes(bundle: list[AuditOutboxEnvelope]) -> int:
    """The size of the JSON that enqueue_bundle sends for this bundle."""

    return len(json.dumps([audit_envelope_wire(envelope) for envelope in bundle], default=str))


async def test_no_statement_is_larger_than_the_byte_limit():
    """Each body fits the payload limit, but the two together do not fit a statement."""

    service = _Service({"org-1": True})  # payload_max_bytes=4096
    text = "hello world " * 300  # 3,600 characters: kept by compaction on its own
    limit = 6_000
    buffer = AuditContentBuffer(service, bundle_max_bytes=limit)
    for _ in range(4):
        buffer.offer(_item(text=text))

    await buffer.flush()

    assert sum(len(bundle) for bundle in service.bundles) == 4
    assert all(_statement_bytes(bundle) <= limit for bundle in service.bundles)
    # The record was compacted harder to fit: its bodies became placeholders.
    stored = service.bundles[0][0].payload["payloads"]
    assert all(payload["content_json"]["omitted"] == "payload_too_large" for payload in stored)


async def test_a_record_that_cannot_fit_a_statement_is_dropped():
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service, bundle_max_bytes=100)
    buffer.offer(_item())

    await buffer.flush()

    assert service.bundles == []


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

    async def full(*, envelopes, **kwargs):
        await original(envelopes=envelopes, **kwargs)
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


def _request(
    *,
    buffer: AuditContentBuffer | None,
    operation: object | None,
    headers: dict[str, str] | None = None,
) -> Request:
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
            "headers": [(b"x-request-id", b"req-1")]
            + [(key.encode(), value.encode()) for key, value in (headers or {}).items()],
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


# Large text requests that fit the payload limit -----------------------------------


def _tool_schema_request(api: str) -> dict:
    """128 tools of 40 string properties each: over 20,000 values, under 1 MiB."""

    def parameters(index: int) -> dict:
        return {
            "type": "object",
            "properties": {
                f"field_{n}": {"type": "string", "description": f"Field {n} of tool {index}."}
                for n in range(40)
            },
            "required": [f"field_{n}" for n in range(40)],
        }

    if api == "chat":
        tools = [
            {
                "type": "function",
                "function": {
                    "name": f"tool_{index}",
                    "description": f"Tool {index}.",
                    "parameters": parameters(index),
                },
            }
            for index in range(128)
        ]
        return {"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": tools}
    if api == "responses":
        tools = [
            {"type": "function", "name": f"tool_{index}", "parameters": parameters(index)}
            for index in range(128)
        ]
        return {"model": "m", "input": "hi", "tools": tools}
    tools = [
        {"name": f"tool_{index}", "description": "d", "input_schema": parameters(index)}
        for index in range(128)
    ]
    return {
        "model": "m",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": tools,
    }


def _long_conversation() -> dict:
    messages = [
        {
            "role": "user" if turn % 2 == 0 else "assistant",
            "content": [{"type": "text", "text": f"Turn {turn}: " + "words " * 15}],
        }
        for turn in range(3_000)
    ]
    return {"model": "m", "messages": messages}


def _value_count(value) -> int:
    if isinstance(value, dict):
        return 1 + sum(_value_count(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_value_count(item) for item in value)
    return 1


def _preflight(request_data: dict, *, text: str | None = None) -> TextPreflightResult:
    """What run_text_preflight leaves on request.state for this request body."""

    text = json_text(request_data) if text is None else text
    return TextPreflightResult(
        auth=None,
        payload=None,
        request_data=request_data,
        callback_manager=None,
        guardrail_middleware=None,
        token_estimate=max(1, len(text) // 4),
        audit_request=AuditRequestSnapshot(chars=len(text), text=text),
    )


async def _emit_chat(buffer: AuditContentBuffer, request_data: dict, *, measured: bool) -> None:
    request = _request(buffer=buffer, operation=accounting_operation())
    if measured:
        request.state.prepared_text_request = _preflight(request_data)
    await emit_text_audit_event(
        request=request,
        auth=_auth(),
        action="CHAT_COMPLETION_REQUEST",
        model="m",
        status="success",
        request_start=perf_counter(),
        request_data=request_data,
        response_data={"reply": "hello"},
    )


@pytest.mark.parametrize(
    "request_data",
    [
        pytest.param(_tool_schema_request("chat"), id="chat-tools"),
        pytest.param(_tool_schema_request("responses"), id="responses-tools"),
        pytest.param(_tool_schema_request("messages"), id="messages-tools"),
        pytest.param(_long_conversation(), id="long-conversation"),
    ],
)
async def test_a_large_measured_request_keeps_its_body(monkeypatch, request_data):
    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", AsyncMock())
    assert _value_count(request_data) > 16_384
    assert len(json_text(request_data)) < DEFAULT_AUDIT_PAYLOAD_MAX_BYTES
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service)

    await _emit_chat(buffer, request_data, measured=True)

    # Kept as the preflight's JSON text and counted by its length: its values are
    # not visited again.
    assert buffer.pending_items == 1
    assert buffer._items[0].request_snapshot.text == json_text(request_data)
    expected = len(json_text(request_data)) + approximate_chars({"reply": "hello"})
    assert buffer._waiting_chars == expected
    await buffer.flush()
    request, response = service.bundles[0][0].payload["payloads"]
    assert request["content_json"] == request_data
    assert response["content_json"] == {"reply": "hello"}


async def test_a_request_grown_by_the_preflight_counts_its_grown_size(monkeypatch):
    """A tiny request whose prompt resolution or hooks add a large system prompt."""

    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", AsyncMock())
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service, max_held_chars=1_048_576)

    def grown() -> dict:
        return {
            "model": "m",
            "messages": [
                {"role": "system", "content": "x" * 800_000},
                {"role": "user", "content": "hi"},
            ],
        }

    for _ in range(3):
        await _emit_chat(buffer, grown(), measured=True)

    # Only the first fits the cap: each one counts its 800,000 characters.
    assert buffer.pending_items == 1
    assert buffer._waiting_chars > 800_000


def test_the_preflight_text_is_used_only_for_its_own_request_object():
    request_data = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    request = _request(buffer=None, operation=None)
    request.state.prepared_text_request = _preflight(request_data)

    measured = [AuditPayloadInput(kind="request", content_json=request_data)]
    assert _preflight_request_snapshot(request, measured).text == json_text(request_data)
    # Another request object (for example one an endpoint built itself) is walked.
    other = [AuditPayloadInput(kind="request", content_json=dict(request_data))]
    assert _preflight_request_snapshot(request, other) is None
    assert _preflight_request_snapshot(_request(buffer=None, operation=None), measured) is None


class _NeverWalkedDict(dict):
    def items(self):
        raise AssertionError("a request body serialized by the preflight was walked")


async def test_a_request_body_serialized_by_the_preflight_is_not_walked(monkeypatch):
    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", AsyncMock())
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)
    request_data = _NeverWalkedDict(a=1)
    request = _request(buffer=buffer, operation=accounting_operation())
    request.state.prepared_text_request = _preflight(request_data, text='{"a": 1}')

    await emit_text_audit_event(
        request=request,
        auth=_auth(),
        action="CHAT_COMPLETION_REQUEST",
        model="m",
        status="success",
        request_start=perf_counter(),
        request_data=request_data,
        response_data={"reply": "hello"},
    )

    assert buffer.pending_items == 1
    assert buffer._waiting_chars == len('{"a": 1}') + approximate_chars({"reply": "hello"})
    await buffer.flush()
    request_payload, response_payload = service.bundles[0][0].payload["payloads"]
    assert request_payload["content_json"] == {"a": 1}
    assert response_payload["content_json"] == {"reply": "hello"}


async def _longest_loop_pause(work) -> float:
    """Run ``work`` while a 1 ms probe runs on the event loop; return its longest gap."""

    gaps: list[float] = []
    done = asyncio.Event()

    async def probe() -> None:
        last = perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.001)
            now = perf_counter()
            gaps.append(now - last)
            last = now

    # No garbage collection pause is counted in the window. gc.unfreeze() is not
    # used: it would also unfreeze the startup heap that conftest.py freezes.
    gc.collect()
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        task = asyncio.create_task(probe())
        await asyncio.sleep(0.005)
        await work
        done.set()
        await task
    finally:
        if gc_was_enabled:
            gc.enable()
    return max(gaps)


async def test_a_request_snapshot_is_a_text_only_when_its_parse_is_short():
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service)
    request = _request(buffer=buffer, operation=None)
    tools = _tool_schema_request("chat")
    tools_text = json_text(tools)

    snapshot = audit_request_snapshot(request, "org-1", tools_text, tools)
    assert snapshot.text == tools_text
    assert snapshot.chars == len(tools_text)
    # Many small values: under the length limit, but a long parse. The request is
    # measured now instead; it is over the work limit, so it becomes a placeholder.
    small_values = {"p": {f"field_{n}": {"type": "string"} for n in range(20_000)}}
    small_text = json_text(small_values)
    assert len(small_text) < AUDIT_CONTENT_MAX_REQUEST_TEXT_CHARS
    snapshot = audit_request_snapshot(request, "org-1", small_text, small_values)
    assert snapshot.text is None
    assert snapshot.body == {"omitted": "payload_too_many_values", "max_values": 16_384}
    assert snapshot.values_limited
    # Numbers are slow to parse, so digits count too: 40,000 floats or 250 integers of
    # 4,001 digits fit the length limit, but they are copied now (or replaced).
    floats = {"enum": [1.2345678901234567e-300] * 40_000}
    floats_text = json_text(floats)
    assert len(floats_text) < AUDIT_CONTENT_MAX_REQUEST_TEXT_CHARS
    assert audit_request_snapshot(request, "org-1", floats_text, floats).text is None
    integers = {"enum": [10**4_000 + 7] * 250}
    integers_text = json_text(integers)
    assert len(integers_text) < AUDIT_CONTENT_MAX_REQUEST_TEXT_CHARS
    snapshot = audit_request_snapshot(request, "org-1", integers_text, integers)
    assert snapshot.text is None
    assert snapshot.body == integers
    assert snapshot.chars >= len(integers_text)
    # Long prose: few values, over the length limit. It is copied now.
    prose = {"messages": [{"role": "user", "content": "word " * 250_000}]}
    prose_text = json_text(prose)
    snapshot = audit_request_snapshot(request, "org-1", prose_text, prose)
    assert snapshot.text is None
    assert snapshot.body == prose
    assert snapshot.body["messages"] is not prose["messages"]
    assert snapshot.chars > len(prose["messages"][0]["content"])
    # Content storage known to be off, or no buffer: no snapshot.
    service.cache_policy("org-2", False)
    assert audit_request_snapshot(request, "org-2", tools_text, tools) is None
    no_buffer = _request(buffer=None, operation=None)
    assert audit_request_snapshot(no_buffer, "org-1", tools_text, tools) is None


async def test_large_integers_are_counted_by_size_and_omitted(monkeypatch):
    monkeypatch.setattr("src.routers.audit_helpers.enqueue_audit_event", AsyncMock())
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service, max_held_chars=1_048_576)
    huge = 10**4_000

    # Counted by digits, not as 8 characters each: only the first fits the cap.
    for _ in range(3):
        await emit_audit_event(
            request=_request(buffer=buffer, operation=accounting_operation()),
            request_start=perf_counter(),
            action="IMAGE_GENERATION_REQUEST",
            status="success",
            actor_type="api_key",
            organization_id="org-1",
            request_payload={"enum": [huge] * 200},
        )
    assert buffer.pending_items == 1
    assert buffer._waiting_chars > 200 * 4_000

    assert await _longest_loop_pause(buffer.flush()) < 0.05
    stored = service.bundles[0][0].payload["payloads"][0]["content_json"]
    assert stored["enum"][0] == f"[omitted large integer: {huge.bit_length()} bits]"


async def test_a_large_request_does_not_stop_the_event_loop_during_the_flush(monkeypatch):
    """The flusher's parse holds the GIL, so a large request is never parsed there."""

    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", AsyncMock())
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service)
    request = _request(buffer=buffer, operation=accounting_operation())
    properties = {f"field_{n}": {"type": "string"} for n in range(400_000)}
    request_data = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "t",
                    "parameters": {"type": "object", "properties": properties},
                },
            }
        ],
    }
    text = json_text(request_data)
    request.state.prepared_text_request = TextPreflightResult(
        auth=None,
        payload=None,
        request_data=request_data,
        callback_manager=None,
        guardrail_middleware=None,
        token_estimate=len(text) // 4,
        audit_request=audit_request_snapshot(request, "org-1", text, request_data),
    )
    await emit_text_audit_event(
        request=request,
        auth=_auth(),
        action="CHAT_COMPLETION_REQUEST",
        model="m",
        status="success",
        request_start=perf_counter(),
        request_data=request_data,
        response_data={"reply": "hello"},
    )
    assert buffer.pending_items == 1
    assert buffer._items[0].request_snapshot.text is None

    assert await _longest_loop_pause(buffer.flush()) < 0.05
    request_payload, response_payload = service.bundles[0][0].payload["payloads"]
    assert request_payload["content_json"]["omitted"] == "payload_too_large"
    assert response_payload["content_json"] == {"reply": "hello"}


@pytest.mark.parametrize("kind", ["text", "base64", "data_url_header", "data_url_parameter"])
async def test_a_large_string_does_not_stop_the_event_loop_during_the_flush(monkeypatch, kind):
    """Compaction scans and hashes a long string in slices, never in one long C call."""

    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", AsyncMock())
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service)
    # 24 MiB of base64 characters; one more character makes it plain text. A data URL
    # header that never ends is read only at the start of the string.
    long = 24 * 1024 * 1024
    content = {
        "text": "A" * long + "!",
        "base64": "A" * long,
        "data_url_header": "data:" + "a" * long + "!",
        "data_url_parameter": "data:a/b;x=" + "y" * long,
    }[kind]
    valid_base64 = kind == "base64"
    request_data = {"model": "m", "messages": [{"role": "user", "content": content}]}
    request = _request(buffer=buffer, operation=accounting_operation())
    text = json_text(request_data)
    request.state.prepared_text_request = TextPreflightResult(
        auth=None,
        payload=None,
        request_data=request_data,
        callback_manager=None,
        guardrail_middleware=None,
        token_estimate=len(text) // 4,
        audit_request=audit_request_snapshot(request, "org-1", text, request_data),
    )
    await emit_text_audit_event(
        request=request,
        auth=_auth(),
        action="CHAT_COMPLETION_REQUEST",
        model="m",
        status="success",
        request_start=perf_counter(),
        request_data=request_data,
        response_data={"reply": "hello"},
    )
    # The text is too long to keep, and the few values of the body are copied.
    assert buffer._items[0].request_snapshot.text is None
    assert buffer._waiting_chars > len(content)

    assert await _longest_loop_pause(buffer.flush()) < 0.05
    request_payload, response_payload = service.bundles[0][0].payload["payloads"]
    stored = request_payload["content_json"]
    if valid_base64:
        digest = sha256(content.encode()).hexdigest()
        assert stored["messages"][0]["content"] == (
            f"[omitted inline data: base64, {len(content)} chars, sha256={digest}]"
        )
    else:
        assert stored["omitted"] == "payload_too_large"
    assert response_payload["content_json"] == {"reply": "hello"}


@pytest.mark.parametrize("chunk", [1, 2, 3, 1024])
def test_records_are_encoded_in_short_calls_with_the_same_text(monkeypatch, chunk):
    """The flusher writes a record in parts of at most a few values; the text is exact."""

    monkeypatch.setattr("src.services.audit.content_buffer._ENCODE_CHUNK_VALUES", chunk)
    calls: list[Any] = []
    real_dumps = json.dumps

    def counting_dumps(value, **kwargs):
        calls.append(value)
        return real_dumps(value, **kwargs)

    monkeypatch.setattr(
        "src.services.audit.content_buffer.json",
        SimpleNamespace(dumps=counting_dumps, loads=json.loads),
    )
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)
    bodies = [
        {"messages": [{"role": "user", "content": 'quote " backslash \\ é 中文 😀'}]},
        {"reply": "مرحبا " * 100, "n": [1, 2.5, None, True], "nan": float("nan")},
        {1: "int key", 2.5: "float key", None: "null key", False: [[], {}, ()]},
        {"enum": [-1.7976931348623157e308] * 50, "nested": [[1, [2, [3, {"a": (4, 5)}]]]]},
        {"when": datetime(2026, 11, 12, tzinfo=UTC), "id": uuid4()},
        "a plain string body",
    ]
    for request_body, response_body in zip(bodies, [*bodies[1:], None], strict=True):
        payloads = [AuditPayloadInput(kind="request", content_json=request_body)]
        if response_body is not None:
            payloads.append(AuditPayloadInput(kind="response", content_json=response_body))
        item = AuditContentItem(
            target_event_id=str(uuid4()),
            event=AuditEventInput(action="CHAT_COMPLETION_REQUEST", organization_id="org-1"),
            payloads=payloads,
            enqueued_at=datetime.now(UTC),
        )
        wire = audit_envelope_wire(buffer._envelope(item))
        calls.clear()
        assert _json_text(wire) == real_dumps(wire, default=str)
        # A part is at most ``chunk`` values, plus the list or object that holds them.
        assert max(_count_values(value) for value in calls if not _is_key_probe(value)) <= (
            chunk + 1
        )
    empty = audit_envelope_wire(
        buffer._envelope(
            AuditContentItem(
                target_event_id=str(uuid4()),
                event=AuditEventInput(action="CHAT_COMPLETION_REQUEST", organization_id="org-1"),
                payloads=[],
                enqueued_at=datetime.now(UTC),
            )
        )
    )
    assert _json_text(empty) == real_dumps(empty, default=str)


def _count_values(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + sum(_count_values(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return 1 + sum(_count_values(item) for item in value)
    return 1


def _is_key_probe(value: Any) -> bool:
    # ``{key: None}``, written to get one key's text.
    return isinstance(value, dict) and len(value) == 1 and next(iter(value.values())) is None


async def test_the_statement_is_the_text_of_its_records():
    """The flusher sends the text it measured; the outbox does not encode it again."""

    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)
    items = [_item(text=text) for text in ("hello", 'é 😀 "quoted"', "x" * 300)]
    for item in items:
        assert buffer.offer(item)
    await buffer.flush()

    assert len(service.statements) == 1
    expected = [audit_envelope_wire(buffer._envelope(_with_request_body(item))) for item in items]
    assert service.statements[0] == json.dumps(expected, default=str)
    # The records that wait for the send keep only their text, not their payloads.
    assert [len(bundle) for bundle in service.bundles] == [3]


async def test_a_record_that_cannot_be_encoded_does_not_stop_the_flush(monkeypatch):
    dropped: list[str] = []
    monkeypatch.setattr(
        "src.services.audit.content_buffer.increment_audit_content_dropped",
        lambda *, reason, count=1: dropped.extend([reason] * count),
    )
    service = _Service({"org-1": True})
    buffer = AuditContentBuffer(service)
    bad = _item()
    # JSON has no tuple keys, so this body cannot be written.
    bad = replace(bad, payloads=[AuditPayloadInput(kind="request", content_json={(1, 2): "x"})])
    good = _item(text="kept")
    assert buffer.offer(bad)
    assert buffer.offer(good)
    await buffer.flush()

    assert dropped == ["invalid_record"]
    assert [item.event_id for bundle in service.bundles for item in bundle] == [
        audit_content_record_id(good.target_event_id)
    ]


async def test_floats_are_counted_by_their_longest_text(monkeypatch):
    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", AsyncMock())
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service, max_held_chars=1_048_576)
    # Its text is not kept (too many separators), so the request is copied: 15,000
    # floats of 24 characters each are counted as such, not as 8.
    request_data = {
        "model": "m",
        "messages": [{"role": "user", "content": ",:" * 25_000}],
        "tools": [{"enum": [-1.7976931348623157e308] * 15_000}],
    }
    text = json_text(request_data)
    for _ in range(5):
        request = _request(buffer=buffer, operation=accounting_operation())
        request.state.prepared_text_request = TextPreflightResult(
            auth=None,
            payload=None,
            request_data=request_data,
            callback_manager=None,
            guardrail_middleware=None,
            token_estimate=len(text) // 4,
            audit_request=audit_request_snapshot(request, "org-1", text, request_data),
        )
        assert request.state.prepared_text_request.audit_request.text is None
        await emit_text_audit_event(
            request=request,
            auth=_auth(),
            action="CHAT_COMPLETION_REQUEST",
            model="m",
            status="success",
            request_start=perf_counter(),
            request_data=request_data,
            response_data={"reply": "hello"},
        )
    assert buffer.pending_items == 2
    assert buffer._waiting_chars >= 2 * len(text)


async def test_a_body_of_many_floats_does_not_stop_the_event_loop_during_the_flush():
    """A float takes up to about 1.2 us to write, so records are written in parts."""

    limit = 8 * 1024 * 1024
    service = _Service({"org-1": True}, payload_max_bytes=limit)
    buffer = AuditContentBuffer(service, bundle_max_bytes=limit, max_size_values=200_000)
    floats = [-1.7976931348623157e308] * 100_000
    item = replace(
        _item(),
        payloads=[
            AuditPayloadInput(kind="request", content_json={"enum": floats}),
            AuditPayloadInput(kind="response", content_json={"reply": "hello"}),
        ],
    )
    assert buffer.offer(item)

    # In one json.dumps call, these floats take about 115 ms.
    assert await _longest_loop_pause(buffer.flush()) < 0.05
    request_payload, response_payload = service.bundles[0][0].payload["payloads"]
    assert request_payload["content_json"] == {"enum": floats}
    assert response_payload["content_json"] == {"reply": "hello"}


async def test_a_long_arabic_body_is_replaced_and_the_rest_of_the_record_is_kept():
    """JSON writes each Arabic letter as a six-character escape; compaction counts it."""

    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service)
    # 200,000 letters: 1.2 MB as sent, though the string is 200,000 characters.
    item = replace(
        _item(),
        payloads=[
            AuditPayloadInput(kind="request", content_json={"messages": ["ع" * 200_000]}),
            AuditPayloadInput(kind="response", content_json={"reply": "مرحبا"}),
        ],
    )
    assert buffer.offer(item)
    await buffer.flush()

    request_payload, response_payload = service.bundles[0][0].payload["payloads"]
    assert request_payload["content_json"]["omitted"] == "payload_too_large"
    assert response_payload["content_json"] == {"reply": "مرحبا"}


async def test_later_changes_to_the_bodies_do_not_reach_the_buffer(monkeypatch):
    """Post-call hooks get the same objects; with streaming they run after the hand-off."""

    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", AsyncMock())
    monkeypatch.setattr("src.routers.audit_helpers.enqueue_audit_event", AsyncMock())
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service)
    chat_request = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    chat_response = {"choices": [{"message": {"content": "hello"}}]}
    request = _request(buffer=buffer, operation=accounting_operation())
    request.state.prepared_text_request = _preflight(chat_request)
    await emit_text_audit_event(
        request=request,
        auth=_auth(),
        action="CHAT_COMPLETION_REQUEST",
        model="m",
        status="success",
        request_start=perf_counter(),
        request_data=chat_request,
        response_data=chat_response,
    )
    # A body without preflight text (images here) is copied while it is measured.
    image_request = {"prompt": "cat", "options": {"size": "small"}}
    image_response = {"data": [{"url": "https://example.test/cat.png"}]}
    await emit_audit_event(
        request=_request(buffer=buffer, operation=accounting_operation()),
        request_start=perf_counter(),
        action="IMAGE_GENERATION_REQUEST",
        status="success",
        actor_type="api_key",
        organization_id="org-1",
        request_payload=image_request,
        response_payload=image_response,
    )
    counted = buffer._waiting_chars
    expected = [
        [json.loads(json.dumps(chat_request)), json.loads(json.dumps(chat_response))],
        [json.loads(json.dumps(image_request)), json.loads(json.dumps(image_response))],
    ]

    # What a post-call hook or guardrail could do after the hand-off.
    for body in (chat_request, image_request):
        body["model"] = "changed"
        body.setdefault("messages", []).append({"role": "system", "content": "y" * 800_000})
    chat_response["choices"][0]["message"]["content"] = "z" * 800_000
    image_response["data"].append({"b64_json": "A" * 800_000})
    image_request["options"]["size"] = "huge"

    assert buffer._waiting_chars == counted < 1_000
    await buffer.flush()
    stored = [
        [payload["content_json"] for payload in envelope.payload["payloads"]]
        for envelope in service.bundles[0]
    ]
    assert stored == expected


async def test_an_unmeasured_body_over_the_work_limit_is_replaced(monkeypatch):
    dropped: list[str] = []
    monkeypatch.setattr(
        "src.services.audit.content_buffer.increment_audit_content_dropped",
        lambda *, reason, count=1: dropped.append(reason),
    )
    monkeypatch.setattr("src.chat.audit.enqueue_audit_event", AsyncMock())
    service = _Service({"org-1": True}, payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)
    buffer = AuditContentBuffer(service)
    request_data = _tool_schema_request("chat")

    # Without a measured size the body cannot be counted safely, so it is replaced;
    # the response is still kept.
    await _emit_chat(buffer, request_data, measured=False)

    assert dropped == ["payload_values"]
    assert buffer._waiting_chars < 1_000
    await buffer.flush()
    request, response = service.bundles[0][0].payload["payloads"]
    assert request["content_json"] == {
        "omitted": "payload_too_many_values",
        "max_values": buffer.max_size_values,
    }
    assert response["content_json"] == {"reply": "hello"}


def test_a_measured_body_is_copied_and_shares_only_strings_and_numbers():
    text = "x" * 1_000
    body = {"a": [text, 1, None, {"b": (2.5, "y")}], "c": {}}

    size = _measure(body, 100)

    assert size.complete
    assert size.copy == {"a": [text, 1, None, {"b": [2.5, "y"]}], "c": {}}
    assert size.copy is not body
    assert size.copy["a"] is not body["a"]
    assert size.copy["a"][3] is not body["a"][3]
    assert size.copy["c"] is not body["c"]
    assert size.copy["a"][0] is text
    # A body over the limit has no copy: it is replaced by a placeholder.
    assert _measure(body, 3).copy is None


def _floor_cases() -> list:
    embedding = {"data": [{"index": i, "embedding": [0.25] * 300} for i in range(40)]}
    image = {"messages": [{"content": [{"image_url": "data:image/png;base64," + "A" * 5_000}]}]}
    return [
        embedding,
        image,
        {1: ["", [], {}], "k": {"x": None, 2: [True, 1.5]}},
        _tool_schema_request("chat"),
        [[], {}, "", 0] * 50,
    ]


@pytest.mark.parametrize("budget", [1, 5, 100, 10_000, 1_000_000])
def test_the_size_floor_never_exceeds_the_compacted_size(budget):
    for body in _floor_cases():
        compacted_size = _compact(body, stop=1 << 40)[1]
        size = _measure(body, budget)
        assert size.floor <= compacted_size
        # The work stays within about twice the limit.
        assert size.visited <= 2 * budget + 2
