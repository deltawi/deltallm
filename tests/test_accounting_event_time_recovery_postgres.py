"""Report completed events that have no start or end time, without changing them."""

from datetime import datetime
import json
from pathlib import Path

import pytest

from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_protocol_postgres import _window, accounting_db as _accounting_db
from tests.test_accounting_read_model_postgres import effects, next_page, repository, source
from tests.test_accounting_local_leases_postgres import deadline

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db

_MIGRATIONS = Path("prisma/migrations")


def _usage_row_function(name: str) -> str:
    text = (_MIGRATIONS / name / "migration.sql").read_text()
    return (
        "CREATE OR REPLACE FUNCTION"
        + text.split("CREATE OR REPLACE FUNCTION", 1)[1].rsplit("COMMIT;", 1)[0]
    )


async def _remove_times(db, sequence: int, change: str) -> None:
    # The paths are a fixed test allowlist.
    if change == "both_missing":
        query = "payload_json #- '{spend,start_time}' #- '{spend,end_time}'"
    elif change == "start_missing":
        query = "payload_json #- '{spend,start_time}'"
    elif change == "end_missing":
        query = "payload_json #- '{spend,end_time}'"
    else:
        assert change == "both_null"
        query = (
            "jsonb_set(jsonb_set(payload_json,'{spend,start_time}','null'::jsonb),"
            "'{spend,end_time}','null'::jsonb)"
        )
    await db.execute_raw(
        f"UPDATE deltallm_accounting_events SET payload_json={query} WHERE sequence=$1",
        sequence,
    )


async def _event(db, sequence: int):
    rows = await db.query_raw(
        "SELECT to_jsonb(e) AS event FROM deltallm_accounting_events e WHERE sequence=$1",
        sequence,
    )
    return rows[0]["event"]


async def test_blocked_cache_hit_page_recovers_after_the_forward_migration(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await source(db, generation, count=1)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    await _remove_times(db, page.sequences[0], "both_missing")
    try:
        await db.execute_raw(_usage_row_function("20261010130000_accounting_reporting_request_ids"))
        with pytest.raises(AccountingProtocolUnavailable) as raised:
            await repo.materialize(page, expires_at=deadline())
        assert raised.value.rejection == "accounting_read_model_attribution"
        assert await effects(db, generation) == {"facts": 0, "audits": 0, "rolled": 0}
    finally:
        # Restore the current function even if the old failure assertion fails.
        await db.execute_raw(_usage_row_function("20261103100000_accounting_reporting_event_times"))
    assert await repo.materialize(page, expires_at=deadline()) == 1
    assert await repo.materialize(page, expires_at=deadline()) == 0
    assert await effects(db, generation) == {"facts": 1, "audits": 1, "rolled": 1}


@pytest.mark.parametrize("change", ["both_missing", "both_null", "start_missing", "end_missing"])
async def test_missing_time_projects_once_without_rewriting_source(accounting_db, change):
    clients, generation = accounting_db
    db = clients[0]
    window, _ = await source(db, generation, count=1)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    sequence = page.sequences[0]
    original = await _event(db, sequence)
    await _remove_times(db, sequence, change)
    before = await _event(db, sequence)
    money_before = await _window(db, window)

    assert await repo.materialize(page, expires_at=deadline()) == 1
    assert await repo.materialize(page, expires_at=deadline()) == 0
    assert await next_page(repo, generation) is None
    assert await effects(db, generation) == {"facts": 1, "audits": 1, "rolled": 1}
    assert await _window(db, window) == money_before
    assert await _event(db, sequence) == before

    fact = (
        await db.query_raw(
            "SELECT start_time,end_time,latency_ms FROM deltallm_accounting_usage_facts_v2 "
            "WHERE accounting_sequence=$1",
            sequence,
        )
    )[0]
    occurred_at = datetime.fromisoformat(original["occurred_at"])
    original_start = datetime.fromisoformat(original["payload_json"]["spend"]["start_time"])
    original_end = datetime.fromisoformat(original["payload_json"]["spend"]["end_time"])
    start = datetime.fromisoformat(str(fact["start_time"]))
    end = datetime.fromisoformat(str(fact["end_time"]))
    expected = {
        "both_missing": (occurred_at, occurred_at),
        "both_null": (occurred_at, occurred_at),
        "start_missing": (original_end, original_end),
        "end_missing": (original_start, occurred_at),
    }[change]
    assert (start, end) == expected
    assert fact["latency_ms"] == max(0, int((end - start).total_seconds() * 1000))


@pytest.mark.parametrize("value", ["not-a-time", 42])
async def test_invalid_times_still_fail_closed(accounting_db, value):
    clients, generation = accounting_db
    db = clients[0]
    await source(db, generation, count=1)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    await db.execute_raw(
        "UPDATE deltallm_accounting_events SET payload_json=jsonb_set(payload_json,'{spend,start_time}',$2::jsonb) WHERE sequence=$1",
        page.sequences[0],
        json.dumps(value),
    )
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.materialize(page, expires_at=deadline())
    assert await effects(db, generation) == {"facts": 0, "audits": 0, "rolled": 0}
