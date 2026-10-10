from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.auth.external_contracts import ExternalPurpose
from src.auth.external_errors import ExternalAuthError
from src.db.identity.external.external_auth_cleanup import ExternalAuthCleanupRepository
from src.db.identity.external.external_auth_subjects import ExternalSubjectRepository
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.models.external_auth import ExternalVersionRequest
from src.services.identity.external.external_auth_administration import ExternalAuthAdministration
from src.services.identity.external.external_auth_linking import (
    ExternalAuthLinking,
    ExternalLinkApproval,
)
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_auth_exchange import enable, make_new, proof, services

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


def admin(fixture):
    exchange, _, _ = services(fixture)
    settings = SimpleNamespace(
        integrations=(SimpleNamespace(integration_id=fixture.integration_id),)
    )
    return ExternalAuthAdministration(exchange.transactions, exchange.audit, settings)


def version(number):
    return ExternalVersionRequest(expected_version=number, reason="approved test change")


async def test_disable_reenable_cannot_reopen_existing_parent(external_database):
    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "first")
    administration = admin(fixture)
    disabled = await administration.set_integration(
        fixture.integration_id,
        enabled=False,
        request=version(0),
        correlation_id="disable",
        approved_by="operator",
    )
    assert disabled.epoch == 1 and disabled.version == 1
    assert (
        await sessions.get_context(exchange.identities.sessions.hash_token(response.session_token))
        is None
    )
    enabled = await administration.set_integration(
        fixture.integration_id,
        enabled=True,
        request=version(1),
        correlation_id="enable",
        approved_by="operator",
    )
    assert enabled.epoch == 1
    with pytest.raises(ExternalAuthError) as denied:
        await exchange.exchange(proof(fixture), "old-parent")
    assert denied.value.code == "external_reauthentication_required"
    fresh = await exchange.exchange(proof(fixture, external_session_id="fresh-parent"), "fresh")
    assert fresh.account_id == response.account_id


async def test_binding_registration_is_idempotent_and_stale_versions_conflict(external_database):
    fixture = external_database
    administration = admin(fixture)
    binding = await administration.register_binding(
        fixture.integration_id,
        customer_id=fixture.binding_id,
        organization_id=fixture.organization_id,
        team_id=fixture.team_id,
        correlation_id="register",
        approved_by="operator",
    )
    assert binding.binding_id == fixture.binding_id
    suspended = await administration.set_binding(
        fixture.binding_id,
        active=False,
        request=version(0),
        correlation_id="suspend",
        approved_by="operator",
    )
    assert suspended.state == "suspended" and suspended.epoch == 1
    with pytest.raises(ExternalAuthError) as conflict:
        await administration.set_binding(
            fixture.binding_id,
            active=True,
            request=version(0),
            correlation_id="stale",
            approved_by="operator",
        )
    assert conflict.value.status_code == 409
    resumed = await administration.set_binding(
        fixture.binding_id,
        active=True,
        request=version(1),
        correlation_id="resume",
        approved_by="operator",
    )
    assert resumed.epoch == 2 and resumed.version == 2
    page = await administration.list_bindings(
        fixture.integration_id, after=None, state="active", customer=fixture.binding_id, limit=2
    )
    assert page == [resumed]
    assert (
        await administration.list_bindings(
            fixture.integration_id, after=resumed.binding_id, state=None, customer=None, limit=2
        )
        == []
    )


async def test_subject_resume_keeps_revoked_parent_closed(external_database):
    fixture = external_database
    await enable(fixture)
    exchange, _, revoke = services(fixture)
    await exchange.exchange(proof(fixture), "first")
    await revoke.revoke(proof(fixture, purpose=ExternalPurpose.SUSPEND.value), "suspend")
    subject = await ExternalSubjectRepository(fixture.db).get(fixture.subject_id)
    resumed = await admin(fixture).resume_subject(
        fixture.subject_id,
        request=version(subject.version),
        correlation_id="resume",
        approved_by="operator",
    )
    assert resumed.state == "active" and resumed.epoch == subject.epoch + 1
    with pytest.raises(ExternalAuthError):
        await exchange.exchange(proof(fixture), "old-parent")
    assert (
        await exchange.exchange(proof(fixture, external_session_id="fresh-parent"), "fresh")
    ).account_id == fixture.account_id


