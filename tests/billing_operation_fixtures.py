import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from src.db.billing_operation_recovery import BillingOperationRecovery
from src.db.billing_operations import BillingOperationRepository
from tests import test_billing_operations_postgres as operation_fixtures

selector_billing_db = operation_fixtures.selector_billing_db
operation_db = operation_fixtures.operation_db


@pytest.fixture
async def review_operation_db(operation_db):
    db, operation, charge = operation_db
    try:
        yield db, operation, charge
    finally:
        # Every test-created child shares this disposable, unique key. The underlying
        # fixtures clear its operation holds/rows and reconcile spend capacity next.
        await db.execute_raw(
            "DELETE FROM deltallm_spend_ingestion_outbox WHERE payload_json->>'api_key'=$1",
            operation.attribution.api_key,
        )


def another_operation(operation, charge):
    owner = operation.attribution.model_copy(update={"operation_id": uuid4()})
    return operation.model_copy(update={"attribution": owner}), charge.model_copy(
        update={"attribution": owner}
    )


def recovery(db):
    return BillingOperationRecovery(
        BillingOperationRepository(db), max_pending_events=100000, max_attempts=10
    )


async def expire(db, operation):
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET created_at=NOW()-interval '10 minutes', "
        "expires_at=NOW()-interval '5 minutes' WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )


async def operation_row(db, operation):
    rows = await db.query_raw(
        "SELECT * FROM deltallm_billing_operations WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    return rows[0]


async def capacity(db):
    rows = await db.query_raw(
        "SELECT pending_count FROM deltallm_telemetry_ingestion_capacity "
        "WHERE queue_name='billing_operations'"
    )
    return int(rows[0]["pending_count"])


async def reserved_totals(db, operation):
    rows = await db.query_raw(
        "SELECT reserved_spend_exact::text AS amount FROM deltallm_verificationtoken WHERE token=$1 "
        "UNION ALL SELECT reserved_spend_exact::text FROM deltallm_usertable WHERE user_id=$2 "
        "UNION ALL SELECT reserved_spend_exact::text FROM deltallm_teamtable WHERE team_id=$3 "
        "UNION ALL SELECT reserved_spend_exact::text FROM deltallm_organizationtable WHERE organization_id=$4 "
        "UNION ALL SELECT reserved_spend_exact::text FROM deltallm_teammodelspend WHERE team_id=$3 AND model=$5",
        operation.attribution.api_key,
        operation.attribution.user_id,
        operation.attribution.team_id,
        operation.attribution.organization_id,
        operation.attribution.model_group,
    )
    assert len(rows) == 5
    return [Decimal(row["amount"]) for row in rows]


async def wait_for_blocked_transaction(db, blocking_pid):
    async def observed():
        while True:
            rows = await db.query_raw(
                "SELECT 1 FROM pg_stat_activity WHERE $1::int=ANY(pg_blocking_pids(pid)) LIMIT 1",
                blocking_pid,
            )
            if rows:
                return
            await asyncio.sleep(0.001)

    await asyncio.wait_for(observed(), 2)
