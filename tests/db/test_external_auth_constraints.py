from __future__ import annotations

import asyncio
from uuid import uuid4

from prisma.errors import RawQueryError, UniqueViolationError
import pytest

from tests.db import external_auth_fixtures as fixtures
from tests.db.external_auth_fixtures import ExternalDatabase

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


@pytest.mark.parametrize(
    "field,value",
    [
        ("binding_id", "other"),
        ("integration_id", "other"),
        ("identity_issuer", "https://other.example.com"),
        ("subject", "other"),
        ("account_id", None),
        ("runtime_user_id", None),
        ("identity_id", None),
    ],
)
async def test_durable_subject_mapping_cannot_be_rebound(
    external_database: ExternalDatabase, field, value
):
    with pytest.raises(RawQueryError):
        await external_database.db.execute_raw(
            f"UPDATE deltallm_externalauthsubject SET {field} = $1 WHERE subject_id = $2",
            value,
            external_database.subject_id,
        )


@pytest.mark.parametrize("field", ["organization_id", "team_id", "external_customer_id", "profile"])
async def test_registered_binding_is_immutable(external_database: ExternalDatabase, field):
    with pytest.raises(RawQueryError):
        await external_database.db.execute_raw(
            f"UPDATE deltallm_externalauthbinding SET {field} = 'other' WHERE binding_id = $1",
            external_database.binding_id,
        )


async def test_active_account_cannot_gain_admin_or_outside_membership(
    external_database: ExternalDatabase,
):
    fixture = external_database
    with pytest.raises(RawQueryError):
        await fixture.db.execute_raw(
            "UPDATE deltallm_platformaccount SET role = 'platform_admin' WHERE account_id = $1",
            fixture.account_id,
        )
    for table, column in (
        ("deltallm_organizationmembership", "organization_id"),
        ("deltallm_teammembership", "team_id"),
    ):
        with pytest.raises(RawQueryError):
            await fixture.db.execute_raw(
                f"INSERT INTO {table} (membership_id, account_id, {column}, updated_at) VALUES ($1, $2, 'outside', NOW())",
                uuid4().hex,
                fixture.account_id,
            )
    await fixture.db.execute_raw(
        "UPDATE deltallm_externalauthsubject SET state = 'suspended' WHERE subject_id = $1",
        fixture.subject_id,
    )
    await fixture.db.execute_raw(
        "UPDATE deltallm_platformaccount SET role = 'platform_admin' WHERE account_id = $1",
        fixture.account_id,
    )
    with pytest.raises(RawQueryError):
        await fixture.db.execute_raw(
            "UPDATE deltallm_externalauthsubject SET state = 'active' WHERE subject_id = $1",
            fixture.subject_id,
        )


async def test_runtime_and_identity_changes_cannot_break_mapping(
    external_database: ExternalDatabase,
):
    fixture = external_database
    with pytest.raises(RawQueryError):
        await fixture.db.execute_raw(
            "UPDATE deltallm_usertable SET team_id = NULL WHERE user_id = $1", fixture.account_id
        )
    with pytest.raises(RawQueryError):
        await fixture.db.execute_raw(
            "UPDATE deltallm_platformidentity SET subject = 'other' WHERE identity_id = $1",
            fixture.identity_id,
        )
    with pytest.raises(RawQueryError):
        await fixture.db.execute_raw(
            "UPDATE deltallm_teamtable SET organization_id = NULL WHERE team_id = $1",
            fixture.team_id,
        )


