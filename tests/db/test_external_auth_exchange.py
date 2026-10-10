from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import secrets

import pytest

from src.auth.external_config import ExternalAuthSettings
from src.auth.external_contracts import (
    ExternalAssertionClaims,
    ExternalPurpose,
    VerifiedExternalAssertion,
)
from src.auth.external_errors import ExternalAuthError, ExternalAuthUnavailable
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.db.identity.platform_memberships import seed_organization_membership, seed_team_membership
from src.db.audit.repository import AuditRepository
from src.services.audit_service import AuditIngestionConfig, AuditService
from src.services.external_auth_audit import ExternalAuthAudit
from src.services.external_auth_exchange import ExternalAuthExchange
from src.services.external_auth_revocation import ExternalAuthRevocation
from src.services.external_auth_sessions import ExternalSessionService
from src.services.platform_identity_service import PlatformIdentityService
from tests.db import external_auth_fixtures as fixtures
from tests.db.external_auth_fixtures import ExternalDatabase

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


def proof(fixture: ExternalDatabase, **updates) -> VerifiedExternalAssertion:
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": "https://console.example.com",
        "aud": "deltallm",
        "sub": fixture.subject_id,
        "identity_issuer": fixture.issuer,
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "jti": secrets.token_urlsafe(16),
        "purpose": str(ExternalPurpose.EXCHANGE),
        "binding_id": fixture.binding_id,
        "external_customer_id": fixture.binding_id,
        "email": fixture.account_id + "@example.com",
        "email_verified": True,
        "external_session_id": "parent-" + fixture.subject_id,
        "auth_time": fixture.auth_time,
        "external_session_expires_at": fixture.auth_time + 3630,
        **updates,
    }
    return VerifiedExternalAssertion(
        fixture.integration_id, ExternalAssertionClaims.model_validate(claims)
    )


def services(fixture: ExternalDatabase, *, writer: bool = False):
    db = fixture.writer if writer else fixture.db
    transactions = ExternalAuthTransactions(db)
    identities = PlatformIdentityService(db, salt="test-external-salt")
    audit = ExternalAuthAudit(
        AuditService(
            AuditRepository(db), db_client=db, ingestion_config=AuditIngestionConfig(enabled=True)
        )
    )
    settings = ExternalAuthSettings()
    return (
        ExternalAuthExchange(transactions, audit, identities, settings),
        ExternalSessionService(transactions, audit),
        ExternalAuthRevocation(transactions, audit, settings),
    )


async def enable(fixture: ExternalDatabase) -> None:
    await fixture.db.execute_raw(
        "UPDATE deltallm_externalauthintegration SET enabled = true WHERE integration_id = $1",
        fixture.integration_id,
    )
    await seed_organization_membership(
        fixture.db, account_id=fixture.account_id, organization_id=fixture.organization_id
    )
    await seed_team_membership(
        fixture.db, account_id=fixture.account_id, team_id=fixture.team_id, role="team_developer"
    )


async def make_new(fixture: ExternalDatabase) -> None:
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_externalauthsubject WHERE subject_id = $1", fixture.subject_id
    )
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_platformaccount WHERE account_id = $1", fixture.account_id
    )
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_usertable WHERE user_id = $1", fixture.account_id
    )


