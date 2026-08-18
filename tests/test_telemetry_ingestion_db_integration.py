from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from typing import Any
from uuid import uuid4

import pytest

from src.billing.spend import SpendTrackingService
from src.db.audit_ingestion import AuditIngestionRepository, AuditOutboxEnvelope
from src.db.email import EmailOutboxRecord, EmailOutboxRepository
from src.db.prompt_registry import PromptRegistryRepository
from src.db.repositories import AuditEventRecord, AuditPayloadRecord, AuditRepository
from src.db.spend_ingestion import SpendIngestionRepository
from src.services.audit_service import AuditService
from src.services.telemetry_replay import (
    TelemetryReplayService,
    TelemetryReplayUnavailableError,
)
from src.telemetry.prompt_render import PromptRenderEvent

try:
    from prisma import Prisma
except Exception:  # pragma: no cover
    Prisma = None  # type: ignore[assignment]


DATABASE_URL = os.getenv("DATABASE_URL")


async def _connect_prisma() -> Any:
    if Prisma is None or not DATABASE_URL:  # pragma: no cover
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL and the generated prisma client")
        pytest.skip("DATABASE_URL and prisma client are required")
    client = Prisma(datasource={"url": DATABASE_URL})
    await client.connect()
    return client


async def _wait_for_advisory_lock_wait(db: Any, backend_pid: int) -> None:
    async def _wait() -> None:
        while True:
            rows = await db.query_raw(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_locks
                    WHERE pid = $1
                      AND locktype = 'advisory'
                      AND granted = FALSE
                ) AS waiting
                """,
                backend_pid,
            )
            if rows and bool(rows[0].get("waiting")):
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait(), timeout=5)


@pytest.mark.asyncio
async def test_real_prisma_transaction_client_does_not_reopen_spend_transaction() -> None:
    db = await _connect_prisma()
    event_id = str(uuid4())
    now = datetime.now(tz=UTC)
    try:
        async with db.tx() as tx:
            assert tx.is_transaction() is True
            outcome = await SpendTrackingService(tx).log_spend_once(
                event_id=event_id,
                request_id="caller-visible-request-id",
                api_key="integration-key",
                user_id=None,
                team_id=None,
                organization_id=None,
                end_user_id=None,
                model="integration-model",
                call_type="integration",
                usage={},
                cost=0.0,
                start_time=now,
                end_time=now,
            )
            assert outcome == "inserted"
    finally:
        await db.execute_raw("DELETE FROM deltallm_spendlog_events WHERE id = $1", event_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_exact_spend_batch_uses_numeric_accumulator_in_postgres() -> None:
    db = await _connect_prisma()
    token = f"integration-key-{uuid4()}"
    event_ids = [str(uuid4()), str(uuid4())]
    now = datetime.now(tz=UTC)
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_verificationtoken (id, token, models)
            VALUES ($1, $2, ARRAY[]::text[])
            """,
            str(uuid4()),
            token,
        )
        writer = SpendTrackingService(db)
        await writer.log_batch_once(
            [
                (
                    event_ids[0],
                    "spend",
                    {
                        "request_id": "exact-a",
                        "api_key": token,
                        "model": "integration-model",
                        "call_type": "integration",
                        "usage": {},
                        "cost": 0.1,
                        "cost_exact": "0.100000000000000000",
                        "start_time": now,
                        "end_time": now,
                    },
                ),
                (
                    event_ids[1],
                    "spend",
                    {
                        "request_id": "exact-b",
                        "api_key": token,
                        "model": "integration-model",
                        "call_type": "integration",
                        "usage": {},
                        "cost": 0.2,
                        "cost_exact": "0.200000000000000000",
                        "start_time": now,
                        "end_time": now,
                    },
                ),
            ]
        )

        rows = await db.query_raw(
            "SELECT spend, spend_exact::text AS spend_exact FROM deltallm_verificationtoken WHERE token = $1",
            token,
        )
        assert rows[0]["spend"] == pytest.approx(0.3)
        assert rows[0]["spend_exact"] == "0.300000000000000000"
        event_rows = await db.query_raw(
            "SELECT spend_exact::text AS spend_exact FROM deltallm_spendlog_events WHERE id = ANY($1::text[]) ORDER BY id",
            event_ids,
        )
        assert sorted(row["spend_exact"] for row in event_rows) == [
            "0.100000000000000000",
            "0.200000000000000000",
        ]
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_spendlog_events WHERE id = ANY($1::text[])", event_ids
        )
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", token)
        await db.disconnect()


