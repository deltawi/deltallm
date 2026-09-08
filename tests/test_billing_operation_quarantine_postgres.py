import asyncio
from decimal import Decimal
import json

import pytest
from prisma.errors import RawQueryError

from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.billing_operation_recovery import BillingOperationRecovery
from src.db.billing_operations import BillingOperationRepository
from src.db.errors import is_record_specific_database_error
from tests import billing_operation_fixtures as fixtures
from tests.test_billing_operations_postgres import deadline, hold
from tests.test_selector_charge_db_integration import _scope_totals

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
selector_billing_db = fixtures.selector_billing_db
operation_db = fixtures.operation_db
review_operation_db = fixtures.review_operation_db


async def accept(db, operation, charge):
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    await repository.dispatch(operation, component="selector", expires_at=deadline())
    await repository.accept_selector(operation, charge, expires_at=deadline())


def conflicting_payload(charge):
    payload = charge.spend_payload()
    payload["cost_exact"] = str(charge.customer_charge + Decimal("0.01"))
    return payload


async def write_event(db, charge, payload):
    async with db.tx() as tx:
        await SpendTrackingService(tx).log_batch_once(
            [(charge.attribution.component_event_id, "spend", payload)]
        )


async def test_poison_receipt_is_durable_and_neighbors_recover_across_workers(review_operation_db):
    db, bad, bad_charge = review_operation_db
    good, good_charge = fixtures.another_operation(bad, bad_charge)
    abandoned, _ = fixtures.another_operation(bad, bad_charge)
    await accept(db, bad, bad_charge)
    await accept(db, good, good_charge)
    await BillingOperationRepository(db).reserve(abandoned, expires_at=deadline())
    await fixtures.expire(db, abandoned)
    # Deliberately inconsistent durable event in this disposable DB only.
    await write_event(db, bad_charge, conflicting_payload(bad_charge))
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET updated_at=NOW()-interval '1 minute' WHERE operation_id=$1",
        str(bad.attribution.operation_id),
    )
    original = await fixtures.operation_row(db, bad)
    ledger = await _scope_totals(db, bad.attribution.api_key)
    first = await fixtures.recovery(db)._recover_one("receipts")
    assert not first.unavailable
    blocked = await fixtures.operation_row(db, bad)
    assert blocked["recovery_error_code"] == "receipt_conflict"
    assert blocked["recovery_blocked_at"] is not None
    for field in ("snapshot", "selector_receipt", "selector_state", "answer_state", "closed_at"):
        assert blocked[field] == original[field]
    assert (
        await hold(db, bad)
        == bad.total_allowance + good.total_allowance + abandoned.total_allowance
    )
    assert await _scope_totals(db, bad.attribution.api_key) == ledger
    await asyncio.gather(fixtures.recovery(db).recover(), fixtures.recovery(db).recover())
    assert (await fixtures.operation_row(db, abandoned))["closed_at"] is not None
    service = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
        operation_recovery=fixtures.recovery(db),
    )
    records = await service._claim_batch()
    assert [record.event_id for record in records] == [good.attribution.component_event_id]
    await service._process_batch(records)
    await BillingOperationRepository(db).unattempted(
        good, component="answer", expires_at=deadline()
    )
    assert await hold(db, bad) == bad.total_allowance
    assert await _scope_totals(db, bad.attribution.api_key) == {
        scope: amount + good_charge.customer_charge for scope, amount in ledger.items()
    }
    assert await fixtures.recovery(db).recover() == 0
    assert (await fixtures.operation_row(db, bad))["recovery_blocked_at"] == blocked[
        "recovery_blocked_at"
    ]


async def test_strict_writer_rolls_back_conflicting_charge_and_exposes_record_error(
    review_operation_db,
):
    db, operation, charge = review_operation_db
    await accept(db, operation, charge)
    original = await _scope_totals(db, operation.attribution.api_key)
    with pytest.raises(RawQueryError, match="billing_component_receipt_conflict") as error:
        async with db.tx() as tx:
            ids = [charge.attribution.component_event_id]
            await BillingOperationRecovery.lock_for_events(tx, ids)
            await SpendTrackingService(tx).log_batch_once(
                [(ids[0], "spend", conflicting_payload(charge))]
            )
            await BillingOperationRecovery.settle_events(tx, ids)
    assert is_record_specific_database_error(error.value)
    assert await _scope_totals(db, operation.attribution.api_key) == original
    assert await hold(db, operation) == operation.total_allowance
    assert not await db.query_raw(
        "SELECT id FROM deltallm_spendlog_events WHERE id=$1", charge.attribution.component_event_id
    )