async def test_suspension_allows_explicit_tenant_migration_without_reactivation(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await fixture.db.execute_raw(
        "UPDATE deltallm_externalauthbinding SET state = 'suspended' WHERE binding_id = $1",
        fixture.binding_id,
    )
    await fixture.db.execute_raw(
        "UPDATE deltallm_teamtable SET organization_id = NULL WHERE team_id = $1", fixture.team_id
    )
    with pytest.raises(RawQueryError):
        await fixture.db.execute_raw(
            "UPDATE deltallm_externalauthbinding SET state = 'active' WHERE binding_id = $1",
            fixture.binding_id,
        )


async def make_parent(fixture: ExternalDatabase, *, revoked: bool = False) -> str:
    parent_id = uuid4().hex
    await fixture.db.execute_raw(
        "INSERT INTO deltallm_externalauthparentsession (parent_id, integration_id, external_session_id_hash, subject_id, auth_time, expires_at, revoked_at, generation, updated_at) VALUES ($1, $2, $3, $4, NOW(), NOW() + INTERVAL '1 hour', CASE WHEN $5 THEN NOW() END, 1, NOW())",
        parent_id,
        fixture.integration_id,
        uuid4().hex * 2,
        fixture.subject_id,
        revoked,
    )
    return parent_id


async def test_parent_tombstone_cannot_be_reopened_or_extended(external_database: ExternalDatabase):
    fixture = external_database
    parent_id = await make_parent(fixture, revoked=True)
    for expression in (
        "revoked_at = NULL",
        "auth_time = NOW() + INTERVAL '1 minute'",
        "expires_at = expires_at + INTERVAL '1 second'",
        "generation = 0",
        "external_session_id_hash = repeat('a', 64)",
    ):
        with pytest.raises(RawQueryError):
            await fixture.db.execute_raw(
                f"UPDATE deltallm_externalauthparentsession SET {expression} WHERE parent_id = $1",
                parent_id,
            )


async def test_external_session_metadata_must_be_complete(external_database: ExternalDatabase):
    fixture = external_database
    parent_id = await make_parent(fixture)
    with pytest.raises(RawQueryError):
        await fixture.db.execute_raw(
            "INSERT INTO deltallm_platformsession (session_id, account_id, session_token_hash, expires_at, external_parent_id, updated_at) VALUES ($1, $2, $1, NOW() + INTERVAL '1 minute', $3, NOW())",
            uuid4().hex,
            fixture.account_id,
            parent_id,
        )
    with pytest.raises(RawQueryError):
        await fixture.db.execute_raw(
            "INSERT INTO deltallm_platformsession (session_id, account_id, session_token_hash, expires_at, external_parent_id, external_generation, external_integration_epoch, external_binding_epoch, external_subject_epoch, updated_at) VALUES ($1, $2, $1, NOW() + INTERVAL '2 hours', $3, 1, 0, 0, 0, NOW())",
            uuid4().hex,
            fixture.account_id,
            parent_id,
        )


async def test_nonce_is_unique_across_connections_and_purposes(external_database: ExternalDatabase):
    fixture = external_database

    async def consume(db, purpose):
        return await db.execute_raw(
            "INSERT INTO deltallm_externalauthassertionuse (integration_id, jti_hash, purpose, retain_until) VALUES ($1, repeat('a', 64), $2, NOW() + INTERVAL '15 minutes')",
            fixture.integration_id,
            purpose,
        )

    results = await asyncio.gather(
        consume(fixture.db, "gateway_session_exchange"),
        consume(fixture.writer, "gateway_session_revoke"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, int) for result in results) == 1
    assert sum(isinstance(result, (RawQueryError, UniqueViolationError)) for result in results) == 1


@pytest.mark.parametrize("entity", ["account", "identity", "runtime", "team", "organization"])
async def test_lifecycle_removal_retains_mapping_and_revokes_all_children(
    external_database, entity
):
    from tests.db.test_external_auth_exchange import enable, proof, services

    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "lifecycle")
    token_hash = exchange.identities.sessions.hash_token(response.session_token)
    assert await sessions.get_context(token_hash) is not None
    operations = {
        "account": (
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1",
            fixture.account_id,
        ),
        "identity": (
            "DELETE FROM deltallm_platformidentity WHERE identity_id = $1",
            fixture.identity_id,
        ),
        "runtime": ("DELETE FROM deltallm_usertable WHERE user_id = $1", fixture.account_id),
        "team": ("DELETE FROM deltallm_teamtable WHERE team_id = $1", fixture.team_id),
        "organization": (
            "UPDATE deltallm_organizationtable SET lifecycle_state = 'deletion_pending' WHERE organization_id = $1",
            fixture.organization_id,
        ),
    }
    query, value = operations[entity]
    await fixture.writer.execute_raw(query, value)
    subject = await fixture.db.query_raw(
        "SELECT * FROM deltallm_externalauthsubject WHERE subject_id = $1", fixture.subject_id
    )
    assert subject[0]["state"] == "suspended"
    assert subject[0]["account_id"] == fixture.account_id
    assert subject[0]["runtime_user_id"] == fixture.account_id
    assert await sessions.get_context(token_hash) is None
    parents = await fixture.db.query_raw(
        "SELECT revoked_at FROM deltallm_externalauthparentsession WHERE subject_id = $1",
        fixture.subject_id,
    )
    assert parents and all(row["revoked_at"] is not None for row in parents)
    children = await fixture.db.query_raw(
        "SELECT revoked_at FROM deltallm_platformsession WHERE session_token_hash = $1", token_hash
    )
    assert all(row["revoked_at"] is not None for row in children)
    if entity in {"team", "organization"}:
        binding = await fixture.db.query_raw(
            "SELECT state FROM deltallm_externalauthbinding WHERE binding_id = $1",
            fixture.binding_id,
        )
        assert binding[0]["state"] == "suspended"