@pytest.mark.asyncio
async def test_email_delivery_audit_reconciliation_is_claimed_separately() -> None:
    db = await _connect_prisma()
    repository = EmailOutboxRepository(db)
    email_id = str(uuid4())
    audit_event_id = str(uuid4())
    try:
        await repository.enqueue(
            EmailOutboxRecord(
                email_id=email_id,
                kind="test",
                provider="smtp",
                to_addresses=["integration@example.com"],
                from_address="noreply@example.com",
                subject="integration",
                text_body="integration",
                next_attempt_at=datetime.now(tz=UTC) - timedelta(seconds=1),
            )
        )
        delivery_claim = await repository.claim_due(
            limit=1,
            worker_id="delivery-worker-1",
            claim_token="delivery-claim-1",
            lease_seconds=60,
        )
        assert [record.email_id for record in delivery_claim] == [email_id]
        assert delivery_claim[0].delivery_claim_token
        assert await repository.begin_delivery_attempt(
            email_id=email_id,
            worker_id="delivery-worker-1",
            claim_token=delivery_claim[0].delivery_claim_token,
        )
        assert await repository.mark_sent(
            email_id,
            worker_id="delivery-worker-1",
            claim_token=delivery_claim[0].delivery_claim_token,
            provider_message_id="provider-1",
            delivery_audit_event_id=audit_event_id,
        )

        audit_claim = await repository.claim_due_delivery_audits(
            limit=1,
            worker_id="audit-worker-1",
            lease_seconds=30,
        )
        assert [record.email_id for record in audit_claim] == [email_id]
        assert audit_claim[0].status == "sent"
        assert audit_claim[0].delivery_audit_status == "processing"
        assert audit_claim[0].delivery_audit_claim_token
        assert await repository.mark_delivery_audit_retry(
            email_id=email_id,
            worker_id="audit-worker-1",
            claim_token=audit_claim[0].delivery_audit_claim_token,
            error="audit unavailable",
            next_attempt_at=datetime.now(tz=UTC) - timedelta(seconds=1),
        )

        assert await repository.claim_due(limit=1) == []
        retry_claim = await repository.claim_due_delivery_audits(
            limit=1,
            worker_id="audit-worker-2",
            lease_seconds=30,
        )
        assert [record.email_id for record in retry_claim] == [email_id]
        assert retry_claim[0].delivery_audit_claim_token
        assert await repository.mark_delivery_audited(
            email_id=email_id,
            worker_id="audit-worker-2",
            claim_token=retry_claim[0].delivery_audit_claim_token,
        )
        stored = await repository.get_by_email_id(email_id)
        assert stored is not None
        assert stored.status == "sent"
        assert stored.delivery_audit_status == "persisted"
    finally:
        await db.execute_raw("DELETE FROM deltallm_emailoutbox WHERE email_id = $1", email_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_real_bulk_spend_write_is_idempotent() -> None:
    db = await _connect_prisma()
    event_ids = [str(uuid4()), str(uuid4())]
    now = datetime.now(tz=UTC)
    payloads = [
        {
            "request_id": "same-client-request-id",
            "api_key": f"integration-key-{index}",
            "user_id": None,
            "team_id": None,
            "organization_id": None,
            "end_user_id": None,
            "model": "integration-model",
            "call_type": "integration",
            "usage": {},
            "cost": 0.0,
            "start_time": now,
            "end_time": now,
        }
        for index in range(2)
    ]
    service = SpendTrackingService(db)
    prepared = [
        service.prepare_batch_event(
            event_id=event_id,
            event_type="spend",
            payload=payload,
        )
        for event_id, payload in zip(event_ids, payloads, strict=True)
    ]
    try:
        first_inserted, _ = await service.log_prepared_batch_once(prepared)
        replay_inserted, _ = await service.log_prepared_batch_once(prepared)
        rows = await db.query_raw(
            """
            SELECT id
            FROM deltallm_spendlog_events
            WHERE id = ANY($1::text[])
            ORDER BY id
            """,
            event_ids,
        )

        assert first_inserted == set(event_ids)
        assert replay_inserted == set()
        assert [str(row["id"]) for row in rows] == sorted(event_ids)
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_spendlog_events WHERE id = ANY($1::text[])",
            event_ids,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_real_bulk_audit_and_prompt_writes_are_idempotent() -> None:
    db = await _connect_prisma()
    event_ids = [str(uuid4()), str(uuid4())]
    payload_ids = [str(uuid4()), str(uuid4())]
    render_ids = [str(uuid4()), str(uuid4())]
    audit_repository = AuditRepository(db)
    events = [
        AuditEventRecord(
            event_id=event_id,
            action="integration.bulk",
            request_id="same-client-request-id",
            metadata={"index": index},
            content_stored=True,
        )
        for index, event_id in enumerate(event_ids)
    ]
    payloads = [
        AuditPayloadRecord(
            payload_id=payload_id,
            event_id=event_id,
            kind="request",
            content_json={"index": index},
            size_bytes=10 + index,
        )
        for index, (payload_id, event_id) in enumerate(zip(payload_ids, event_ids, strict=True))
    ]
    renders = [
        {
            "prompt_render_log_id": render_id,
            "request_id": "same-client-request-id",
            "status": "success",
            "latency_ms": index,
            "variables": {"index": index},
            "metadata": {"source": "integration"},
            "variables_redacted": False,
        }
        for index, render_id in enumerate(render_ids)
    ]
    try:
        async with db.tx() as tx:
            assert await audit_repository.with_db(tx).create_events_batch(events) == 2
            assert await audit_repository.with_db(tx).create_payloads_batch(payloads) == 2
            assert await PromptRegistryRepository(tx).create_render_logs_batch(renders) == 2

        async with db.tx() as tx:
            assert await audit_repository.with_db(tx).create_events_batch(events) == 0
            assert await audit_repository.with_db(tx).create_payloads_batch(payloads) == 0
            assert await PromptRegistryRepository(tx).create_render_logs_batch(renders) == 0
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_auditpayload WHERE payload_id = ANY($1::uuid[])",
            payload_ids,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_auditevent WHERE event_id = ANY($1::uuid[])",
            event_ids,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_promptrenderlog WHERE prompt_render_log_id = ANY($1::text[])",
            render_ids,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_real_prompt_render_write_uses_current_database_policy() -> None:
    db = await _connect_prisma()
    organization_id = f"integration-org-{uuid4()}"
    render_ids = [str(uuid4()), str(uuid4())]
    service = AuditService(
        AuditRepository(db),
        db_client=db,
        prompt_repository=PromptRegistryRepository(db),
    )
    await service.start()

    def event(event_id: str) -> PromptRenderEvent:
        return PromptRenderEvent(
            prompt_render_log_id=event_id,
            request_id="same-client-request-id",
            api_key="integration-key",
            organization_id=organization_id,
            model="integration-model",
            prompt_key="integration.prompt",
            label="production",
            status="success",
            latency_ms=1,
            variables={"secret": "value"},
        )

    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id,
                audit_content_storage_enabled,
                audit_content_policy_version
            ) VALUES ($1, FALSE, 1)
            """,
            organization_id,
        )
        # A stale local cache must never override the policy checked in the
        # transaction that persists content-bearing prompt render metadata.
        service._content_policy_cache[organization_id] = (float("inf"), True)
        await service.enqueue_prompt_render(event(render_ids[0]))
        await service._queue.join()

        await db.execute_raw(
            """
            UPDATE deltallm_organizationtable
            SET audit_content_storage_enabled = TRUE,
                audit_content_policy_version = audit_content_policy_version + 1
            WHERE organization_id = $1
            """,
            organization_id,
        )
        await service.enqueue_prompt_render(event(render_ids[1]))
        await service._queue.join()

        rows = await db.query_raw(
            """
            SELECT prompt_render_log_id, variables, variables_redacted
            FROM deltallm_promptrenderlog
            WHERE prompt_render_log_id = ANY($1::text[])
            ORDER BY prompt_render_log_id
            """,
            render_ids,
        )
        by_id = {str(row["prompt_render_log_id"]): row for row in rows}

        assert by_id[render_ids[0]]["variables"] is None
        assert by_id[render_ids[0]]["variables_redacted"] is True
        assert by_id[render_ids[1]]["variables"] == {"secret": "value"}
        assert by_id[render_ids[1]]["variables_redacted"] is False
    finally:
        await service.shutdown()
        await db.execute_raw(
            "DELETE FROM deltallm_promptrenderlog WHERE prompt_render_log_id = ANY($1::text[])",
            render_ids,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_real_prompt_render_bundle_uses_one_policy_and_capacity_decision() -> None:
    db = await _connect_prisma()
    organization_id = f"integration-org-{uuid4()}"
    render_id = str(uuid4())
    audit_id = str(uuid4())
    repository = AuditIngestionRepository(db)
    envelopes = [
        AuditOutboxEnvelope(
            event_id=render_id,
            record_type="prompt_render",
            organization_id=organization_id,
            delivery_class="required",
            payload={"prompt_render_log_id": render_id, "variables": {"secret": "value"}},
            redacted_payload={"prompt_render_log_id": render_id, "variables": None},
            max_attempts=3,
        ),
        AuditOutboxEnvelope(
            event_id=audit_id,
            record_type="audit_event",
            organization_id=organization_id,
            delivery_class="best_effort",
            payload={"event": {"action": "PROMPT_RESOLUTION_REQUEST"}},
            redacted_payload={"event": {"action": "PROMPT_RESOLUTION_REQUEST"}},
            max_attempts=3,
        ),
    ]
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id,
                audit_content_storage_enabled,
                audit_content_policy_version
            ) VALUES ($1, FALSE, 1)
            """,
            organization_id,
        )
        before = await repository.reconcile_capacity()
        first = await repository.enqueue_bundle(
            envelopes=envelopes,
            max_pending_events=before + 10,
            required_reserve=2,
        )
        duplicate = await repository.enqueue_bundle(
            envelopes=envelopes,
            max_pending_events=before + 10,
            required_reserve=2,
        )

        assert first.statuses == {render_id: "accepted", audit_id: "accepted"}
        assert first.pending_count == before + 2
        assert duplicate.statuses == {render_id: "duplicate", audit_id: "duplicate"}
        assert duplicate.pending_count == before + 2
        rows = await db.query_raw(
            """
            SELECT event_id, payload_json, policy_version
            FROM deltallm_audit_ingestion_outbox
            WHERE event_id = ANY($1::text[])
            """,
            [render_id, audit_id],
        )
        by_id = {str(row["event_id"]): row for row in rows}
        assert by_id[render_id]["payload_json"]["variables"] is None
        assert int(by_id[render_id]["policy_version"]) == 1
        assert by_id[audit_id]["payload_json"]["event"]["action"] == ("PROMPT_RESOLUTION_REQUEST")
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_audit_ingestion_outbox WHERE event_id = ANY($1::text[])",
            [render_id, audit_id],
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await repository.reconcile_capacity()
        await db.disconnect()


