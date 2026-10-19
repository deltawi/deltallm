from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import os
from uuid import uuid4

from prisma import Prisma
from prisma.errors import RawQueryError
import pytest

from src.db.identity.key_creation import (
    KeyCreateRecord,
    KeyCreationRepository,
    KeyCredentialConflict,
)
from src.db.identity.key_repository import KeyRepository
from src.services.identity.keys.key_material import prepare_key_material
from src.services.identity.keys.key_service import KeyService

pytestmark = pytest.mark.postgres


@pytest.fixture
async def database() -> AsyncIterator[tuple[Prisma, Prisma, str]]:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is required")
    db, peer = Prisma(datasource={"url": url}), Prisma(datasource={"url": url})
    prefix = "credential-test-" + uuid4().hex
    await db.connect()
    await peer.connect()
    try:
        yield db, peer, prefix
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE token LIKE $1", prefix + "%"
        )
        await db.execute_raw(
            "DELETE FROM deltallm_keycredentialhistory WHERE token_hash LIKE $1", prefix + "%"
        )
        await peer.disconnect()
        await db.disconnect()


async def insert(db, token):
    await db.execute_raw(
        "INSERT INTO deltallm_verificationtoken (id, token, models, updated_at) VALUES (gen_random_uuid(), $1, ARRAY[]::text[], NOW())",
        token,
    )


async def test_concurrent_writers_have_one_winner(database):
    db, peer, token = database
    results = await asyncio.gather(insert(db, token), insert(peer, token), return_exceptions=True)
    assert sum(result is None for result in results) == 1
    failures = [result for result in results if isinstance(result, RawQueryError)]
    assert len(failures) == 1 and failures[0].meta["code"] == "PKEY1"
    assert await db.query_raw(
        "SELECT token_hash FROM deltallm_keycredentialhistory WHERE token_hash = $1", token
    ) == [{"token_hash": token}]


async def test_deleted_and_rotated_values_cannot_be_issued_again(database):
    db, _, token = database
    await insert(db, token)
    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET token = $1 WHERE token = $2", token + "-new", token
    )
    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET token = token WHERE token = $1", token + "-new"
    )
    await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", token + "-new")
    for retired in (token, token + "-new"):
        with pytest.raises(RawQueryError) as error:
            await insert(db, retired)
        assert error.value.meta["code"] == "PKEY1"


async def test_failed_transaction_releases_the_reservation(database):
    db, peer, token = database
    with pytest.raises(RuntimeError):
        async with db.tx() as tx:
            await insert(tx, token)
            raise RuntimeError("rollback")
    await insert(peer, token)
    assert await db.query_raw(
        "SELECT token FROM deltallm_verificationtoken WHERE token = $1", token
    )


async def test_supplied_secret_repository_authentication_and_conflict_mapping(database):
    db, _, prefix = database
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, models, updated_at) VALUES ($1, ARRAY[]::text[], NOW())",
        prefix,
    )
    keys = KeyService(KeyRepository(db), salt="credential-test-salt")
    raw_key = "sk-" + prefix
    material = prepare_key_material(raw_key, hasher=keys, master_key=None)
    record = KeyCreateRecord(
        token_hash=material.token_hash,
        key_name="Imported",
        user_id=None,
        team_id=prefix,
        owner_account_id=None,
        owner_service_account_id=None,
        max_budget=5,
        rpm_limit=10,
        tpm_limit=None,
        rph_limit=None,
        rpd_limit=None,
        tpd_limit=None,
        expires=None,
        budget_duration=None,
        budget_reset_at=None,
        metadata_json=None,
    )
    try:
        async with db.tx() as tx:
            await KeyCreationRepository(tx).create(record)
        assert (await keys.validate_key(raw_key)).team_id == prefix
        with pytest.raises(KeyCredentialConflict):
            async with db.tx() as tx:
                await KeyCreationRepository(tx).create(record)
        rows = await db.query_raw(
            "SELECT token_hash FROM deltallm_keycredentialhistory WHERE token_hash = $1",
            material.token_hash,
        )
        assert rows == [{"token_hash": material.token_hash}]
        assert raw_key not in str(rows)
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE token = $1", material.token_hash
        )
        await db.execute_raw(
            "DELETE FROM deltallm_keycredentialhistory WHERE token_hash = $1", material.token_hash
        )
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", prefix)
        await keys.close()
