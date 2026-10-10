"""Recover missing reporting IDs without changing events or financial effects."""

from decimal import Decimal
import json
from pathlib import Path

import pytest

from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_protocol_postgres import _window, accounting_db as _accounting_db
from tests.test_accounting_read_model_postgres import effects, next_page, repository, source
from tests.test_accounting_local_leases_postgres import deadline

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def test_blocked_old_projection_recovers_after_the_forward_migration(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    _, _values = await source(db, generation, count=1)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    await db.execute_raw(
        "UPDATE deltallm_accounting_events SET payload_json=jsonb_set(payload_json,'{spend,request_id}','\"\"'::jsonb) WHERE sequence=$1",
        page.sequences[0],
    )
    migrations = Path("prisma/migrations")
    historical = (
        migrations / "20261006040000_accounting_native_projection_commit/migration.sql"
    ).read_text()
    old_function = (
        "CREATE OR REPLACE FUNCTION deltallm_accounting_usage_row("
        + historical.split("CREATE FUNCTION deltallm_accounting_usage_row(", 1)[1].split(
            "CREATE FUNCTION deltallm_accounting_audit_row", 1
        )[0]
    )
    correction = (
        migrations / "20261010130000_accounting_reporting_request_ids/migration.sql"
    ).read_text()
    corrected_function = (
        "CREATE OR REPLACE FUNCTION"
        + correction.split("CREATE OR REPLACE FUNCTION", 1)[1].rsplit("COMMIT;", 1)[0]
    )
    try:
        await db.execute_raw(old_function)
        with pytest.raises(AccountingProtocolUnavailable):
            await repo.materialize(page, expires_at=deadline())
        assert await effects(db, generation) == {"facts": 0, "audits": 0, "rolled": 0}
    finally:
        # Restore the current function even if the old failure assertion fails.
        await db.execute_raw(corrected_function)
    assert await repo.materialize(page, expires_at=deadline()) == 1
    assert await repo.materialize(page, expires_at=deadline()) == 0
    assert await effects(db, generation) == {"facts": 1, "audits": 1, "rolled": 1}
    event = await db.query_raw(
        "SELECT payload_json#>>'{spend,request_id}' AS request_id FROM deltallm_accounting_events WHERE sequence=$1",
        page.sequences[0],
    )
    assert event == [{"request_id": ""}]


@pytest.mark.parametrize("request_id", ["missing", None, ""])
async def test_accepted_missing_id_projects_once_without_rewriting_source(
    accounting_db, request_id
):
    clients, generation = accounting_db
    db = clients[0]
    window, _ = await source(db, generation, count=1)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    if request_id == "missing":
        await db.execute_raw(
            "UPDATE deltallm_accounting_events SET payload_json=payload_json #- '{spend,request_id}' WHERE sequence=$1",
            page.sequences[0],
        )
    else:
        await db.execute_raw(
            "UPDATE deltallm_accounting_events SET payload_json=jsonb_set(payload_json,'{spend,request_id}',$2::jsonb) WHERE sequence=$1",
            page.sequences[0],
            json.dumps(request_id),
        )
    before = await db.query_raw(
        "SELECT to_jsonb(e) AS event FROM deltallm_accounting_events e WHERE sequence=$1",
        page.sequences[0],
    )
    money_before = await _window(db, window)
    assert await repo.materialize(page, expires_at=deadline()) == 1
    assert await repo.materialize(page, expires_at=deadline()) == 0
    assert await next_page(repo, generation) is None
    assert await effects(db, generation) == {"facts": 1, "audits": 1, "rolled": 1}
    assert await _window(db, window) == money_before
    after = await db.query_raw(
        "SELECT to_jsonb(e) AS event FROM deltallm_accounting_events e WHERE sequence=$1",
        page.sequences[0],
    )
    assert after == before
    fact = await db.query_raw(
        "SELECT f.request_id,f.operation_id::text AS operation_id,f.spend_exact::text AS charge,"
        "f.source_sha256=sha256(convert_to((to_jsonb(e)-'created_at')::text||(b.snapshot->'attribution')::text,'UTF8')) AS source_matches "
        "FROM deltallm_accounting_usage_facts_v2 f JOIN deltallm_accounting_events e ON e.sequence=f.accounting_sequence "
        "JOIN deltallm_billing_operations b ON b.operation_id=e.operation_id WHERE f.accounting_sequence=$1",
        page.sequences[0],
    )
    assert fact[0]["request_id"] == fact[0]["operation_id"]
    assert Decimal(fact[0]["charge"]) == Decimal("0.6")
    assert fact[0]["source_matches"] is True


@pytest.mark.parametrize("request_id", [42, {}, [], "x" * 257])
async def test_other_invalid_reporting_ids_still_fail_closed(accounting_db, request_id):
    clients, generation = accounting_db
    db = clients[0]
    await source(db, generation, count=1)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    await db.execute_raw(
        "UPDATE deltallm_accounting_events SET payload_json=jsonb_set(payload_json,'{spend,request_id}',$2::jsonb) WHERE sequence=$1",
        page.sequences[0],
        json.dumps(request_id),
    )
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.materialize(page, expires_at=deadline())
    assert await effects(db, generation) == {"facts": 0, "audits": 0, "rolled": 0}