@pytest.mark.asyncio
async def test_policy_disable_committing_first_forces_redacted_audit_enqueue() -> None:
    holder_db = await _connect_prisma()
    enqueue_db = await _connect_prisma()
    observer_db = await _connect_prisma()
    organization_id = f"integration-org-{uuid4()}"
    event_id = str(uuid4())
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()
    holder_task: asyncio.Task[None] | None = None
    enqueue_task: asyncio.Task[Any] | None = None
    repository = AuditIngestionRepository(enqueue_db)

    async def disable_while_holding_policy_lock() -> None:
        async with holder_db.tx() as tx:
            await tx.query_raw(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-content-policy:' || $1, 0)
                )::text AS locked
                """,
                organization_id,
            )
            await tx.execute_raw(
                """
                UPDATE deltallm_organizationtable
                SET audit_content_storage_enabled = FALSE,
                    audit_content_policy_version = audit_content_policy_version + 1
                WHERE organization_id = $1
                """,
                organization_id,
            )
            holder_ready.set()
            await release_holder.wait()

    try:
        await holder_db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id,
                audit_content_storage_enabled,
                audit_content_policy_version
            ) VALUES ($1, TRUE, 1)
            """,
            organization_id,
        )
        before = await repository.reconcile_capacity()
        holder_task = asyncio.create_task(disable_while_holding_policy_lock())
        await asyncio.wait_for(holder_ready.wait(), timeout=5)

        async with enqueue_db.tx() as tx:
            pid_rows = await tx.query_raw("SELECT pg_backend_pid()::int AS pid")
            backend_pid = int(pid_rows[0]["pid"])
            enqueue_task = asyncio.create_task(
                AuditIngestionRepository(tx).enqueue(
                    event_id=event_id,
                    record_type="audit_event",
                    organization_id=organization_id,
                    delivery_class="required",
                    payload={"payloads": [{"content_json": {"secret": "raw"}}]},
                    redacted_payload={"payloads": [{"content_json": None}]},
                    max_attempts=3,
                    max_pending_events=before + 10,
                    required_reserve=0,
                )
            )
            await _wait_for_advisory_lock_wait(observer_db, backend_pid)
            release_holder.set()
            await holder_task
            result = await enqueue_task

        assert result.status == "accepted"
        rows = await observer_db.query_raw(
            """
            SELECT payload_json, policy_version
            FROM deltallm_audit_ingestion_outbox
            WHERE event_id = $1
            """,
            event_id,
        )
        assert rows[0]["payload_json"]["payloads"][0]["content_json"] is None
        assert int(rows[0]["policy_version"]) == 2
    finally:
        release_holder.set()
        for task in (enqueue_task, holder_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (enqueue_task, holder_task) if task is not None),
            return_exceptions=True,
        )
        await observer_db.execute_raw(
            "DELETE FROM deltallm_audit_ingestion_outbox WHERE event_id = $1",
            event_id,
        )
        await observer_db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await AuditIngestionRepository(observer_db).reconcile_capacity()
        await holder_db.disconnect()
        await enqueue_db.disconnect()
        await observer_db.disconnect()