async def test_explicit_link_preserves_password_mfa_runtime_and_memberships(external_database):
    fixture = external_database
    await enable(fixture)
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_externalauthsubject WHERE subject_id = $1", fixture.subject_id
    )
    await fixture.db.execute_raw(
        "UPDATE deltallm_platformaccount SET password_hash = 'preserved-hash', mfa_enabled = true, force_password_change = true WHERE account_id = $1",
        fixture.account_id,
    )
    before = await fixture.db.query_raw(
        "SELECT * FROM deltallm_platformaccount WHERE account_id = $1", fixture.account_id
    )
    exchange, _, _ = services(fixture)
    linking = ExternalAuthLinking(exchange.transactions, exchange.audit, exchange.identities)
    assertion = proof(fixture, purpose=ExternalPurpose.LINK.value)
    await exchange.consume(assertion, "link-attempt")
    approval = ExternalLinkApproval(
        fixture.binding_id,
        fixture.account_id,
        0,
        "change-344",
        "approved dedicated account",
        "operator",
        fixture.account_id,
    )
    linked = await linking.link(assertion, approval, "link")
    assert linked.account_id == fixture.account_id and linked.runtime_user_id == fixture.account_id
    after = await fixture.db.query_raw(
        "SELECT * FROM deltallm_platformaccount WHERE account_id = $1", fixture.account_id
    )
    for field in (
        "password_hash",
        "mfa_enabled",
        "force_password_change",
        "role",
        "email",
        "metadata",
    ):
        assert before[0][field] == after[0][field]
    with pytest.raises(ExternalAuthError) as conflict:
        await linking.link(proof(fixture, purpose=ExternalPurpose.LINK.value), approval, "repeat")
    assert conflict.value.status_code == 409
    issued = await exchange.exchange(proof(fixture), "exchange")
    assert issued.next_step == "mfa_verify" and issued.mfa_required


async def test_link_denies_wrong_email_and_does_not_add_identity(external_database):
    fixture = external_database
    await enable(fixture)
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_externalauthsubject WHERE subject_id = $1", fixture.subject_id
    )
    exchange, _, _ = services(fixture)
    linking = ExternalAuthLinking(exchange.transactions, exchange.audit, exchange.identities)
    approval = ExternalLinkApproval(
        fixture.binding_id,
        fixture.account_id,
        0,
        "change",
        "approved",
        "operator",
        fixture.account_id,
    )
    with pytest.raises(ExternalAuthError) as denied:
        await linking.link(
            proof(fixture, email="other@example.com", purpose=ExternalPurpose.LINK.value),
            approval,
            "link",
        )
    assert denied.value.status_code == 403
    assert not await fixture.db.query_raw(
        "SELECT 1 FROM deltallm_externalauthsubject WHERE binding_id = $1", fixture.binding_id
    )


async def test_explicit_runtime_binding_requires_fresh_pending_version(external_database):
    fixture = external_database
    await enable(fixture)
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_externalauthsubject WHERE subject_id = $1", fixture.subject_id
    )
    exchange, _, _ = services(fixture)
    linking = ExternalAuthLinking(exchange.transactions, exchange.audit, exchange.identities)
    approval = ExternalLinkApproval(
        fixture.binding_id, fixture.account_id, 0, "change", "approved", "operator"
    )
    bound = await linking.bind_runtime(
        proof(fixture, purpose=ExternalPurpose.RUNTIME_BIND.value), approval, "runtime-bind"
    )
    assert bound.state == "pending" and bound.account_id is None and bound.version == 1
    with pytest.raises(ExternalAuthError) as conflict:
        await linking.bind_runtime(
            proof(fixture, purpose=ExternalPurpose.RUNTIME_BIND.value), approval, "stale"
        )
    assert conflict.value.status_code == 409


class CountingDatabase:
    def __init__(self, db, queries=None):
        self.db = db
        self.queries = queries if queries is not None else []

    def is_transaction(self):
        return self.db.is_transaction()

    async def query_raw(self, query, *params):
        self.queries.append(query)
        return await self.db.query_raw(query, *params)

    async def execute_raw(self, query, *params):
        self.queries.append(query)
        return await self.db.execute_raw(query, *params)

    @asynccontextmanager
    async def tx(self, **kwargs):
        async with self.db.tx(**kwargs) as transaction:
            yield CountingDatabase(transaction, self.queries)


async def test_exchange_query_budgets_include_required_audit_and_transaction_setup(
    external_database,
):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    counted = CountingDatabase(fixture.db)
    exchange, sessions, _ = services(replace(fixture, db=counted))
    first = proof(fixture)
    await exchange.consume(first, "first")
    assert len(counted.queries) <= 4
    counted.queries.clear()
    response = await exchange.exchange(first, "first")
    assert len(counted.queries) <= 16, (len(counted.queries), counted.queries)
    counted.queries.clear()
    await exchange.exchange(proof(fixture), "repeat")
    assert len(counted.queries) <= 10, (len(counted.queries), counted.queries)
    counted.queries.clear()
    await sessions.get_context(exchange.identities.sessions.hash_token(response.session_token))
    assert len(counted.queries) == 2  # One UTC/deadline setup, one indexed authorization join.


