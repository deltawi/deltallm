"""Real PostgreSQL: the content buffer sends bodies that the audit worker attaches."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from src.services.audit.audit_service import AuditEventInput, AuditPayloadInput
from src.services.audit.content_attachment import (
    AUDIT_CONTENT_RECORD_TYPE,
    audit_content_record_id,
)
from src.services.audit.content_buffer import AuditContentBuffer, AuditContentItem
from tests.test_audit_content_attachment_postgres import (
    _claim,
    _content_stored,
    _insert_target,
    _outbox_row,
    _payloads,
    _service,
    audit_db,  # noqa: F401 -- shared database fixture
)

pytestmark = pytest.mark.postgres


def _item(organization_id: str, target_event_id: str) -> AuditContentItem:
    return AuditContentItem(
        target_event_id=target_event_id,
        event=AuditEventInput(
            action="CHAT_COMPLETION_REQUEST",
            organization_id=organization_id,
            actor_type="api_key",
            status="success",
            metadata={"accounting_operation_id": target_event_id},
        ),
        payloads=[
            AuditPayloadInput(kind="request", content_json={"messages": ["hi"]}),
            AuditPayloadInput(kind="response", content_json={"reply": "hello"}),
        ],
        enqueued_at=datetime.now(UTC),
    )


async def _outbox(db: Any, event_id: str) -> list[dict[str, Any]]:
    return await db.query_raw(
        """
        SELECT record_type, delivery_class, payload_json
        FROM deltallm_audit_ingestion_outbox WHERE event_id = $1
        """,
        event_id,
    )


async def test_buffered_content_reaches_the_accounting_event(
    audit_db,  # noqa: F811 -- request the imported shared fixture
):
    db, organization_id, cleanup = audit_db
    target = str(uuid4())
    record_id = audit_content_record_id(target)
    cleanup["events"].append(target)
    cleanup["outbox"].append(record_id)
    await _insert_target(db, event_id=target, organization_id=organization_id)
    service = _service(db)
    buffer = AuditContentBuffer(service)

    # The policy is not cached yet, so the request path keeps the item and the
    # flusher reads the policy in one query.
    assert buffer.offer(_item(organization_id, target)) is True
    await buffer.flush()

    rows = await _outbox(db, record_id)
    assert [(row["record_type"], row["delivery_class"]) for row in rows] == [
        (AUDIT_CONTENT_RECORD_TYPE, "best_effort")
    ]
    assert service.peek_content_storage_policy(organization_id) is True

    await service._process_durable_batch(await _claim(db, [record_id]))

    payloads = await _payloads(db, target)
    assert [(row["kind"], row["content_json"]) for row in payloads] == [
        ("request", {"messages": ["hi"]}),
        ("response", {"reply": "hello"}),
    ]
    assert await _content_stored(db, target) is True
    assert (await _outbox_row(db, record_id))["status"] == "completed"


async def test_content_of_an_org_without_content_storage_never_reaches_the_database(
    audit_db,  # noqa: F811 -- request the imported shared fixture
):
    db, organization_id, cleanup = audit_db
    await db.execute_raw(
        """
        UPDATE deltallm_organizationtable
        SET audit_content_storage_enabled = FALSE,
            audit_content_policy_version = audit_content_policy_version + 1
        WHERE organization_id = $1
        """,
        organization_id,
    )
    target = str(uuid4())
    record_id = audit_content_record_id(target)
    cleanup["outbox"].append(record_id)
    service = _service(db)
    buffer = AuditContentBuffer(service)

    buffer.offer(_item(organization_id, target))
    await buffer.flush()

    assert await _outbox(db, record_id) == []
    # Now the policy is cached: later requests of this org skip the buffer.
    assert service.peek_content_storage_policy(organization_id) is False
    assert buffer.offer(_item(organization_id, str(uuid4()))) is False
    assert buffer.pending_items == 0