@pytest.mark.asyncio
async def test_worker_policy_read_uses_snapshot_after_waiting_for_disable() -> None:
    holder_db = await _connect_prisma()
    worker_db = await _connect_prisma()
    observer_db = await _connect_prisma()
    organization_id = f"integration-org-{uuid4()}"
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()
    holder_task: asyncio.Task[None] | None = None
    lock_task: asyncio.Task[None] | None = None

    async def disable_while_holding_policy_lock() -> None:
        async with holder_db.tx() as tx:
            await tx.query_raw(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-content-policy:' || $1, 0)
                )::text AS locked
                """,
                organization_id,
            )
            await tx.execute_raw(
                """
                UPDATE deltallm_organizationtable
                SET audit_content_storage_enabled = FALSE,
                    audit_content_policy_version = audit_content_policy_version + 1
                WHERE organization_id = $1
                """,
                organization_id,
            )
            holder_ready.set()
            await release_holder.wait()

    try:
        await holder_db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id,
                audit_content_storage_enabled,
                audit_content_policy_version
            ) VALUES ($1, TRUE, 1)
            """,
            organization_id,
        )
        holder_task = asyncio.create_task(disable_while_holding_policy_lock())
        await asyncio.wait_for(holder_ready.wait(), timeout=5)

        async with worker_db.tx() as tx:
            pid_rows = await tx.query_raw("SELECT pg_backend_pid()::int AS pid")
            backend_pid = int(pid_rows[0]["pid"])
            transactional = AuditIngestionRepository(tx)
            lock_task = asyncio.create_task(transactional.lock_content_policies([organization_id]))
            await _wait_for_advisory_lock_wait(observer_db, backend_pid)
            release_holder.set()
            await holder_task
            await lock_task
            policies = await transactional.get_content_policies([organization_id])

        assert policies == {organization_id: (False, 2)}
    finally:
        release_holder.set()
        for task in (lock_task, holder_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (lock_task, holder_task) if task is not None),
            return_exceptions=True,
        )
        await observer_db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await holder_db.disconnect()
        await worker_db.disconnect()
        await observer_db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_name", ["spend", "audit"])
