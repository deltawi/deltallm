from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from prisma import Prisma
import pytest

pytestmark = pytest.mark.postgres
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "prisma/migrations/20261019120000_key_credential_history/migration.sql"
)


async def test_upgrade_retains_active_and_recoverable_retired_hashes():
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is required")
    schema = "key_upgrade_" + uuid4().hex
    db = Prisma(datasource={"url": url})
    await db.connect()
    hashes = [character * 64 for character in "abcdef"]
    try:
        await db.execute_raw(f'CREATE SCHEMA "{schema}"')
        definitions = {
            "deltallm_verificationtoken": "token text primary key",
            "deltallm_auditevent": "resource_type text, resource_id text",
            "deltallm_spendlog_events": "api_key text",
            "deltallm_accounting_usage_facts_v2": "api_key text",
            "deltallm_cacheinvalidationoutbox": "scope_type text, scope_id text",
            "deltallm_audit_ingestion_outbox": "payload_json jsonb",
        }
        for table, columns in definitions.items():
            await db.execute_raw(f'CREATE TABLE "{schema}"."{table}" ({columns})')
        await db.execute_raw(
            f'INSERT INTO "{schema}".deltallm_verificationtoken VALUES ($1)', hashes[0]
        )
        await db.execute_raw(
            f"INSERT INTO \"{schema}\".deltallm_auditevent VALUES ('api_key', $1)", hashes[1]
        )
        await db.execute_raw(
            f'INSERT INTO "{schema}".deltallm_spendlog_events VALUES ($1)', hashes[2]
        )
        await db.execute_raw(
            f'INSERT INTO "{schema}".deltallm_accounting_usage_facts_v2 VALUES ($1)', hashes[3]
        )
        await db.execute_raw(
            f"INSERT INTO \"{schema}\".deltallm_cacheinvalidationoutbox VALUES ('key_hash', $1)",
            hashes[4],
        )
        await db.execute_raw(
            f'''INSERT INTO "{schema}".deltallm_audit_ingestion_outbox VALUES
            (jsonb_build_object('event', jsonb_build_object('resource_type', 'api_key', 'resource_id', $1::text)))''',
            hashes[5],
        )
        for _ in range(2):
            # A retry must keep all reservations after a partially completed upgrade.
            await asyncio.to_thread(
                subprocess.run,
                ["psql", url, "-v", "ON_ERROR_STOP=1", "-q"],
                input=MIGRATION.read_text(),
                text=True,
                capture_output=True,
                env={**os.environ, "PGOPTIONS": f"-c search_path={schema},public"},
                check=True,
                timeout=60,
            )
        rows = await db.query_raw(
            f'SELECT token_hash FROM "{schema}".deltallm_keycredentialhistory ORDER BY token_hash'
        )
        assert [row["token_hash"] for row in rows] == hashes
        await db.execute_raw(f'UPDATE "{schema}".deltallm_verificationtoken SET token = token')
    finally:
        await db.execute_raw(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await db.disconnect()
