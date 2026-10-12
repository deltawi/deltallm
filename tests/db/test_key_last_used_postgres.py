from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.db.identity.key_last_used import KeyLastUsedRepository

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
async def key_database() -> AsyncIterator[tuple[Prisma, str]]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    prefix = "last-used-" + uuid4().hex
    await db.execute_raw(
        """
        INSERT INTO deltallm_verificationtoken(id, token, models, updated_at)
        SELECT $1 || '-' || n, $1 || '-' || n, ARRAY[]::text[], TIMESTAMP '2026-10-01'
        FROM generate_series(1, 3) n
        """,
        prefix,
    )
    try:
        yield db, prefix
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE token LIKE $1", prefix + "-%"
        )
        await db.disconnect()


async def _last_used(db: Prisma, prefix: str) -> dict[str, datetime | None]:
    rows = await db.query_raw(
        """
        SELECT vt.token, lu.last_used_at, vt.updated_at
        FROM deltallm_verificationtoken vt
        LEFT JOIN deltallm_verificationtoken_last_used lu ON lu.token = vt.token
        WHERE vt.token LIKE $1
        """,
        prefix + "-%",
    )
    for row in rows:
        # Usage tracking is not a key edit.
        assert str(row["updated_at"]).startswith("2026-10-01")
    return {row["token"].removeprefix(prefix + "-"): row["last_used_at"] for row in rows}


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@pytest.mark.asyncio
async def test_write_sets_last_used_and_never_moves_it_back(
    key_database: tuple[Prisma, str],
) -> None:
    db, prefix = key_database
    repository = KeyLastUsedRepository(db)

    updated = await repository.write(
        {f"{prefix}-1": T0, f"{prefix}-2": T0, f"{prefix}-missing": T0}
    )
    stale = await repository.write({f"{prefix}-1": T0 - timedelta(minutes=5)})
    newer = await repository.write({f"{prefix}-2": T0 + timedelta(minutes=5)})

    values = await _last_used(db, prefix)
    assert (updated, stale, newer) == (2, 0, 1)
    assert _as_datetime(values["1"]) == T0
    assert _as_datetime(values["2"]) == T0 + timedelta(minutes=5)
    assert values["3"] is None


@pytest.mark.asyncio
async def test_write_skips_a_locked_key_instead_of_waiting(
    key_database: tuple[Prisma, str],
) -> None:
    db, prefix = key_database
    url = os.environ["DATABASE_URL"]
    writer_db = Prisma(datasource={"url": url})
    await writer_db.connect()
    try:
        async with db.tx(timeout=timedelta(seconds=30)) as tx:
            await tx.query_raw(
                "SELECT token FROM deltallm_verificationtoken WHERE token = $1 FOR UPDATE",
                f"{prefix}-1",
            )
            updated = await KeyLastUsedRepository(writer_db).write(
                {f"{prefix}-1": T0, f"{prefix}-2": T0}
            )
    finally:
        await writer_db.disconnect()

    values = await _last_used(db, prefix)
    assert updated == 1
    assert values["1"] is None
    assert _as_datetime(values["2"]) == T0


@pytest.mark.asyncio
async def test_last_used_follows_regeneration_and_deletion(
    key_database: tuple[Prisma, str],
) -> None:
    db, prefix = key_database
    await KeyLastUsedRepository(db).write({f"{prefix}-1": T0, f"{prefix}-2": T0})

    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET token = $1 WHERE token = $2",
        f"{prefix}-new",
        f"{prefix}-1",
    )
    await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", f"{prefix}-2")

    rows = await db.query_raw(
        "SELECT token, last_used_at FROM deltallm_verificationtoken_last_used WHERE token LIKE $1",
        prefix + "-%",
    )
    assert [(row["token"], _as_datetime(row["last_used_at"])) for row in rows] == [
        (f"{prefix}-new", T0)
    ]