@pytest.mark.parametrize("waiter_kind", ["new", "duplicate"])
async def test_enqueue_waiter_uses_fresh_capacity_and_duplicate_snapshot(
    queue_name: str,
    waiter_kind: str,
) -> None:
    holder_db = await _connect_prisma()
    waiter_db = await _connect_prisma()
    observer_db = await _connect_prisma()
    holder_event_id = str(uuid4())
    waiter_event_id = holder_event_id if waiter_kind == "duplicate" else str(uuid4())
    table_name = (
        "deltallm_spend_ingestion_outbox"
        if queue_name == "spend"
        else "deltallm_audit_ingestion_outbox"
    )
    repository = (
        SpendIngestionRepository(waiter_db)
        if queue_name == "spend"
        else AuditIngestionRepository(waiter_db)
    )
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()
    holder_task: asyncio.Task[None] | None = None
    waiter_task: asyncio.Task[Any] | None = None

    async def insert_last_slot_while_holding_capacity_lock() -> None:
        lock_key = f"deltallm:{queue_name}-ingestion-capacity"
        async with holder_db.tx() as tx:
            await tx.query_raw(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))::text AS locked",
                lock_key,
            )
            if queue_name == "spend":
                await tx.execute_raw(
                    """
                    INSERT INTO deltallm_spend_ingestion_outbox (
                        event_id, event_type, payload_json
                    ) VALUES ($1, 'spend', '{}'::jsonb)
                    """,
                    holder_event_id,
                )
            else:
                await tx.execute_raw(
                    """
                    INSERT INTO deltallm_audit_ingestion_outbox (
                        event_id, record_type, delivery_class,
                        payload_json, redacted_payload_json
                    ) VALUES (
                        $1, 'audit_event', 'required', '{}'::jsonb, '{}'::jsonb
                    )
                    """,
                    holder_event_id,
                )
            await tx.execute_raw(
                """
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = pending_count + 1,
                    updated_at = NOW()
                WHERE queue_name = $1
                """,
                queue_name,
            )
            holder_ready.set()
            await release_holder.wait()

    try:
        before = await repository.reconcile_capacity()
        holder_task = asyncio.create_task(insert_last_slot_while_holding_capacity_lock())
        await asyncio.wait_for(holder_ready.wait(), timeout=5)

        async with waiter_db.tx() as tx:
            pid_rows = await tx.query_raw("SELECT pg_backend_pid()::int AS pid")
            backend_pid = int(pid_rows[0]["pid"])
            if queue_name == "spend":
                waiter_task = asyncio.create_task(
                    SpendIngestionRepository(tx).enqueue(
                        event_id=waiter_event_id,
                        event_type="spend",
                        payload={},
                        max_attempts=3,
                        max_pending_events=before + 1,
                    )
                )
            else:
                waiter_task = asyncio.create_task(
                    AuditIngestionRepository(tx).enqueue(
                        event_id=waiter_event_id,
                        record_type="audit_event",
                        organization_id=None,
                        delivery_class="required",
                        payload={},
                        redacted_payload={},
                        max_attempts=3,
                        max_pending_events=before + 1,
                        required_reserve=0,
                    )
                )
            await _wait_for_advisory_lock_wait(observer_db, backend_pid)
            release_holder.set()
            await holder_task
            result = await waiter_task

        expected_status = "duplicate" if waiter_kind == "duplicate" else "full"
        assert result.status == expected_status
        assert result.pending_count == before + 1
        rows = await observer_db.query_raw(
            """
            SELECT pending_count
            FROM deltallm_telemetry_ingestion_capacity
            WHERE queue_name = $1
            """,
            queue_name,
        )
        assert int(rows[0]["pending_count"]) == before + 1
    finally:
        release_holder.set()
        for task in (waiter_task, holder_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (waiter_task, holder_task) if task is not None),
            return_exceptions=True,
        )
        await observer_db.execute_raw(
            f"DELETE FROM {table_name} WHERE event_id = ANY($1::text[])",
            [holder_event_id, waiter_event_id],
        )
        await repository.with_db(observer_db).reconcile_capacity()
        await holder_db.disconnect()
        await waiter_db.disconnect()
        await observer_db.disconnect()


