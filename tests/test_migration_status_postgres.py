import os
from datetime import timedelta
from uuid import uuid4

from prisma import Prisma
import pytest

from src.db.migration_status import MigrationVerificationError, verify_migration_status

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class RollbackFixture(Exception):
    pass


async def test_real_history_is_read_only_and_unfinished_migration_prevents_startup():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide migrated test PostgreSQL")
        pytest.skip("migrated test PostgreSQL required")
    db = Prisma(datasource={"url": url})
    try:
        await db.connect(timeout=timedelta(seconds=10))
        async with db.tx() as tx:
            await tx.execute_raw("SET TRANSACTION READ ONLY")
            await verify_migration_status(tx, timeout_seconds=2)
        # The injected failed record is transaction-local and always rolled back.
        with pytest.raises(RollbackFixture):
            async with db.tx() as tx:
                await tx.execute_raw(
                    'INSERT INTO "_prisma_migrations" (id,checksum,migration_name,started_at) '
                    "VALUES ($1,$2,$3,NOW())",
                    str(uuid4()),
                    "0" * 64,
                    "pr8_unfinished_fixture",
                )
                with pytest.raises(MigrationVerificationError, match="unfinished"):
                    await verify_migration_status(tx, timeout_seconds=2)
                raise RollbackFixture()
        await verify_migration_status(db, timeout_seconds=2)
    finally:
        await db.disconnect(timeout=timedelta(seconds=5))
