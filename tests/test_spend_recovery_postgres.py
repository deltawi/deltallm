"""Process-death and exactly-once economic recovery against the migrated database."""

import asyncio
from decimal import Decimal
import json
import os
import signal
import sys
from uuid import uuid4

import pytest

from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.billing.spend_operations import OperationPrincipal
from src.billing.spend_reconciliation import SpendOperationResolution
from src.db.spend_ingestion import SpendIngestionRepository, SpendOutboxRecord
from src.db.spend_operations import SpendOperationRepository
from src.db.spend_reconciliation import SpendReconciliationRepository
from tests.test_spend_operations_postgres import accept, begin, deadline, handle, payload, row
from tests.test_telemetry_ingestion_db_integration import _connect_prisma

pytestmark = pytest.mark.postgres


@pytest.fixture
async def economic_operation():
    db = await _connect_prisma()
    operation = handle()
    key = "spend-recovery-test-" + uuid4().hex
    operation = operation.model_copy(
        update={
            "intent": operation.intent.model_copy(
                update={"principal": OperationPrincipal(api_key=key)}
            )
        }
    )
    await db.execute_raw(
        "INSERT INTO deltallm_verificationtoken (id,token,models) VALUES ($1,$2,ARRAY[]::text[])",
        str(uuid4()),
        key,
    )
    try:
        yield db, operation
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_auditevent WHERE resource_type='spend_operation' AND resource_id=$1",
            str(operation.event_id),
        )
        await db.execute_raw(
            "DELETE FROM deltallm_spend_ingestion_outbox WHERE event_id=$1", str(operation.event_id)
        )
        await db.execute_raw(
            "DELETE FROM deltallm_spendlog_events WHERE id=$1", str(operation.event_id)
        )
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token=$1", key)
        await SpendIngestionRepository(db).reconcile_capacity()
        await db.disconnect()


async def settle_once(db, operation):
    token = uuid4().hex
    await db.execute_raw(
        "UPDATE deltallm_spend_ingestion_outbox SET status='processing',locked_by='test-worker',"
        "claim_token=$2,lease_expires_at=NOW()+interval '30 seconds',attempt_count=1 "
        "WHERE event_id=$1 AND status='queued'",
        str(operation.event_id),
        token,
    )
    state = await row(db, operation)
    record = SpendOutboxRecord(
        str(operation.event_id), "spend", state["payload_json"], 1, claim_token=token
    )
    service = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        config=SpendIngestionConfig(enabled=True, worker_id="test-worker"),
    )
    await service._process(record)
    return service, record


async def assert_charge_once(db, operation):
    result = await db.query_raw(
        "SELECT spend_exact::text AS amount FROM deltallm_verificationtoken WHERE token=$1",
        operation.intent.principal.api_key,
    )
    assert Decimal(result[0]["amount"]) == Decimal("0.1")
    assert (
        len(
            await db.query_raw(
                "SELECT id FROM deltallm_spendlog_events WHERE id=$1", str(operation.event_id)
            )
        )
        == 1
    )


async def test_receipt_replay_and_stale_worker_apply_one_charge(economic_operation):
    db, operation = economic_operation
    repo = SpendOperationRepository(db)
    await begin(repo, operation, capacity=100000)
    await accept(repo, operation)
    await accept(repo, operation)
    service, stale = await settle_once(db, operation)
    await service._process(stale)
    await accept(repo, operation)
    assert (await row(db, operation))["status"] == "completed"
    await assert_charge_once(db, operation)


async def test_unknown_reconciliation_is_atomic_audited_and_idempotent(economic_operation):
    db, operation = economic_operation
    repo = SpendOperationRepository(db)
    await begin(repo, operation, capacity=100000)
    await repo.mark_unknown(
        event_id=str(operation.event_id),
        owner_token=str(operation.owner_token),
        expires_at=deadline(),
    )
    resolution = SpendOperationResolution(
        event_id=operation.event_id,
        actor_id="operator-test",
        evidence_reference="review-ticket-1",
        outcome="usage_confirmed",
        cost_exact=Decimal("0.1"),
        usage={"prompt_tokens": 1},
    )
    reconciler = SpendReconciliationRepository(db)
    await reconciler.reconcile(resolution, expires_at=deadline())
    await reconciler.reconcile(resolution, expires_at=deadline())
    audits = await db.query_raw(
        "SELECT event_id FROM deltallm_auditevent WHERE resource_type='spend_operation' AND resource_id=$1",
        str(operation.event_id),
    )
    assert len(audits) == 1
    await settle_once(db, operation)
    await assert_charge_once(db, operation)


