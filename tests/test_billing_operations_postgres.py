import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.operation_reservation import BillingOperationUnavailable
from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.billing_operations import BillingOperationRepository
from src.db.billing_operation_recovery import BillingOperationRecovery
from tests.test_operation_reservation import make_operation
from tests import test_selector_charge_db_integration as selector_db_fixtures

selector_billing_db = selector_db_fixtures.selector_billing_db

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.fixture
async def operation_db(selector_billing_db):
    db, charge = selector_billing_db
    operation = make_operation().model_copy(
        update={
            "attribution": charge.attribution,
            "expires_at": datetime.now(UTC) + timedelta(seconds=60),
        }
    )
    await db.execute_raw(
        "INSERT INTO deltallm_teammodelspend(team_id,model,spend,spend_exact,updated_at) "
        "VALUES ($1,$2,0,0,NOW())",
        charge.attribution.team_id,
        charge.attribution.model_group,
    )
    try:
        yield db, operation, charge
    finally:
        # Disposable fixture cleanup; production must reconcile rather than clear holds.
        await db.execute_raw(
            "UPDATE deltallm_verificationtoken SET reserved_spend_exact=0 WHERE token=$1",
            charge.attribution.api_key,
        )
        await db.execute_raw(
            "UPDATE deltallm_usertable SET reserved_spend_exact=0 WHERE user_id=$1",
            charge.attribution.user_id,
        )
        await db.execute_raw(
            "UPDATE deltallm_teamtable SET reserved_spend_exact=0 WHERE team_id=$1",
            charge.attribution.team_id,
        )
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET reserved_spend_exact=0 WHERE organization_id=$1",
            charge.attribution.organization_id,
        )
        await db.execute_raw(
            "UPDATE deltallm_teammodelspend SET reserved_spend_exact=0 WHERE team_id=$1",
            charge.attribution.team_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_billing_operations WHERE api_key=$1", charge.attribution.api_key
        )
        await db.execute_raw(
            "UPDATE deltallm_telemetry_ingestion_capacity SET pending_count="
            "(SELECT count(*) FROM deltallm_billing_operations WHERE closed_at IS NULL) WHERE queue_name='billing_operations'"
        )


def deadline():
    return asyncio.get_running_loop().time() + 2


async def hold(db, operation):
    rows = await db.query_raw(
        "SELECT reserved_spend_exact::text AS amount FROM deltallm_verificationtoken WHERE token=$1",
        operation.attribution.api_key,
    )
    return Decimal(rows[0]["amount"])


@pytest.mark.parametrize("scope", ["key", "user", "team", "org", "model"])
async def test_concurrent_whole_operation_reservations_cannot_bypass_budget(operation_db, scope):
    db, operation, _ = operation_db
    targets = {
        "key": ("deltallm_verificationtoken", "token"),
        "user": ("deltallm_usertable", "user_id"),
        "team": ("deltallm_teamtable", "team_id"),
        "org": ("deltallm_organizationtable", "organization_id"),
    }
    amount = str(operation.total_allowance * Decimal("1.5"))
    if scope == "model":
        await db.execute_raw(
            "UPDATE deltallm_teamtable SET model_max_budget=jsonb_build_object($3::text,$1::numeric) WHERE team_id=$2",
            amount,
            operation.attribution.team_id,
            operation.attribution.model_group,
        )
    else:
        table, column = targets[scope]
        await db.execute_raw(
            f"UPDATE {table} SET max_budget=$1::numeric::double precision WHERE {column}=$2",
            amount,
            operation.attribution.api_key,
        )
    repositories = [BillingOperationRepository(db), BillingOperationRepository(db)]

    async def reserve(index):
        candidate = operation.model_copy(
            update={
                "attribution": operation.attribution.model_copy(update={"operation_id": uuid4()}),
                "owner_token": uuid4(),
            }
        )
        try:
            await repositories[index % 2].reserve(candidate, expires_at=deadline())
            return True
        except BillingOperationUnavailable:
            return False

    results = await asyncio.gather(*(reserve(index) for index in range(8)))
    assert sum(results) == 1
    assert await hold(db, operation) == operation.total_allowance


