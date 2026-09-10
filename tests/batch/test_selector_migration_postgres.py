from pathlib import Path
from datetime import timedelta
from uuid import uuid4

import pytest
from prisma.errors import RawQueryError

from tests import test_batch_db_integration as batch_fixtures

pytestmark = pytest.mark.postgres
batch_db = batch_fixtures.batch_db
MIGRATIONS = Path(__file__).resolve().parents[2] / "prisma" / "migrations"
INSTALL = "20260909000100_batch_selector_checkpoint"
VALIDATE = "20260910000100_batch_selector_checkpoint_validation"


async def apply(db, migration):
    # These two actual migrations contain only simple DDL and SET statements.
    for statement in (MIGRATIONS / migration / "migration.sql").read_text().split(";"):
        if statement.strip():
            await db.execute_raw(statement)


@pytest.fixture
async def checkpoint_table(batch_db):
    schema = "selector_migration_" + uuid4().hex
    await batch_db.execute_raw(f'CREATE SCHEMA "{schema}"')
    await batch_db.execute_raw(
        f'CREATE TABLE "{schema}".deltallm_batch_item (item_id int PRIMARY KEY)'
    )
    await batch_db.execute_raw(f'INSERT INTO "{schema}".deltallm_batch_item VALUES (1)')
    try:
        yield schema
    finally:
        await batch_db.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')


async def test_install_commits_before_validation_which_allows_concurrent_writers(
    batch_db, checkpoint_table
):
    schema = checkpoint_table
    async with batch_db.tx() as tx:
        await tx.execute_raw(f'SET LOCAL search_path TO "{schema}"')
        await apply(tx, INSTALL)
    (installed,) = await batch_db.query_raw(
        "SELECT convalidated FROM pg_constraint WHERE conrelid=$1::regclass AND conname=$2",
        f"{schema}.deltallm_batch_item",
        "deltallm_batch_selector_checkpoint_bound",
    )
    assert installed["convalidated"] is False
    with pytest.raises(RawQueryError):
        await batch_db.execute_raw(
            f"UPDATE \"{schema}\".deltallm_batch_item SET selector_checkpoint='{{}}'::jsonb"
        )
    async with batch_db.tx() as writer:
        await writer.execute_raw(f'UPDATE "{schema}".deltallm_batch_item SET item_id=1')
        async with batch_db.tx() as validator:
            await validator.execute_raw(f'SET LOCAL search_path TO "{schema}"')
            await apply(validator, VALIDATE)
    (validated,) = await batch_db.query_raw(
        "SELECT convalidated FROM pg_constraint WHERE conrelid=$1::regclass AND conname=$2",
        f"{schema}.deltallm_batch_item",
        "deltallm_batch_selector_checkpoint_bound",
    )
    assert validated["convalidated"] is True
    rows = await batch_db.query_raw(f'SELECT * FROM "{schema}".deltallm_batch_item')
    assert rows == [{"item_id": 1, "selector_checkpoint": None}]


async def test_conflicting_install_lock_times_out_without_partial_schema(
    batch_db, checkpoint_table
):
    schema = checkpoint_table
    async with batch_db.tx(timeout=timedelta(seconds=10)) as writer:
        await writer.execute_raw(f'UPDATE "{schema}".deltallm_batch_item SET item_id=1')
        with pytest.raises(RawQueryError, match="lock timeout"):
            async with batch_db.tx(timeout=timedelta(seconds=10)) as installer:
                await installer.execute_raw(f'SET LOCAL search_path TO "{schema}"')
                await apply(installer, INSTALL)
    rows = await batch_db.query_raw(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=$1 AND column_name=$2",
        schema,
        "selector_checkpoint",
    )
    assert rows == []
