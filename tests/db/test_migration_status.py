import asyncio
from hashlib import sha256
from unittest.mock import AsyncMock

import pytest

from src.db.migration_status import (
    MigrationVerificationError,
    required_migrations,
    verify_history,
    verify_migration_status,
)


def row(name="001_init", checksum="abc", *, finished=True, rolled_back=False):
    return dict(migration_name=name, checksum=checksum, finished=finished, rolled_back=rolled_back)


def test_history_accepts_completed_required_migrations_and_compatible_superset():
    verify_history({"001_init": "abc"}, [row(), row("002_additive")])
    verify_history({"001_init": "abc"}, [row(finished=False, rolled_back=True), row()])


@pytest.mark.parametrize(
    "rows,reason",
    [
        ([], "have not completed"),
        ([row(checksum="different")], "checksum"),
        ([row(finished=False)], "unfinished"),
        ([row(), row("002_new", finished=False)], "unfinished"),
        ([row(finished=False, rolled_back=True)], "have not completed"),
        ([row()] * 4097, "bound"),
    ],
)
def test_history_rejects_incomplete_changed_and_unbounded_history(rows, reason):
    with pytest.raises(MigrationVerificationError, match=reason):
        verify_history({"001_init": "abc"}, rows)


async def test_verification_uses_only_bounded_read_only_history_query(tmp_path):
    migration = tmp_path / "001_init"
    migration.mkdir()
    content = b"CREATE TABLE example (id integer);\n"
    (migration / "migration.sql").write_bytes(content)
    assert required_migrations(tmp_path) == {"001_init": sha256(content).hexdigest()}
    client = AsyncMock()
    client.query_raw.return_value = [row(checksum=sha256(content).hexdigest())]
    await verify_migration_status(client, timeout_seconds=1, directory=tmp_path)
    query = client.query_raw.await_args.args[0]
    assert query.startswith("SELECT")
    assert "LIMIT 4097" in query
    assert "logs" not in query


async def test_unavailable_and_slow_history_do_not_expose_credentials():
    client = AsyncMock()
    client.query_raw.side_effect = RuntimeError("postgresql://private:secret@db")
    with pytest.raises(MigrationVerificationError, match="unavailable") as failure:
        await verify_migration_status(client, timeout_seconds=1)
    assert "secret" not in str(failure.value)

    async def blocked(*_):
        await asyncio.Event().wait()

    client.query_raw.side_effect = blocked
    with pytest.raises(MigrationVerificationError, match="timed out"):
        await verify_migration_status(client, timeout_seconds=0.01)