async def test_explicit_requeue_of_reconciled_evidence_is_idempotent(review_operation_db):
    db, operation, charge = review_operation_db
    await accept(db, operation, charge)
    # Models a previously investigated quarantine whose authoritative evidence is
    # now consistent. Requeue changes scheduling only, not the frozen charge/holds.
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET recovery_blocked_at=NOW(), "
        "recovery_error_code='receipt_conflict' WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    assert await fixtures.recovery(db).recover() == 0
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET recovery_blocked_at=NULL,recovery_error_code=NULL "
        "WHERE operation_id=$1 AND recovery_error_code='receipt_conflict'",
        str(operation.attribution.operation_id),
    )
    await fixtures.recovery(db).recover()
    await fixtures.recovery(db).recover()
    rows = await db.query_raw(
        "SELECT event_id FROM deltallm_spend_ingestion_outbox WHERE event_id=$1",
        charge.attribution.component_event_id,
    )
    assert len(rows) == 1
    assert await hold(db, operation) == operation.total_allowance


async def test_integrity_failure_rolls_back_partial_release_and_keeps_capacity(review_operation_db):
    db, operation, charge = review_operation_db
    await accept(db, operation, charge)
    await fixtures.expire(db, operation)
    await write_event(db, charge, charge.spend_payload())
    # Deliberately damaged last counter: preceding scope updates must roll back.
    await db.execute_raw(
        "UPDATE deltallm_teammodelspend SET reserved_spend_exact=0 WHERE team_id=$1 AND model=$2",
        operation.attribution.team_id,
        operation.attribution.model_group,
    )
    before = await fixtures.capacity(db)
    await fixtures.recovery(db).recover()
    row = await fixtures.operation_row(db, operation)
    assert row["recovery_error_code"] == "integrity_failure"
    assert row["selector_state"] == "accepted"
    assert row["answer_state"] == "reserved"
    assert row["closed_at"] is None
    assert await hold(db, operation) == operation.total_allowance
    assert await fixtures.capacity(db) == before


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_quarantine_does_not_escape_rolled_back_transaction(review_operation_db, error):
    db, operation, charge = review_operation_db
    await accept(db, operation, charge)
    await write_event(db, charge, conflicting_payload(charge))
    with pytest.raises(error):
        async with db.tx() as tx:
            outcome = await tx.query_raw(
                "SELECT deltallm_recover_operation_isolated($1) AS outcome",
                str(operation.attribution.operation_id),
            )
            assert outcome[0]["outcome"] == "receipt_conflict"
            raise error()
    assert (await fixtures.operation_row(db, operation))["recovery_blocked_at"] is None
    assert await hold(db, operation) == operation.total_allowance


@pytest.mark.parametrize(
    "assignment",
    [
        "recovery_blocked_at=NOW()",
        "recovery_error_code='receipt_conflict'",
        "recovery_blocked_at=NOW(),recovery_error_code='private-provider-text'",
    ],
)
async def test_quarantine_status_is_paired_and_allowlisted_in_database(
    review_operation_db, assignment
):
    db, operation, _ = review_operation_db
    await BillingOperationRepository(db).reserve(operation, expires_at=deadline())
    with pytest.raises(RawQueryError):
        await db.execute_raw(
            f"UPDATE deltallm_billing_operations SET {assignment} WHERE operation_id=$1",
            str(operation.attribution.operation_id),
        )
    assert (await fixtures.operation_row(db, operation))["recovery_blocked_at"] is None


async def test_receipt_index_excludes_large_quarantined_history(review_operation_db):
    db, operation, charge = review_operation_db
    await accept(db, operation, charge)
    await db.execute_raw(
        "INSERT INTO deltallm_billing_operations "
        "(operation_id,owner_token,api_key,model,snapshot,selector_event_id,"
        "selector_allowance,answer_allowance,selector_state,selector_receipt,"
        "recovery_blocked_at,recovery_error_code,expires_at) "
        "SELECT $1||n::text,'test-owner',$2,'group','{}'::jsonb,$1||'event'||n::text,"
        "0,0,'accepted','{}'::jsonb,NOW(),'receipt_conflict',NOW()+interval '5 minutes' "
        "FROM generate_series(1,5000) n",
        str(operation.owner_token),
        operation.attribution.api_key,
    )
    await db.execute_raw("ANALYZE deltallm_billing_operations")
    rows = await db.query_raw(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT operation_id "
        "FROM deltallm_billing_operations WHERE selector_state='accepted' AND recovery_blocked_at IS NULL "
        "ORDER BY updated_at,operation_id FOR UPDATE SKIP LOCKED LIMIT 1"
    )
    plan = json.dumps(rows)
    assert "deltallm_billing_operations_receipt_idx" in plan
    assert "Seq Scan" not in plan