async def test_first_and_repeat_exchange_preserve_identity_and_billing(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    before = await fixture.db.query_raw(
        "SELECT * FROM deltallm_teamtable WHERE team_id = $1", fixture.team_id
    )
    exchange, sessions, _ = services(fixture)
    first = proof(fixture)
    await exchange.consume(first, "first")
    response = await exchange.exchange(first, "first")
    assert response.session_generation == 1
    assert response.inference_user_id == response.account_id
    assert response.mfa_required is False and response.next_step == "ready"
    context = await sessions.get_context(
        exchange.identities.sessions.hash_token(response.session_token)
    )
    assert context is not None
    assert context.external_workspace.team_id == fixture.team_id
    second = proof(
        fixture,
        email="updated-email@example.com",
        auth_time=first.claims.auth_time,
        external_session_expires_at=first.claims.external_session_expires_at,
    )
    await exchange.consume(second, "second")
    renewed = await exchange.exchange(second, "second")
    assert renewed.account_id == response.account_id
    assert renewed.inference_user_id == response.inference_user_id
    assert renewed.session_generation == 2
    assert (
        await fixture.db.query_raw(
            "SELECT * FROM deltallm_teamtable WHERE team_id = $1", fixture.team_id
        )
        == before
    )
    stored = await fixture.db.query_raw(
        "SELECT email FROM deltallm_platformaccount WHERE account_id = $1", response.account_id
    )
    assert stored == [{"email": first.claims.email}]


async def test_concurrent_fresh_assertions_converge_on_one_account(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    left, _, _ = services(fixture)
    right, _, _ = services(fixture, writer=True)
    first = proof(fixture)
    second = proof(
        fixture,
        auth_time=first.claims.auth_time,
        external_session_expires_at=first.claims.external_session_expires_at,
    )
    await asyncio.gather(left.consume(first, "left"), right.consume(second, "right"))
    responses = await asyncio.gather(left.exchange(first, "left"), right.exchange(second, "right"))
    assert responses[0].account_id == responses[1].account_id
    assert sorted(item.session_generation for item in responses) == [1, 2]
    count = await fixture.db.query_raw(
        "SELECT count(*)::int AS count FROM deltallm_externalauthsubject WHERE integration_id = $1",
        fixture.integration_id,
    )
    assert count == [{"count": 1}]


async def test_same_nonce_is_durable_across_services(external_database: ExternalDatabase):
    fixture = external_database
    await enable(fixture)
    left, _, _ = services(fixture)
    right, _, _ = services(fixture, writer=True)
    assertion = proof(fixture)
    results = await asyncio.gather(
        left.consume(assertion, "left"), right.consume(assertion, "right"), return_exceptions=True
    )
    assert sum(result is None for result in results) == 1
    errors = [result for result in results if isinstance(result, ExternalAuthError)]
    assert len(errors) == 1 and errors[0].code == "assertion_replayed"


async def test_email_collision_rolls_back_but_consumes_nonce(external_database: ExternalDatabase):
    fixture = external_database
    await enable(fixture)
    exchange, _, _ = services(fixture)
    assertion = proof(fixture, sub="different-subject")
    await exchange.consume(assertion, "collision")
    with pytest.raises(ExternalAuthError) as error:
        await exchange.exchange(assertion, "collision")
    assert error.value.code == "account_link_required"
    assert (
        await fixture.db.query_raw(
            "SELECT subject FROM deltallm_externalauthsubject WHERE subject = 'different-subject'"
        )
        == []
    )
    with pytest.raises(ExternalAuthError) as replay:
        await exchange.consume(assertion, "retry")
    assert replay.value.code == "assertion_replayed"


async def test_audit_failure_rolls_back_first_provisioning(
    external_database: ExternalDatabase, monkeypatch
):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    exchange, _, _ = services(fixture)
    assertion = proof(fixture)
    await exchange.consume(assertion, "attempt")

    async def unavailable(*args):
        raise ExternalAuthUnavailable()

    monkeypatch.setattr(exchange.audit, "write_many", unavailable)
    with pytest.raises(ExternalAuthUnavailable):
        await exchange.exchange(assertion, "failed")
    assert (
        await fixture.db.query_raw(
            "SELECT account_id FROM deltallm_platformaccount WHERE email = $1",
            assertion.claims.email,
        )
        == []
    )
    assert (
        await fixture.db.query_raw(
            "SELECT subject_id FROM deltallm_externalauthsubject WHERE integration_id = $1",
            fixture.integration_id,
        )
        == []
    )
    assert await fixture.db.query_raw(
        "SELECT purpose FROM deltallm_externalauthassertionuse WHERE integration_id = $1",
        fixture.integration_id,
    ) == [{"purpose": str(ExternalPurpose.EXCHANGE)}]


async def test_revoke_before_login_blocks_old_parent_but_allows_new_login(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    exchange, _, revocation = services(fixture)
    revoked = proof(fixture, purpose=str(ExternalPurpose.REVOKE))
    await exchange.consume(revoked, "logout")
    await revocation.revoke(revoked, "logout")
    assertion = proof(
        fixture,
        auth_time=revoked.claims.auth_time,
        external_session_expires_at=revoked.claims.external_session_expires_at,
    )
    await exchange.consume(assertion, "old-parent")
    with pytest.raises(ExternalAuthError):
        await exchange.exchange(assertion, "old-parent")
    new_session = proof(fixture, external_session_id="new-parent")
    await exchange.consume(new_session, "new-parent")
    assert (await exchange.exchange(new_session, "new-parent")).session_generation == 1


async def test_subject_suspend_before_login_does_not_create_account(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    exchange, _, revocation = services(fixture)
    suspend = proof(fixture, purpose=str(ExternalPurpose.SUSPEND))
    await exchange.consume(suspend, "suspend")
    await revocation.revoke(suspend, "suspend")
    assertion = proof(fixture, external_session_id="other-parent")
    await exchange.consume(assertion, "denied")
    with pytest.raises(ExternalAuthError):
        await exchange.exchange(assertion, "denied")
    assert await fixture.db.query_raw(
        "SELECT account_id, state FROM deltallm_externalauthsubject WHERE integration_id = $1",
        fixture.integration_id,
    ) == [{"account_id": None, "state": "suspended"}]


async def test_three_generations_leave_only_current_and_previous(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    original = proof(fixture)
    responses = []
    for _ in range(3):
        assertion = proof(
            fixture,
            auth_time=original.claims.auth_time,
            external_session_expires_at=original.claims.external_session_expires_at,
        )
        await exchange.consume(assertion, "renew")
        responses.append(await exchange.exchange(assertion, "renew"))
    for index, response in enumerate(responses):
        context = await sessions.get_context(
            exchange.identities.sessions.hash_token(response.session_token)
        )
        assert (context is not None) == (index > 0)
    remaining = await fixture.db.query_raw(
        "SELECT external_generation, expires_at - (NOW() AT TIME ZONE 'UTC') < INTERVAL '31 seconds' AS overlap FROM deltallm_platformsession WHERE account_id = $1 AND revoked_at IS NULL ORDER BY external_generation",
        fixture.account_id,
    )
    assert remaining == [
        {"external_generation": 2, "overlap": True},
        {"external_generation": 3, "overlap": False},
    ]


@pytest.mark.parametrize("mutation", ["membership", "binding", "integration", "account", "parent"])
async def test_current_state_revokes_on_other_replica(
    external_database: ExternalDatabase, mutation
):
    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    assertion = proof(fixture)
    await exchange.consume(assertion, "issue")
    response = await exchange.exchange(assertion, "issue")
    operations = {
        "membership": (
            "DELETE FROM deltallm_teammembership WHERE account_id = $1",
            fixture.account_id,
        ),
        "binding": (
            "UPDATE deltallm_externalauthbinding SET state = 'suspended' WHERE binding_id = $1",
            fixture.binding_id,
        ),
        "integration": (
            "UPDATE deltallm_externalauthintegration SET enabled = false WHERE integration_id = $1",
            fixture.integration_id,
        ),
        "account": (
            "UPDATE deltallm_platformaccount SET is_active = false WHERE account_id = $1",
            fixture.account_id,
        ),
        "parent": (
            "UPDATE deltallm_externalauthparentsession SET revoked_at = NOW() WHERE subject_id = $1",
            fixture.subject_id,
        ),
    }
    query, value = operations[mutation]
    await fixture.writer.execute_raw(query, value)
    assert (
        await sessions.get_context(exchange.identities.sessions.hash_token(response.session_token))
        is None
    )


async def test_linked_mfa_and_password_controls_remain(external_database: ExternalDatabase):
    fixture = external_database
    await enable(fixture)
    await fixture.db.execute_raw(
        "UPDATE deltallm_platformaccount SET mfa_enabled = true, force_password_change = true WHERE account_id = $1",
        fixture.account_id,
    )
    exchange, sessions, _ = services(fixture)
    assertion = proof(fixture)
    await exchange.consume(assertion, "mfa")
    response = await exchange.exchange(assertion, "mfa")
    assert response.next_step == "mfa_verify" and response.mfa_required
    context = await sessions.get_context(
        exchange.identities.sessions.hash_token(response.session_token)
    )
    assert context.mfa_enabled and not context.mfa_verified and context.force_password_change


async def test_cancelled_provisioning_keeps_nonce_and_releases_capacity(
    external_database: ExternalDatabase, monkeypatch
):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    exchange, _, _ = services(fixture)
    assertion = proof(fixture)
    await exchange.consume(assertion, "attempt")
    reached = asyncio.Event()
    original = exchange._provision

    async def pause(*args):
        result = await original(*args)
        reached.set()
        await asyncio.Future()
        return result

    monkeypatch.setattr(exchange, "_provision", pause)
    task = asyncio.create_task(exchange.exchange(assertion, "cancel"))
    await asyncio.wait_for(reached.wait(), 0.7)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exchange.transactions.gates["mutation"].active == 0
    assert (
        await fixture.db.query_raw(
            "SELECT account_id FROM deltallm_platformaccount WHERE email = $1",
            assertion.claims.email,
        )
        == []
    )
    with pytest.raises(ExternalAuthError) as replay:
        await exchange.consume(assertion, "retry")
    assert replay.value.code == "assertion_replayed"
