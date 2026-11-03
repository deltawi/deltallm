"""Audit retention deletes expired rows when the id columns are uuid or text."""

from collections.abc import AsyncIterator
from datetime import timedelta
import os

import pytest
from prisma import Prisma

from src.db.audit.repository import AuditRepository

pytestmark = pytest.mark.postgres


class _RollbackFixture(Exception):
    pass


@pytest.fixture
async def audit_db() -> AsyncIterator[Prisma]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    try:
        async with db.tx(timeout=timedelta(seconds=30)) as tx:
            yield tx
            # Roll back all fixture rows, including when a test fails.
            raise _RollbackFixture()
    except _RollbackFixture:
        pass
    finally:
        await db.disconnect()


async def _shadow_audit_tables(db, id_type: str) -> None:
    # Temporary tables come first on the search path, so the repository uses
    # them. Databases created with `prisma db push` have text id columns.
    # The type is a fixed test value, never input.
    assert id_type in {"uuid", "text"}
    await db.execute_raw(
        f"""
        CREATE TEMP TABLE deltallm_auditevent (
            event_id {id_type} PRIMARY KEY,
            organization_id text,
            occurred_at timestamptz NOT NULL
        ) ON COMMIT DROP
        """
    )
    await db.execute_raw(
        f"""
        CREATE TEMP TABLE deltallm_auditpayload (
            payload_id {id_type} PRIMARY KEY,
            event_id {id_type} NOT NULL REFERENCES pg_temp.deltallm_auditevent (event_id)
                ON DELETE CASCADE,
            created_at timestamptz NOT NULL
        ) ON COMMIT DROP
        """
    )


async def _insert(db, id_type: str, event_age_days: int, payload_age_days: int) -> tuple[str, str]:
    rows = await db.query_raw(
        f"""
        WITH event AS (
            INSERT INTO deltallm_auditevent (event_id, occurred_at)
            VALUES (gen_random_uuid()::{id_type}, NOW() - make_interval(days => $1::int))
            RETURNING event_id
        )
        INSERT INTO deltallm_auditpayload (payload_id, event_id, created_at)
        SELECT gen_random_uuid()::{id_type}, event_id, NOW() - make_interval(days => $2::int)
        FROM event
        RETURNING payload_id::text AS payload_id, event_id::text AS event_id
        """,
        event_age_days,
        payload_age_days,
    )
    return rows[0]["event_id"], rows[0]["payload_id"]


async def _ids(db, table: str, column: str) -> set[str]:
    # Both names are a fixed test allowlist.
    assert (table, column) in {
        ("deltallm_auditevent", "event_id"),
        ("deltallm_auditpayload", "payload_id"),
    }
    rows = await db.query_raw(f"SELECT {column}::text AS id FROM {table}")
    return {row["id"] for row in rows}


@pytest.mark.parametrize("id_type", ["uuid", "text"])
async def test_retention_deletes_only_expired_rows_for_both_id_types(audit_db, id_type) -> None:
    await _shadow_audit_tables(audit_db, id_type)
    old_event, old_payload = await _insert(audit_db, id_type, 400, 400)
    kept_event, expired_payload = await _insert(audit_db, id_type, 100, 100)
    new_event, new_payload = await _insert(audit_db, id_type, 1, 1)
    repo = AuditRepository(audit_db)

    assert await repo.delete_expired_payloads(default_retention_days=90, limit=10) == 2
    assert await _ids(audit_db, "deltallm_auditpayload", "payload_id") == {new_payload}

    assert await repo.delete_expired_events(default_retention_days=365, limit=10) == 1
    assert await _ids(audit_db, "deltallm_auditevent", "event_id") == {kept_event, new_event}
    assert old_event not in await _ids(audit_db, "deltallm_auditevent", "event_id")
    assert old_payload != expired_payload


@pytest.mark.parametrize("id_type", ["uuid", "text"])
async def test_retention_limit_deletes_the_oldest_rows_first(audit_db, id_type) -> None:
    await _shadow_audit_tables(audit_db, id_type)
    oldest, _ = await _insert(audit_db, id_type, 500, 1)
    older, _ = await _insert(audit_db, id_type, 450, 1)
    newer, _ = await _insert(audit_db, id_type, 400, 1)
    repo = AuditRepository(audit_db)

    assert await repo.delete_expired_events(default_retention_days=365, limit=2) == 2
    assert await _ids(audit_db, "deltallm_auditevent", "event_id") == {newer}
    assert await repo.delete_expired_events(default_retention_days=365, limit=2) == 1
    assert await repo.delete_expired_events(default_retention_days=365, limit=2) == 0
    assert {oldest, older}.isdisjoint(await _ids(audit_db, "deltallm_auditevent", "event_id"))