async def test_cleanup_lease_is_shared_across_database_connections(external_database):
    fixture = external_database
    # Release the test database's singleton lease without touching other state.
    await fixture.db.execute_raw(
        "UPDATE deltallm_externalauthmaintenancelease SET next_run_at = '-infinity' WHERE lease_id = 'external-auth-cleanup'"
    )
    async with ExternalAuthTransactions(fixture.db).transaction("maintenance") as db:
        assert await ExternalAuthCleanupRepository(db).claim(5)
    async with ExternalAuthTransactions(fixture.writer).transaction("maintenance") as db:
        assert not await ExternalAuthCleanupRepository(db).claim(5)


async def test_mfa_proof_survives_renewal_but_not_secret_change_or_new_parent(external_database):
    fixture = external_database
    await enable(fixture)
    await fixture.db.execute_raw(
        "UPDATE deltallm_platformaccount SET mfa_enabled = true, mfa_secret = 'verified-secret' WHERE account_id = $1",
        fixture.account_id,
    )
    exchange, sessions, _ = services(fixture)
    exchange.identities.sessions.external = sessions
    first = await exchange.exchange(proof(fixture), "first")
    assert first.mfa_required
    assert not await exchange.identities.sessions.verify_mfa(
        token=first.session_token, code="wrong", verify_code=lambda secret, code: code == "correct"
    )
    assert await exchange.identities.sessions.verify_mfa(
        token=first.session_token,
        code="correct",
        verify_code=lambda secret, code: secret == "verified-secret" and code == "correct",
    )
    renewed = await exchange.exchange(proof(fixture), "renewed")
    assert not renewed.mfa_required and renewed.next_step == "ready"
    context = await sessions.get_context(
        exchange.identities.sessions.hash_token(renewed.session_token)
    )
    assert context.mfa_verified
    fresh = await exchange.exchange(
        proof(fixture, external_session_id="different-parent"), "different-parent"
    )
    assert fresh.mfa_required
    await fixture.writer.execute_raw(
        "UPDATE deltallm_platformaccount SET mfa_secret = 'changed-secret' WHERE account_id = $1",
        fixture.account_id,
    )
    context = await sessions.get_context(
        exchange.identities.sessions.hash_token(renewed.session_token)
    )
    assert not context.mfa_verified
    assert (await exchange.exchange(proof(fixture), "after-secret-change")).mfa_required


async def test_account_session_revocation_tombstones_external_parents(external_database):
    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    issued = await exchange.exchange(proof(fixture), "first")
    await exchange.identities.sessions.revoke_for_account(fixture.account_id)
    assert (
        await sessions.get_context(exchange.identities.sessions.hash_token(issued.session_token))
        is None
    )
    with pytest.raises(ExternalAuthError):
        await exchange.exchange(proof(fixture), "renewal")
    rows = await fixture.db.query_raw(
        "SELECT child.revoked_at AS child_revoked, parent.revoked_at AS parent_revoked FROM deltallm_platformsession child JOIN deltallm_externalauthparentsession parent ON parent.parent_id = child.external_parent_id WHERE child.account_id = $1",
        fixture.account_id,
    )
    assert rows and all(row["child_revoked"] and row["parent_revoked"] for row in rows)


async def test_bounded_rollback_revokes_legacy_children_and_retains_tombstones(external_database):
    from src.db.identity.external.external_auth_rollback import ExternalAuthRollbackRepository

    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "rollback")
    token_hash = exchange.identities.sessions.hash_token(response.session_token)
    async with exchange.transactions.transaction() as tx:
        await ExternalAuthRollbackRepository(tx).disable()
    for parents in [False, True]:
        while True:
            async with exchange.transactions.transaction("maintenance") as tx:
                removed = await ExternalAuthRollbackRepository(tx).revoke_page(
                    parents=parents, limit=1
                )
            if not removed:
                break
    assert await sessions.get_context(token_hash) is None
    parent = await fixture.db.query_raw(
        "SELECT revoked_at FROM deltallm_externalauthparentsession WHERE subject_id = $1",
        fixture.subject_id,
    )
    child = await fixture.db.query_raw(
        "SELECT revoked_at FROM deltallm_platformsession WHERE session_token_hash = $1", token_hash
    )
    assert parent[0]["revoked_at"] is not None and child[0]["revoked_at"] is not None
    async with exchange.transactions.transaction("validation") as tx:
        remaining = await ExternalAuthRollbackRepository(tx).counts()
    assert remaining["live_children"] == 0 and remaining["live_parents"] == 0
