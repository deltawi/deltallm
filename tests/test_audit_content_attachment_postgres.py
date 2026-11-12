"""Real PostgreSQL: the audit worker attaches, defers and falls back for content records."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import os
from typing import Any
from uuid import uuid4

import pytest

from src.db.audit.audit_ingestion import AuditIngestionRepository, AuditOutboxRecord
from src.db.audit.repository import AuditEventRecord, AuditRepository
from src.services.audit.audit_service import (
    AuditEventInput,
    AuditIngestionConfig,
    AuditPayloadInput,
    AuditService,
)
from src.services.audit.content_attachment import (
    AUDIT_CONTENT_RECORD_TYPE,
    audit_content_fields,
    audit_content_record_id,
)

try:
    from prisma import Prisma
except Exception:  # pragma: no cover
    Prisma = None  # type: ignore[assignment]


pytestmark = pytest.mark.postgres

DATABASE_URL = os.getenv("DATABASE_URL")
WORKER_ID = "audit-content-test"


async def _connect_prisma() -> Any:
    if Prisma is None or not DATABASE_URL:  # pragma: no cover
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL and the generated prisma client")
        pytest.skip("DATABASE_URL and prisma client are required")
    client = Prisma(datasource={"url": DATABASE_URL})
    await client.connect()
    return client


def _service(db: Any) -> AuditService:
    return AuditService(
        AuditRepository(db),
        db_client=db,
        worker_db_client=db,
        ingestion_config=AuditIngestionConfig(
            enabled=True, worker_enabled=False, worker_id=WORKER_ID
        ),
    )


def _content_payloads(
    *, organization_id: str, target_event_id: str, enqueued_at: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    event = AuditEventInput(
        action="CHAT_COMPLETION_REQUEST",
        organization_id=organization_id,
        actor_type="api_key",
        status="success",
        metadata={"operation_id": target_event_id},
    )
    fields = audit_content_fields(target_event_id=target_event_id, enqueued_at=enqueued_at)
    payload = {
        "event": asdict(event),
        "payloads": [
            asdict(AuditPayloadInput(kind="request", content_json={"messages": ["hi"]})),
            asdict(AuditPayloadInput(kind="response", content_json={"reply": "hello"})),
        ],
        "critical": False,
        **fields,
    }
    redacted = {
        **payload,
        "payloads": [
            asdict(AuditPayloadInput(kind="request", redacted=True)),
            asdict(AuditPayloadInput(kind="response", redacted=True)),
        ],
    }
    return payload, redacted


async def _enqueue_content(
    db: Any, *, organization_id: str, target_event_id: str, enqueued_at: datetime
) -> str:
    record_id = audit_content_record_id(target_event_id)
    payload, redacted = _content_payloads(
        organization_id=organization_id,
        target_event_id=target_event_id,
        enqueued_at=enqueued_at,
    )
    result = await AuditIngestionRepository(db).enqueue(
        event_id=record_id,
        record_type=AUDIT_CONTENT_RECORD_TYPE,
        organization_id=organization_id,
        delivery_class="best_effort",
        payload=payload,
        redacted_payload=redacted,
        max_attempts=3,
        max_pending_events=100_000,
        required_reserve=0,
    )
    assert result.status == "accepted"
    return record_id


async def _claim(db: Any, event_ids: list[str]) -> list[AuditOutboxRecord]:
    """Claim only this test's rows, the way claim_batch does, so other rows stay put."""

    token = str(uuid4())
    rows = await db.query_raw(
        """
        UPDATE deltallm_audit_ingestion_outbox
        SET status = 'processing', attempt_count = attempt_count + 1,
            locked_by = $2, claim_token = $3,
            lease_expires_at = NOW() + INTERVAL '30 seconds', updated_at = NOW()
        WHERE event_id = ANY($1::text[]) AND status IN ('queued', 'retry')
        RETURNING event_id, record_type, organization_id, delivery_class, payload_json,
                  redacted_payload_json, policy_version, attempt_count, max_attempts,
                  claim_token
        """,
        event_ids,
        WORKER_ID,
        token,
    )
    return [
        AuditOutboxRecord(
            event_id=str(row["event_id"]),
            record_type=str(row["record_type"]),
            organization_id=row.get("organization_id"),
            delivery_class=row["delivery_class"],
            payload=dict(row["payload_json"]),
            redacted_payload=dict(row["redacted_payload_json"]),
            policy_version=int(row["policy_version"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            claim_token=str(row["claim_token"]),
        )
        for row in rows
    ]


async def _outbox_row(db: Any, event_id: str) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT status, attempt_count, last_error
        FROM deltallm_audit_ingestion_outbox WHERE event_id = $1
        """,
        event_id,
    )
    return rows[0]


async def _payloads(db: Any, event_id: str) -> list[dict[str, Any]]:
    return await db.query_raw(
        """
        SELECT kind, content_json, redacted FROM deltallm_auditpayload
        WHERE event_id = $1::uuid ORDER BY kind
        """,
        event_id,
    )


async def _content_stored(db: Any, event_id: str) -> bool | None:
    rows = await db.query_raw(
        "SELECT content_stored FROM deltallm_auditevent WHERE event_id = $1::uuid",
        event_id,
    )
    return bool(rows[0]["content_stored"]) if rows else None


async def _insert_target(db: Any, *, event_id: str, organization_id: str) -> None:
    await AuditRepository(db).create_events_batch(
        [
            AuditEventRecord(
                event_id=event_id,
                action="ACCOUNTING_PROVIDER_FINALIZATION",
                organization_id=organization_id,
                status="success",
                input_tokens=12,
                output_tokens=3,
            )
        ]
    )


@pytest.fixture
async def audit_db():
    db = await _connect_prisma()
    organization_id = f"content-org-{uuid4().hex}"
    cleanup: dict[str, list[str]] = {"events": [], "outbox": []}
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationtable (
            organization_id, audit_content_storage_enabled, audit_content_policy_version
        ) VALUES ($1, TRUE, 1)
        """,
        organization_id,
    )
    try:
        yield db, organization_id, cleanup
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_auditevent WHERE event_id = ANY($1::uuid[])",
            cleanup["events"],
        )
        await db.execute_raw(
            "DELETE FROM deltallm_audit_ingestion_outbox WHERE event_id = ANY($1::text[])",
            cleanup["outbox"],
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await AuditIngestionRepository(db).reconcile_capacity()
        await db.disconnect()


async def test_content_attaches_to_the_existing_target_event(audit_db):
    db, organization_id, cleanup = audit_db
    target = str(uuid4())
    cleanup["events"].append(target)
    await _insert_target(db, event_id=target, organization_id=organization_id)
    record_id = await _enqueue_content(
        db, organization_id=organization_id, target_event_id=target, enqueued_at=datetime.now(UTC)
    )
    cleanup["outbox"].append(record_id)

    await _service(db)._process_durable_batch(await _claim(db, [record_id]))

    payloads = await _payloads(db, target)
    assert [(row["kind"], row["content_json"]) for row in payloads] == [
        ("request", {"messages": ["hi"]}),
        ("response", {"reply": "hello"}),
    ]
    assert await _content_stored(db, target) is True
    assert (await _outbox_row(db, record_id))["status"] == "completed"


async def test_content_waits_for_the_target_then_attaches(audit_db):
    db, organization_id, cleanup = audit_db
    target = str(uuid4())
    cleanup["events"].append(target)
    record_id = await _enqueue_content(
        db, organization_id=organization_id, target_event_id=target, enqueued_at=datetime.now(UTC)
    )
    cleanup["outbox"].append(record_id)
    service = _service(db)

    await service._process_durable_batch(await _claim(db, [record_id]))

    waiting = await _outbox_row(db, record_id)
    assert waiting["status"] == "retry"
    # Waiting does not use up an attempt.
    assert int(waiting["attempt_count"]) == 0
    assert waiting["last_error"] == "waiting_for_audit_event"
    assert await _payloads(db, target) == []

    await _insert_target(db, event_id=target, organization_id=organization_id)
    await service._process_durable_batch(await _claim(db, [record_id]))

    assert len(await _payloads(db, target)) == 2
    assert await _content_stored(db, target) is True
    assert (await _outbox_row(db, record_id))["status"] == "completed"


async def test_content_becomes_its_own_event_when_the_target_never_arrives(audit_db):
    db, organization_id, cleanup = audit_db
    target = str(uuid4())
    record_id = await _enqueue_content(
        db,
        organization_id=organization_id,
        target_event_id=target,
        enqueued_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    cleanup["outbox"].append(record_id)
    cleanup["events"].append(record_id)

    await _service(db)._process_durable_batch(await _claim(db, [record_id]))

    rows = await db.query_raw(
        "SELECT action, metadata FROM deltallm_auditevent WHERE event_id = $1::uuid",
        record_id,
    )
    assert rows[0]["action"] == "CHAT_COMPLETION_REQUEST"
    assert rows[0]["metadata"] == {"operation_id": target}
    assert len(await _payloads(db, record_id)) == 2
    assert await _content_stored(db, record_id) is True
    assert (await _outbox_row(db, record_id))["status"] == "completed"


async def test_content_of_an_org_without_content_storage_is_not_written(audit_db):
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
    cleanup["events"].append(target)
    await _insert_target(db, event_id=target, organization_id=organization_id)
    record_id = await _enqueue_content(
        db, organization_id=organization_id, target_event_id=target, enqueued_at=datetime.now(UTC)
    )
    cleanup["outbox"].append(record_id)

    await _service(db)._process_durable_batch(await _claim(db, [record_id]))

    assert await _payloads(db, target) == []
    assert await _content_stored(db, target) is False
    assert (await _outbox_row(db, record_id))["status"] == "completed"
    stored = await db.query_raw(
        "SELECT payload_json FROM deltallm_audit_ingestion_outbox WHERE event_id = $1",
        record_id,
    )
    assert all(item["content_json"] is None for item in stored[0]["payload_json"]["payloads"])


async def test_content_and_its_target_in_one_batch_attach_together(audit_db):
    db, organization_id, cleanup = audit_db
    target = str(uuid4())
    cleanup["events"].append(target)
    cleanup["outbox"].append(target)
    event_payload = {
        "event": asdict(
            AuditEventInput(
                action="ACCOUNTING_PROVIDER_FINALIZATION",
                organization_id=organization_id,
                status="success",
            )
        ),
        "payloads": [],
        "critical": True,
    }
    accepted = await AuditIngestionRepository(db).enqueue(
        event_id=target,
        record_type="audit_event",
        organization_id=organization_id,
        delivery_class="required",
        payload=event_payload,
        redacted_payload=event_payload,
        max_attempts=3,
        max_pending_events=100_000,
        required_reserve=0,
    )
    assert accepted.status == "accepted"
    record_id = await _enqueue_content(
        db, organization_id=organization_id, target_event_id=target, enqueued_at=datetime.now(UTC)
    )
    cleanup["outbox"].append(record_id)

    await _service(db)._process_durable_batch(await _claim(db, [target, record_id]))

    assert len(await _payloads(db, target)) == 2
    assert await _content_stored(db, target) is True
    for event_id in (target, record_id):
        assert (await _outbox_row(db, event_id))["status"] == "completed"


async def test_content_never_attaches_to_an_event_of_another_organization(audit_db):
    db, organization_id, cleanup = audit_db
    other_organization = f"content-org-off-{uuid4().hex}"
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationtable (
            organization_id, audit_content_storage_enabled, audit_content_policy_version
        ) VALUES ($1, FALSE, 1)
        """,
        other_organization,
    )
    try:
        target = str(uuid4())
        cleanup["events"].append(target)
        await _insert_target(db, event_id=target, organization_id=other_organization)
        record_id = await _enqueue_content(
            db,
            organization_id=organization_id,
            target_event_id=target,
            enqueued_at=datetime.now(UTC),
        )
        cleanup["outbox"].append(record_id)
        cleanup["events"].append(record_id)

        await _service(db)._process_durable_batch(await _claim(db, [record_id]))

        # The other organization's event gets no bodies and no flag.
        assert await _payloads(db, target) == []
        assert await _content_stored(db, target) is False
        # The bodies stay in their own organization, as their own event.
        rows = await db.query_raw(
            "SELECT organization_id FROM deltallm_auditevent WHERE event_id = $1::uuid",
            record_id,
        )
        assert rows[0]["organization_id"] == organization_id
        assert len(await _payloads(db, record_id)) == 2
        assert (await _outbox_row(db, record_id))["status"] == "completed"
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            other_organization,
        )


async def test_content_that_names_another_organization_is_never_written(audit_db):
    """A record checked under one organization cannot write bodies into another."""

    db, organization_id, cleanup = audit_db
    other_organization = f"content-org-off-{uuid4().hex}"
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationtable (
            organization_id, audit_content_storage_enabled, audit_content_policy_version
        ) VALUES ($1, FALSE, 1)
        """,
        other_organization,
    )
    try:
        target = str(uuid4())
        cleanup["events"].append(target)
        await _insert_target(db, event_id=target, organization_id=other_organization)
        record_id = audit_content_record_id(target)
        cleanup["outbox"].append(record_id)
        cleanup["events"].append(record_id)
        # The outbox row is in the organization with content storage on, but the
        # stored event names the other organization.
        payload, redacted = _content_payloads(
            organization_id=other_organization,
            target_event_id=target,
            enqueued_at=datetime.now(UTC),
        )
        result = await AuditIngestionRepository(db).enqueue(
            event_id=record_id,
            record_type=AUDIT_CONTENT_RECORD_TYPE,
            organization_id=organization_id,
            delivery_class="best_effort",
            payload=payload,
            redacted_payload=redacted,
            max_attempts=3,
            max_pending_events=100_000,
            required_reserve=0,
        )
        assert result.status == "accepted"

        await _service(db)._process_durable_batch(await _claim(db, [record_id]))

        # Nothing is written: no bodies on the target and no fallback event.
        assert await _payloads(db, target) == []
        assert await _content_stored(db, target) is False
        assert await _content_stored(db, record_id) is None
        assert await _payloads(db, record_id) == []
        row = await _outbox_row(db, record_id)
        assert row["status"] == "retry"
        assert "another organization" in row["last_error"]
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            other_organization,
        )


async def test_a_waiting_record_comes_back_later_as_it_gets_older(audit_db):
    db, organization_id, cleanup = audit_db
    record_id = await _enqueue_content(
        db,
        organization_id=organization_id,
        target_event_id=str(uuid4()),
        enqueued_at=datetime.now(UTC) - timedelta(seconds=60),
    )
    cleanup["outbox"].append(record_id)

    await _service(db)._process_durable_batch(await _claim(db, [record_id]))

    rows = await db.query_raw(
        """
        SELECT status, EXTRACT(EPOCH FROM (next_attempt_at - NOW()))::float8 AS wait
        FROM deltallm_audit_ingestion_outbox WHERE event_id = $1
        """,
        record_id,
    )
    assert rows[0]["status"] == "retry"
    # Half the 60 s age, capped at 30 s; not the 2 s of a new record.
    assert 25 <= rows[0]["wait"] <= 30
