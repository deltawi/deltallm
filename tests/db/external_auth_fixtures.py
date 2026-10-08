from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import os
from uuid import uuid4

from prisma import Prisma
import pytest
from src.db.audit_ingestion import AuditIngestionRepository


@dataclass(frozen=True)
class ExternalDatabase:
    db: Prisma
    writer: Prisma
    integration_id: str
    binding_id: str
    organization_id: str
    team_id: str
    account_id: str
    identity_id: str
    subject_id: str
    issuer: str = "https://clerk.example.com"
    auth_time: int = field(default_factory=lambda: int(datetime.now(UTC).timestamp()) - 30)


@pytest.fixture
async def external_database() -> AsyncIterator[ExternalDatabase]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is required for PostgreSQL tests")
    suffix = uuid4().hex
    db = Prisma(datasource={"url": url})
    writer = Prisma(datasource={"url": url})
    fixture = ExternalDatabase(
        db,
        writer,
        f"external-{suffix}",
        f"binding-{suffix}",
        f"org-{suffix}",
        f"team-{suffix}",
        f"account-{suffix}",
        f"identity-{suffix}",
        f"subject-{suffix}",
    )
    await db.connect()
    try:
        await writer.connect()
        try:
            await _seed(fixture)
            yield fixture
        finally:
            try:
                await _clean(fixture)
            finally:
                await writer.disconnect()
    finally:
        await db.disconnect()


async def _seed(fixture: ExternalDatabase) -> None:
    db = fixture.db
    await db.execute_raw(
        "INSERT INTO deltallm_externalauthintegration (integration_id, updated_at) VALUES ($1, NOW())",
        fixture.integration_id,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable (id, organization_id, updated_at) VALUES ($1, $1, NOW())",
        fixture.organization_id,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, organization_id, models, updated_at) VALUES ($1, $2, ARRAY[]::text[], NOW())",
        fixture.team_id,
        fixture.organization_id,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_platformaccount (account_id, email, updated_at) VALUES ($1, $1 || '@example.com', NOW())",
        fixture.account_id,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_platformidentity (identity_id, account_id, provider, subject, updated_at) VALUES ($1, $2, $3, $4, NOW())",
        fixture.identity_id,
        fixture.account_id,
        "external:" + hashlib.sha256(fixture.issuer.encode()).hexdigest(),
        fixture.subject_id,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_usertable (user_id, team_id, models, updated_at) VALUES ($1, $2, ARRAY[]::text[], NOW())",
        fixture.account_id,
        fixture.team_id,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_externalauthbinding (binding_id, integration_id, external_customer_id, organization_id, team_id, updated_at) VALUES ($1, $2, $1, $3, $4, NOW())",
        fixture.binding_id,
        fixture.integration_id,
        fixture.organization_id,
        fixture.team_id,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_externalauthsubject (subject_id, integration_id, binding_id, identity_issuer, subject, state, account_id, identity_id, runtime_user_id, updated_at) VALUES ($1, $2, $3, $4, $1, 'active', $5, $6, $5, NOW())",
        fixture.subject_id,
        fixture.integration_id,
        fixture.binding_id,
        fixture.issuer,
        fixture.account_id,
        fixture.identity_id,
    )


async def _clean(fixture: ExternalDatabase) -> None:
    db = fixture.db
    # These rows belong only to this disposable fixture. Clear its synthetic
    # reservations before the existing economic deletion guards run.
    await db.execute_raw(
        "UPDATE deltallm_usertable SET reserved_spend_exact = 0 WHERE user_id = $1 AND reserved_spend_exact <> 0",
        fixture.account_id,
    )
    await db.execute_raw(
        "UPDATE deltallm_teamtable SET reserved_spend_exact = 0 WHERE team_id = $1 AND reserved_spend_exact <> 0",
        fixture.team_id,
    )
    await db.execute_raw(
        "UPDATE deltallm_organizationtable SET reserved_spend_exact = 0 WHERE organization_id = $1 AND reserved_spend_exact <> 0",
        fixture.organization_id,
    )
    references = await db.query_raw(
        "SELECT account_id, runtime_user_id FROM deltallm_externalauthsubject WHERE integration_id = $1",
        fixture.integration_id,
    )
    accounts = list(
        {
            fixture.account_id,
            *(str(row["account_id"]) for row in references if row["account_id"] is not None),
        }
    )
    runtime_users = list(
        {
            fixture.account_id,
            *(
                str(row["runtime_user_id"])
                for row in references
                if row["runtime_user_id"] is not None
            ),
        }
    )
    await db.execute_raw(
        "DELETE FROM deltallm_audit_ingestion_outbox WHERE payload_json->'event'->>'actor_id' = $1",
        fixture.integration_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_audit_ingestion_outbox WHERE payload_json->'event'->>'actor_id' = ANY($1::text[])",
        accounts,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_cacheinvalidationoutbox WHERE metadata->>'actor_id' = ANY($1::text[])",
        accounts,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_verificationtoken WHERE team_id = $1", fixture.team_id
    )
    await AuditIngestionRepository(db).reconcile_capacity()
    await db.execute_raw(
        "DELETE FROM deltallm_platformsession WHERE external_parent_id IN (SELECT parent_id FROM deltallm_externalauthparentsession WHERE integration_id = $1)",
        fixture.integration_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_externalauthparentsession WHERE integration_id = $1",
        fixture.integration_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_externalauthsubject WHERE integration_id = $1", fixture.integration_id
    )
    await db.execute_raw(
        "DELETE FROM deltallm_externalauthbinding WHERE integration_id = $1", fixture.integration_id
    )
    await db.execute_raw(
        "DELETE FROM deltallm_externalauthassertionuse WHERE integration_id = $1",
        fixture.integration_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_externalauthintegration WHERE integration_id = $1",
        fixture.integration_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_platformaccount WHERE account_id = ANY($1::text[])", accounts
    )
    await db.execute_raw(
        "DELETE FROM deltallm_usertable WHERE user_id = ANY($1::text[])", runtime_users
    )
    await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", fixture.team_id)
    await db.execute_raw(
        "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", fixture.organization_id
    )