_CHILD = """
import asyncio, json, sys
from prisma import Prisma
from src.billing.spend_operations import OperationHandle
from src.db.spend_operations import SpendOperationRepository
async def main():
    data=json.loads(sys.stdin.readline())
    db=Prisma(datasource={"url":data["url"]})
    await db.connect()
    op=OperationHandle.model_validate(data["operation"])
    repo=SpendOperationRepository(db)
    await repo.begin(op,capacity=100000,max_attempts=2,expires_at=asyncio.get_running_loop().time()+.25)
    if data["phase"]=="receipt":
        await repo.accept(event_id=str(op.event_id),owner_token=str(op.owner_token),payload=data["payload"],expires_at=asyncio.get_running_loop().time()+.25)
    print("committed",flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""


@pytest.mark.parametrize("phase", ["intent", "receipt"])
async def test_process_death_retains_intent_or_recoverable_receipt(economic_operation, phase):
    db, operation = economic_operation
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        process.stdin.write(
            json.dumps(
                {
                    "url": os.environ["DATABASE_URL"],
                    "operation": operation.model_dump(mode="json"),
                    "phase": phase,
                    "payload": payload(operation),
                }
            ).encode()
            + b"\n"
        )
        await process.stdin.drain()
        assert await asyncio.wait_for(process.stdout.readline(), 10) == b"committed\n"
    finally:
        # Only our setsid child and its Prisma engine belong to this process group.
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
    if phase == "intent":
        await db.execute_raw(
            "UPDATE deltallm_spend_ingestion_outbox SET operation_expires_at=NOW() WHERE event_id=$1",
            str(operation.event_id),
        )
        await SpendOperationRepository(db).recover_expired()
        assert (await row(db, operation))["operation_state"] == "unknown"
        assert (
            await db.query_raw(
                "SELECT id FROM deltallm_spendlog_events WHERE id=$1", str(operation.event_id)
            )
            == []
        )
    else:
        await settle_once(db, operation)
        await assert_charge_once(db, operation)


async def test_cleaned_settled_event_cannot_be_dispatched_again(economic_operation):
    from src.billing.operation_reservation import BillingOperationUnavailable

    db, operation = economic_operation
    repo = SpendOperationRepository(db)
    await begin(repo, operation, capacity=100000)
    await accept(repo, operation)
    await settle_once(db, operation)
    await db.execute_raw(
        "DELETE FROM deltallm_spend_ingestion_outbox WHERE event_id=$1", str(operation.event_id)
    )
    with pytest.raises(BillingOperationUnavailable):
        await begin(repo, operation, capacity=100000)
    await assert_charge_once(db, operation)


async def test_reconciliation_audit_failure_rolls_back_receipt(economic_operation, monkeypatch):
    from unittest.mock import AsyncMock
    from src.billing.operation_reservation import BillingOperationUnavailable
    from src.db.repositories import AuditRepository

    db, operation = economic_operation
    repo = SpendOperationRepository(db)
    await begin(repo, operation, capacity=100000)
    await repo.mark_unknown(
        event_id=str(operation.event_id),
        owner_token=str(operation.owner_token),
        expires_at=deadline(),
    )
    resolution = SpendOperationResolution(
        event_id=operation.event_id,
        actor_id="operator-test",
        evidence_reference="review-ticket-2",
        outcome="not_executed",
        cost_exact=Decimal(0),
    )
    monkeypatch.setattr(
        AuditRepository, "create_event", AsyncMock(side_effect=ConnectionError("audit unavailable"))
    )
    with pytest.raises(BillingOperationUnavailable):
        await SpendReconciliationRepository(db).reconcile(resolution, expires_at=deadline())
    state = await row(db, operation)
    assert state["operation_state"] == "unknown" and state["operation_resolution"] is None
    assert state["status"] == "blocked"


async def test_reconciliation_cannot_cross_tenant_scope(economic_operation):
    from src.billing.operation_reservation import BillingOperationUnavailable

    db, operation = economic_operation
    repo = SpendOperationRepository(db)
    await begin(repo, operation, capacity=100000)
    await repo.mark_unknown(
        event_id=str(operation.event_id),
        owner_token=str(operation.owner_token),
        expires_at=deadline(),
    )
    resolution = SpendOperationResolution(
        event_id=operation.event_id,
        organization_id="other-tenant",
        actor_id="operator-test",
        evidence_reference="review-ticket-3",
        outcome="not_executed",
        cost_exact=Decimal(0),
    )
    with pytest.raises(BillingOperationUnavailable):
        await SpendReconciliationRepository(db).reconcile(resolution, expires_at=deadline())
    assert (await row(db, operation))["operation_state"] == "unknown"
