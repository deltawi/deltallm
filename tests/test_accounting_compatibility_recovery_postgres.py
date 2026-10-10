"""Actual recovery events must advance compatibility reporting exactly once."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.reporting.accounting_projection import (
    AccountingCompatibilityProjector,
    AccountingProjectionConfig,
    AccountingProjectionWorker,
)
from src.billing.spend.spend import SpendTrackingService
from src.db.accounting.reporting.accounting_projection import AccountingProjectionRepository
from src.db.accounting.accounting_protocol import AccountingProtocolRepository
from src.db.audit.audit_ingestion import AuditIngestionRepository
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db
PROJECTION = AccountingProjectionWorker.PROJECTION_NAME


async def test_expiry_and_resolution_audits_keep_identity_and_capacity_on_replay(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    await AccountingProtocolRepository(db, statement_budget_seconds=2).reserve_batch(
        [item], expires_at=deadline()
    )
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET created_at=NOW()-INTERVAL '10 minutes',"
        "expires_at=NOW()-INTERVAL '1 second' "
        "WHERE operation_id=$1",
        str(item.operation_id),
    )
    audit = AuditIngestionRepository(db)
    projection = AccountingProjectionRepository(db)
    projector = AccountingCompatibilityProjector(
        spend=SpendTrackingService(db),
        audit=audit,
        config=AccountingProjectionConfig(generation=generation, worker_id="recovery-audit"),
    )
    identifiers = []
    try:
        assert await projection.reconcile_expired(generation=generation, limit=1) == 1
        assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(1))
        await db.query_raw(
            "SELECT deltallm_accounting_resolve_provisional($1,$2,0,'null'::jsonb,$3) AS sequence",
            generation,
            str(item.operation_id),
            "provider confirmed no charge",
        )
        assert await _settle_grants(db, generation) == 0
        expected = await db.query_raw(
            "SELECT md5('deltallm-accounting-audit:v2:'||event_id)::uuid::text AS audit_id,"
            "sequence,event_id FROM deltallm_accounting_events WHERE generation=$1 "
            "AND event_type IN ('finalized','reconciled') ORDER BY sequence",
            generation,
        )
        assert [row["event_id"] for row in expected] == [
            f"{item.operation_id}:expired:v2",
            f"{item.operation_id}:reconciled:v2",
        ]
        identifiers = [row["audit_id"] for row in expected]
        before = await audit.reconcile_capacity()
        await projection.initialize(projection_name=PROJECTION, generation=generation)
        for replay in (False, True):
            if replay:
                # Controlled, stopped-owner replay: do not truncate any sink.
                await db.execute_raw(
                    "UPDATE deltallm_accounting_projection_checkpoints SET last_sequence=0 "
                    "WHERE projection_name=$1 AND generation=$2",
                    PROJECTION,
                    generation,
                )
            claim = await projection.claim_next(
                projection_name=PROJECTION,
                generation=generation,
                worker_id="recovery-audit",
                lease_seconds=30,
            )
            assert claim is not None
            events = await projection.read_batch(claim, limit=2)
            assert len(events) == 2
            await projector.project_batch(events)
            # Simulate a lost acknowledgement after sinks committed, before the
            # checkpoint: retries must preserve rows and the capacity counter.
            await projector.project_batch(events)
            assert await projection.complete(claim, last_sequence=events[-1].sequence)
            assert await projection.backlog(projection_name=PROJECTION, generation=generation) == (
                0,
                0.0,
            )
            rows = await db.query_raw(
                "SELECT event_id,delivery_class,payload_json,redacted_payload_json "
                "FROM deltallm_audit_ingestion_outbox WHERE event_id=ANY($1::text[])",
                identifiers,
            )
            assert {row["event_id"] for row in rows} == set(identifiers)
            assert all(row["delivery_class"] == "required" for row in rows)
            assert all(row["payload_json"] == row["redacted_payload_json"] for row in rows)
            assert await audit.reconcile_capacity() == before + 2
            assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(0))
            assert await db.query_raw(
                "SELECT count(*)::integer AS count FROM deltallm_spendlog_events WHERE api_key=$1",
                item.attribution.api_key,
            ) == [{"count": 0}]
    finally:
        if identifiers:
            await db.execute_raw(
                "DELETE FROM deltallm_audit_ingestion_outbox WHERE event_id=ANY($1::text[])",
                identifiers,
            )
            await audit.reconcile_capacity()