async def test_receipt_recovery_settles_exactly_once_and_releases_unused_answer_allowance(
    operation_db,
):
    db, operation, charge = operation_db
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    await repository.reserve(operation, expires_at=deadline())
    await repository.dispatch(operation, component="selector", expires_at=deadline())
    await repository.accept_selector(operation, charge, expires_at=deadline())
    recovery = BillingOperationRecovery(repository, max_pending_events=100000, max_attempts=10)
    service = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
        operation_recovery=recovery,
    )
    records = await service._claim_batch()
    await service._process_batch(records)
    await repository.unattempted(operation, component="answer", expires_at=deadline())
    assert await hold(db, operation) == 0
    await repository.accept_selector(operation, charge, expires_at=deadline())
    assert await service._claim_batch() == []
    rows = await db.query_raw(
        "SELECT spend_exact::text AS amount FROM deltallm_verificationtoken WHERE token=$1",
        operation.attribution.api_key,
    )
    assert Decimal(rows[0]["amount"]) == charge.customer_charge


async def test_process_loss_after_dispatch_retains_unknown_hold_and_forbids_replay(operation_db):
    db, operation, _ = operation_db
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    await repository.dispatch(operation, component="selector", expires_at=deadline())
    # Move both timestamps while preserving the database lifetime constraint.
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET created_at=NOW()-interval '10 minutes', "
        "expires_at=NOW()-interval '5 minutes' WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    recovery = BillingOperationRecovery(
        BillingOperationRepository(db), max_pending_events=100000, max_attempts=10
    )
    await recovery.recover()
    assert await hold(db, operation) == operation.selector.allowance
    with pytest.raises(BillingOperationUnavailable):
        await repository.dispatch(operation, component="selector", expires_at=deadline())
    rows = await db.query_raw(
        "SELECT selector_state,answer_state FROM deltallm_billing_operations WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    assert rows[0] == {"selector_state": "pending", "answer_state": "unattempted"}


async def test_changed_tenant_snapshot_cannot_reserve_or_dispatch(operation_db):
    db, operation, _ = operation_db
    repository = BillingOperationRepository(db)
    wrong = operation.model_copy(
        update={
            "attribution": operation.attribution.model_copy(update={"organization_id": "other-org"})
        }
    )
    with pytest.raises(BillingOperationUnavailable):
        await repository.reserve(wrong, expires_at=deadline())
    assert await hold(db, operation) == 0
    await repository.reserve(operation, expires_at=deadline())
    with pytest.raises(BillingOperationUnavailable):
        await repository.dispatch(
            operation.model_copy(update={"owner_token": uuid4()}),
            component="selector",
            expires_at=deadline(),
        )


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_reservation_rollback_does_not_leak_hold_or_capacity(
    operation_db, monkeypatch, error
):
    db, operation, _ = operation_db
    repository = BillingOperationRepository(db)
    insert = repository._insert

    async def fail_after_insert(tx, candidate):
        await insert(tx, candidate)
        raise error()

    monkeypatch.setattr(repository, "_insert", fail_after_insert)
    expected = (
        asyncio.CancelledError if error is asyncio.CancelledError else BillingOperationUnavailable
    )
    with pytest.raises(expected):
        await repository.reserve(operation, expires_at=deadline())
    assert await hold(db, operation) == 0
    assert not await db.query_raw(
        "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    monkeypatch.setattr(repository, "_insert", insert)
    await repository.reserve(operation, expires_at=deadline())
    assert await hold(db, operation) == operation.total_allowance


async def test_pending_answer_can_settle_from_a_late_direct_writer_receipt(operation_db):
    db, operation, charge = operation_db
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    await repository.dispatch(operation, component="selector", expires_at=deadline())
    await repository.dispatch(operation, component="answer", expires_at=deadline())
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET created_at=NOW()-interval '10 minutes', "
        "expires_at=NOW()-interval '5 minutes' WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    recovery = BillingOperationRecovery(repository, max_pending_events=100000, max_attempts=10)
    await recovery.recover()
    payload = charge.spend_payload()
    payload["call_type"] = "chat_completion"
    async with db.tx() as tx:
        await SpendTrackingService(tx).log_batch_once(
            [(str(operation.attribution.operation_id), "spend", payload)]
        )
    await recovery.recover()
    assert await hold(db, operation) == operation.selector.allowance
    rows = await db.query_raw(
        "SELECT selector_state,answer_state FROM deltallm_billing_operations WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    assert rows[0] == {"selector_state": "pending", "answer_state": "settled"}


async def test_physical_deletion_cannot_orphan_outstanding_economic_holds(operation_db):
    from prisma.errors import RawQueryError

    db, operation, _ = operation_db
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    with pytest.raises(RawQueryError, match="requires_reconciliation"):
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE token=$1", operation.attribution.api_key
        )
    assert await hold(db, operation) == operation.total_allowance
    await repository.unattempted(operation, component="answer", expires_at=deadline())
    await repository.unattempted(operation, component="selector", expires_at=deadline())
    assert await hold(db, operation) == 0


async def test_changed_receipt_cannot_replace_the_accepted_charge(operation_db):
    db, operation, charge = operation_db
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    await repository.dispatch(operation, component="selector", expires_at=deadline())
    await repository.accept_selector(operation, charge, expires_at=deadline())
    changed = charge.model_copy(update={"finished_at": charge.finished_at + timedelta(seconds=1)})
    with pytest.raises(BillingOperationUnavailable):
        await repository.accept_selector(operation, changed, expires_at=deadline())
    mismatched = operation.model_copy(
        update={
            "attribution": operation.attribution.model_copy(update={"model_group": "another-group"})
        }
    )
    with pytest.raises(BillingOperationUnavailable):
        await repository.accept_selector(
            mismatched,
            charge.model_copy(update={"attribution": mismatched.attribution}),
            expires_at=deadline(),
        )


async def test_unknown_answer_failure_is_not_reconciled_as_zero(operation_db):
    db, operation, _ = operation_db
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    await repository.dispatch(operation, component="answer", expires_at=deadline())
    await repository.unattempted(operation, component="selector", expires_at=deadline())
    await SpendTrackingService(db).log_request_failure_once(
        event_id=str(operation.attribution.operation_id),
        request_id=str(operation.attribution.operation_id),
        api_key=operation.attribution.api_key,
        user_id=operation.attribution.user_id,
        team_id=operation.attribution.team_id,
        organization_id=operation.attribution.organization_id,
        end_user_id=None,
        model=operation.attribution.model_group,
        call_type="chat_completion",
        http_status_code=500,
        error_type="unknown_provider_usage",
    )
    await BillingOperationRecovery(repository, max_pending_events=100000, max_attempts=10).recover()
    assert await hold(db, operation) == operation.answer.allowance


async def test_recovery_query_uses_bounded_open_row_index_amid_terminal_history(operation_db):
    import json

    db, operation, _ = operation_db
    await BillingOperationRepository(db).reserve(operation, expires_at=deadline())
    await db.execute_raw(
        "INSERT INTO deltallm_billing_operations "
        "(operation_id,owner_token,api_key,model,snapshot,selector_event_id,"
        "selector_allowance,answer_allowance,selector_state,answer_state,closed_at,expires_at) "
        "SELECT $1||n::text,'test-owner',$2,'group','{}'::jsonb,$1||'event'||n::text,"
        "0,0,'unattempted','unattempted',NOW(),NOW()+interval '5 minutes' "
        "FROM generate_series(1,5000) n",
        str(operation.owner_token),
        operation.attribution.api_key,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_billing_operations "
        "(operation_id,owner_token,api_key,model,snapshot,selector_event_id,"
        "selector_allowance,answer_allowance,recovery_blocked_at,recovery_error_code,expires_at) "
        "SELECT $1||'blocked'||n::text,'test-owner',$2,'group','{}'::jsonb,$1||'blocked-event'||n::text,"
        "0,0,NOW(),'receipt_conflict',NOW()+interval '5 minutes' FROM generate_series(1,5000) n",
        str(operation.owner_token),
        operation.attribution.api_key,
    )
    await db.execute_raw("ANALYZE deltallm_billing_operations")
    rows = await db.query_raw(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT operation_id "
        "FROM deltallm_billing_operations WHERE closed_at IS NULL AND recovery_blocked_at IS NULL "
        "ORDER BY updated_at,operation_id FOR UPDATE SKIP LOCKED LIMIT 1"
    )
    plan = json.dumps(rows)
    assert "deltallm_billing_operations_recovery_idx" in plan
    assert "Seq Scan" not in plan
