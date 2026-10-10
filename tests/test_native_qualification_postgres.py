"""Qualification SQL executes against the migrated test schema, not mocks."""

import asyncio
from uuid import uuid4
from prisma import Prisma
import pytest

from tests.performance.native_qualification_economics import (
    accounting_snapshot,
    native_storage_snapshot,
)
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _repository,
    _reservation,
)
from src.billing.accounting_protocol import AccountingOutcome
from tests.test_accounting_local_leases_postgres import funded
from tests.performance.native_qualification_failures import (
    _UNSETTLED_SQL,
    capture_unsettled_operations,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def test_qualification_state_and_storage_queries_use_real_schema(accounting_db):
    clients, _ = accounting_db
    state = await accounting_snapshot(clients[0])
    assert set(state) == {
        "unsettled_operations",
        "open_grants",
        "terminal_pending",
        "report_pending_partitions",
        "spend_pending",
        "audit_pending",
        "unsafe_windows",
        "facts",
        "fact_charge",
        "legacy_spend_rows",
    }
    assert isinstance(state["fact_charge"], str)
    storage = await native_storage_snapshot(clients[0])
    assert 1 <= len(storage) <= 64
    assert all(row["table_bytes"] >= 0 and row["index_bytes"] >= 0 for row in storage)


@pytest.mark.parametrize(
    "reason,classification",
    [
        ("private tenant provider details", "unknown"),
        ("service_unavailable", "service_unavailable"),
    ],
)
async def test_unsettled_capture_reads_exact_money_without_changing_it(
    accounting_db, monkeypatch, reason, classification
):
    clients, generation = accounting_db
    db, window = clients[0], str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    repository = _repository(db)
    await repository.reserve_batch([item], expires_at=asyncio.get_running_loop().time() + 2)
    terminal = _finalization(item, AccountingOutcome.UNCERTAIN).model_copy(
        update={"uncertainty_reason": reason}
    )
    await repository.finalize_batch([terminal], expires_at=asyncio.get_running_loop().time() + 2)
    query = (
        "SELECT operation_id,accounting_state,provisional_debit_exact::text AS held "
        "FROM deltallm_billing_operations WHERE accounting_generation=$1"
    )
    before = await db.query_raw(query, generation)
    original, read_only = Prisma.query_raw, []

    async def checked_read(client, sql, *parameters, **options):
        if sql == _UNSETTLED_SQL:
            read_only.append(
                await original(client, "SELECT current_setting('transaction_read_only') AS value")
            )
        return await original(client, sql, *parameters, **options)

    monkeypatch.setattr(Prisma, "query_raw", checked_read)
    result = await capture_unsettled_operations(db, generation=generation)
    assert result["available"] and not result["truncated"]
    assert read_only == [[{"value": "on"}]]
    assert result["operations"] == [
        {
            "operation_id": str(item.operation_id),
            "accounting_state": "provisional",
            "max_scope_reserved_exact": "0.000000000000000000",
            "max_scope_provisional_exact": "1.000000000000000000",
            "journal_outcome": None,
            "journal_status": None,
            "uncertainty_class": classification,
        }
    ]
    assert await db.query_raw(query, generation) == before
    assert "private" not in str(result)


async def test_real_unsettled_capture_does_not_export_more_than_64_rows(accounting_db):
    clients, generation = accounting_db
    db, window = clients[0], str(uuid4())
    await _create_window(db, generation, window, limit="1000")
    items = [_reservation(generation, window) for _ in range(65)]
    await _repository(db, target_operations=65).reserve_batch(
        items, expires_at=asyncio.get_running_loop().time() + 5
    )
    result = await capture_unsettled_operations(db, generation=generation)
    assert result["available"] and result["truncated"]
    assert len(result["operations"]) == 64
    assert {row["operation_id"] for row in result["operations"]} <= {
        str(item.operation_id) for item in items
    }


async def test_open_local_grants_are_captured_when_no_operation_exists(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    _, _, grant = await funded(db, generation)
    before = await db.query_raw(
        "SELECT allocated_exact::text,consumed_exact::text,returned_exact::text "
        "FROM deltallm_accounting_grants WHERE grant_id=$1",
        grant.grant_id,
    )
    result = await capture_unsettled_operations(db, generation=generation)
    assert result["available"] and result["operations"] == []
    assert not result["grants_truncated"] and len(result["open_local_grants"]) == 1
    captured = result["open_local_grants"][0]
    assert captured["grant_id"] == grant.grant_id
    assert captured["allocated_exact"] == "4.000000000000000000"
    assert captured["consumed_operations"] == captured["returned_operations"] == 0
    assert captured["state"] == "active"
    assert captured["recovery_seconds_remaining"] > 0
    assert (
        await db.query_raw(
            "SELECT allocated_exact::text,consumed_exact::text,returned_exact::text "
            "FROM deltallm_accounting_grants WHERE grant_id=$1",
            grant.grant_id,
        )
        == before
    )