@pytest.mark.asyncio
async def test_real_required_audit_failure_rolls_back_telemetry_replay() -> None:
    db = await _connect_prisma()
    blocked_event_id = str(uuid4())
    audit_event_id = str(uuid4())
    ingestion_repository = AuditIngestionRepository(db)
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_audit_ingestion_outbox (
                event_id, record_type, delivery_class, payload_json,
                redacted_payload_json, status, blocked_at
            ) VALUES (
                $1, 'audit_event', 'required', '{}'::jsonb, '{}'::jsonb,
                'blocked', NOW()
            )
            """,
            blocked_event_id,
        )
        await ingestion_repository.reconcile_capacity()
        service = TelemetryReplayService(db, audit_service=object())

        async def write_then_fail(repository: AuditRepository) -> None:
            await repository.create_event(
                AuditEventRecord(
                    event_id=audit_event_id,
                    action="ADMIN_TELEMETRY_INGESTION_REPLAY",
                )
            )
            raise RuntimeError("forced audit failure")

        with pytest.raises(TelemetryReplayUnavailableError):
            await service.replay_blocked(
                queue_name="audit",
                event_id=blocked_event_id,
                replayed_by="integration-admin",
                audit_writer=write_then_fail,
            )

        replay_rows = await db.query_raw(
            """
            SELECT status, replay_count
            FROM deltallm_audit_ingestion_outbox
            WHERE event_id = $1
            """,
            blocked_event_id,
        )
        audit_rows = await db.query_raw(
            "SELECT event_id FROM deltallm_auditevent WHERE event_id = $1::uuid",
            audit_event_id,
        )
        assert replay_rows[0]["status"] == "blocked"
        assert int(replay_rows[0]["replay_count"]) == 0
        assert audit_rows == []
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_auditevent WHERE event_id = $1::uuid",
            audit_event_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_audit_ingestion_outbox WHERE event_id = $1",
            blocked_event_id,
        )
        await ingestion_repository.reconcile_capacity()
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("queue_name", "table_name", "repository_type", "record_type"),
    [
        ("spend", "deltallm_spend_ingestion_outbox", SpendIngestionRepository, None),
        ("audit", "deltallm_audit_ingestion_outbox", AuditIngestionRepository, "audit_event"),
    ],
)
async def test_capacity_reconciliation_observes_concurrent_completion(
    queue_name: str,
    table_name: str,
    repository_type: type,
    record_type: str | None,
) -> None:
    worker_db = await _connect_prisma()
    reconciler_db = await _connect_prisma()
    event_id = str(uuid4())
    worker_id = f"integration-{queue_name}"
    try:
        active_rows = await worker_db.query_raw(
            f"""
            SELECT COUNT(*)::bigint AS count
            FROM {table_name}
            WHERE status IN ('queued', 'retry', 'processing', 'blocked')
            """
        )
        active_before = int(active_rows[0]["count"])
        await worker_db.execute_raw(
            """
            UPDATE deltallm_telemetry_ingestion_capacity
            SET pending_count = $2
            WHERE queue_name = $1
            """,
            queue_name,
            active_before + 1,
        )
        if queue_name == "spend":
            await worker_db.execute_raw(
                """
                INSERT INTO deltallm_spend_ingestion_outbox (
                    event_id, event_type, payload_json, status, locked_by,
                    claim_token, lease_expires_at
                ) VALUES (
                    $1, 'spend', '{}'::jsonb, 'processing', $2,
                    'claim-1', NOW() + INTERVAL '1 minute'
                )
                """,
                event_id,
                worker_id,
            )
        else:
            await worker_db.execute_raw(
                """
                INSERT INTO deltallm_audit_ingestion_outbox (
                    event_id, record_type, delivery_class, payload_json,
                    redacted_payload_json, status, locked_by, claim_token,
                    lease_expires_at
                ) VALUES (
                    $1, $2, 'required', '{}'::jsonb, '{}'::jsonb,
                    'processing', $3, 'claim-1', NOW() + INTERVAL '1 minute'
                )
                """,
                event_id,
                record_type,
                worker_id,
            )

        async with worker_db.tx() as tx:
            completed = await repository_type(tx).mark_completed(
                event_ids=[event_id],
                worker_id=worker_id,
                claim_token="claim-1",
            )
            assert completed == 1
            reconcile_task = asyncio.create_task(
                repository_type(reconciler_db).reconcile_capacity()
            )
            await asyncio.sleep(0.1)
            assert not reconcile_task.done()

        reconciled = await asyncio.wait_for(reconcile_task, timeout=5)
        rows = await reconciler_db.query_raw(
            """
            SELECT pending_count
            FROM deltallm_telemetry_ingestion_capacity
            WHERE queue_name = $1
            """,
            queue_name,
        )
        actual_rows = await reconciler_db.query_raw(
            f"""
            SELECT COUNT(*)::bigint AS count
            FROM {table_name}
            WHERE status IN ('queued', 'retry', 'processing', 'blocked')
            """
        )
        actual = int(actual_rows[0]["count"])
        assert reconciled == actual
        assert int(rows[0]["pending_count"]) == actual
    finally:
        await worker_db.execute_raw(f"DELETE FROM {table_name} WHERE event_id = $1", event_id)
        await worker_db.disconnect()
        await reconciler_db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_name", ["spend", "audit"])
async def test_real_claim_token_fences_stale_telemetry_worker(queue_name: str) -> None:
    first_db = await _connect_prisma()
    second_db = await _connect_prisma()
    event_id = str(uuid4())
    first = (
        SpendIngestionRepository(first_db)
        if queue_name == "spend"
        else AuditIngestionRepository(first_db)
    )
    second = (
        SpendIngestionRepository(second_db)
        if queue_name == "spend"
        else AuditIngestionRepository(second_db)
    )
    table_name = (
        "deltallm_spend_ingestion_outbox"
        if queue_name == "spend"
        else "deltallm_audit_ingestion_outbox"
    )
    try:
        if queue_name == "spend":
            await first.enqueue(
                event_id=event_id,
                event_type="spend",
                payload={},
                max_attempts=3,
                max_pending_events=10_000_000,
            )
        else:
            await first.enqueue(
                event_id=event_id,
                record_type="audit_event",
                organization_id=None,
                delivery_class="required",
                payload={},
                redacted_payload={},
                max_attempts=3,
                max_pending_events=10_000_000,
                required_reserve=0,
            )

        first_claim = await first.claim_batch(
            limit=1,
            worker_id="worker-1",
            claim_token="claim-1",
            lease_seconds=30,
        )
        assert [record.event_id for record in first_claim] == [event_id]
        await first_db.execute_raw(
            f"UPDATE {table_name} SET lease_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE event_id = $1",
            event_id,
        )
        second_claim = await second.claim_batch(
            limit=1,
            worker_id="worker-2",
            claim_token="claim-2",
            lease_seconds=30,
        )
        assert [record.event_id for record in second_claim] == [event_id]

        stale_completion = await first.mark_completed(
            event_ids=[event_id],
            worker_id="worker-1",
            claim_token="claim-1",
        )
        current_completion = await second.mark_completed(
            event_ids=[event_id],
            worker_id="worker-2",
            claim_token="claim-2",
        )
        assert stale_completion == 0
        assert current_completion == 1
    finally:
        await first_db.execute_raw(f"DELETE FROM {table_name} WHERE event_id = $1", event_id)
        await first_db.disconnect()
        await second_db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_name", ["spend", "audit"])
async def test_real_required_telemetry_block_and_replay_keeps_capacity(
    queue_name: str,
) -> None:
    db = await _connect_prisma()
    event_id = str(uuid4())
    repository = (
        SpendIngestionRepository(db) if queue_name == "spend" else AuditIngestionRepository(db)
    )
    table_name = (
        "deltallm_spend_ingestion_outbox"
        if queue_name == "spend"
        else "deltallm_audit_ingestion_outbox"
    )
    try:
        before = await repository.reconcile_capacity()
        if queue_name == "spend":
            await repository.enqueue(
                event_id=event_id,
                event_type="spend",
                payload={},
                max_attempts=1,
                max_pending_events=10_000_000,
            )
        else:
            await repository.enqueue(
                event_id=event_id,
                record_type="audit_event",
                organization_id=None,
                delivery_class="required",
                payload={},
                redacted_payload={},
                max_attempts=1,
                max_pending_events=10_000_000,
                required_reserve=0,
            )
        claimed = await repository.claim_batch(
            limit=1,
            worker_id="worker-1",
            claim_token="claim-1",
            lease_seconds=30,
        )
        target = next(record for record in claimed if record.event_id == event_id)
        terminal = await repository.mark_retry(
            record=target,
            worker_id="worker-1",
            error="poison payload",
        )
        assert terminal is True

        blocked_rows = await db.query_raw(
            f"SELECT status FROM {table_name} WHERE event_id = $1",
            event_id,
        )
        capacity_rows = await db.query_raw(
            "SELECT pending_count FROM deltallm_telemetry_ingestion_capacity WHERE queue_name = $1",
            queue_name,
        )
        assert blocked_rows[0]["status"] == "blocked"
        assert int(capacity_rows[0]["pending_count"]) == before + 1

        replayed = await repository.replay_blocked(
            event_id=event_id,
            replayed_by="integration-admin",
        )
        assert replayed is True
        replay_rows = await db.query_raw(
            f"SELECT status, attempt_count, replay_count FROM {table_name} WHERE event_id = $1",
            event_id,
        )
        assert replay_rows[0]["status"] == "retry"
        assert int(replay_rows[0]["attempt_count"]) == 0
        assert int(replay_rows[0]["replay_count"]) == 1
    finally:
        await db.execute_raw(f"DELETE FROM {table_name} WHERE event_id = $1", event_id)
        await repository.reconcile_capacity()
        await db.disconnect()
