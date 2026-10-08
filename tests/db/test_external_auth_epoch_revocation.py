from __future__ import annotations

from uuid import uuid4

import pytest

from src.auth.external_contracts import ExternalPurpose
from src.auth.external_errors import ExternalAuthError
from src.db.external_auth_subjects import ExternalSubjectRepository
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_auth_administration import admin, version
from tests.db.test_external_auth_exchange import enable, proof, services

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


async def suspend(fixture, scope):
    if scope == "integration":
        await admin(fixture).set_integration(
            fixture.integration_id,
            enabled=False,
            request=version(0),
            correlation_id="disable",
            approved_by="operator",
        )
    elif scope == "binding":
        await admin(fixture).set_binding(
            fixture.binding_id,
            active=False,
            request=version(0),
            correlation_id="suspend",
            approved_by="operator",
        )
    else:
        await services(fixture)[2].revoke(
            proof(fixture, purpose=ExternalPurpose.SUSPEND.value), "suspend"
        )


async def resume(fixture, scope):
    if scope == "integration":
        await admin(fixture).set_integration(
            fixture.integration_id,
            enabled=True,
            request=version(1),
            correlation_id="enable",
            approved_by="operator",
        )
    elif scope == "binding":
        await admin(fixture).set_binding(
            fixture.binding_id,
            active=True,
            request=version(1),
            correlation_id="resume",
            approved_by="operator",
        )
    else:
        subject = await ExternalSubjectRepository(fixture.db).get(fixture.subject_id)
        await admin(fixture).resume_subject(
            fixture.subject_id,
            request=version(subject.version),
            correlation_id="resume",
            approved_by="operator",
        )


@pytest.fixture
async def retained_history(external_database):
    fixture = external_database
    prefix = "history-" + uuid4().hex
    await enable(fixture)
    await fixture.db.execute_raw(
        """INSERT INTO deltallm_externalauthparentsession
            (parent_id, integration_id, external_session_id_hash, subject_id,
             auth_time, expires_at, revoked_at, generation, updated_at)
        SELECT $2 || seq::text, $1, encode(digest($2 || seq::text, 'sha256'), 'hex'), $3,
            NOW() - INTERVAL '49 hours', NOW() - INTERVAL '48 hours',
            NOW() - INTERVAL '48 hours', 10, NOW() FROM generate_series(1, 30000) seq""",
        fixture.integration_id,
        prefix,
        fixture.subject_id,
    )
    try:
        yield fixture, prefix
    finally:
        await fixture.db.execute_raw("ANALYZE deltallm_externalauthparentsession")
        while await fixture.db.execute_raw(
            """DELETE FROM deltallm_externalauthparentsession WHERE parent_id IN (
                SELECT parent_id FROM deltallm_externalauthparentsession
                WHERE integration_id = $1 AND parent_id LIKE $2 LIMIT 1000)""",
            fixture.integration_id,
            prefix + "%",
        ):
            pass


@pytest.mark.parametrize("scope", ["integration", "binding", "subject"])
async def test_scope_revocation_is_independent_of_history_and_survives_resume(
    retained_history, scope
):
    fixture, prefix = retained_history
    exchange, _, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "current")
    other_exchange, other_sessions, _ = services(fixture, writer=True)
    token_hash = exchange.identities.sessions.hash_token(response.session_token)
    historical = await fixture.db.query_raw(
        "SELECT min(updated_at) AS first, max(updated_at) AS last, count(*)::int AS count FROM deltallm_externalauthparentsession WHERE integration_id = $1 AND parent_id LIKE $2",
        fixture.integration_id,
        prefix + "%",
    )
    await suspend(fixture, scope)
    assert await other_sessions.get_context(token_hash) is None
    await resume(fixture, scope)
    assert await other_sessions.get_context(token_hash) is None
    with pytest.raises(ExternalAuthError) as denied:
        await other_exchange.exchange(proof(fixture), "closed-parent")
    assert denied.value.code == "external_reauthentication_required"
    fresh = await other_exchange.exchange(
        proof(fixture, external_session_id="fresh-parent"), "fresh"
    )
    assert await other_sessions.get_context(
        exchange.identities.sessions.hash_token(fresh.session_token)
    )
    after = await fixture.db.query_raw(
        "SELECT min(updated_at) AS first, max(updated_at) AS last, count(*)::int AS count FROM deltallm_externalauthparentsession WHERE integration_id = $1 AND parent_id LIKE $2",
        fixture.integration_id,
        prefix + "%",
    )
    assert after == historical and after[0]["count"] == 30000


async def test_expired_child_still_pins_live_parent_epochs(external_database):
    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    first = await exchange.exchange(proof(fixture), "first")
    await fixture.db.execute_raw(
        "UPDATE deltallm_platformsession SET expires_at = NOW() - INTERVAL '1 second' WHERE account_id = $1",
        fixture.account_id,
    )
    assert (
        await sessions.get_context(exchange.identities.sessions.hash_token(first.session_token))
        is None
    )
    renewed = await exchange.exchange(proof(fixture), "renew-after-expiry")
    assert renewed.session_generation == 2


async def test_parent_epoch_lookup_uses_generation_index_at_retained_cardinality(retained_history):
    fixture, prefix = retained_history
    exchange, _, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "current")
    try:
        await fixture.db.execute_raw(
            """INSERT INTO deltallm_platformsession (session_id, account_id, session_token_hash,
                expires_at, revoked_at, updated_at, external_parent_id, external_generation,
                external_integration_epoch, external_binding_epoch, external_subject_epoch)
            SELECT p.parent_id || '-child', $1, p.parent_id || '-hash', p.expires_at,
                p.revoked_at, NOW(), p.parent_id, p.generation, 0, 0, 0
            FROM deltallm_externalauthparentsession p
            WHERE p.integration_id = $2 AND p.parent_id LIKE $3""",
            fixture.account_id,
            fixture.integration_id,
            prefix + "%",
        )
        await fixture.db.execute_raw("ANALYZE deltallm_platformsession")
        child = (
            await fixture.db.query_raw(
                "SELECT external_parent_id, external_generation FROM deltallm_platformsession WHERE session_token_hash = $1",
                exchange.identities.sessions.hash_token(response.session_token),
            )
        )[0]
        plan = (
            await fixture.db.query_raw(
                """EXPLAIN (ANALYZE, FORMAT JSON) SELECT 1 FROM deltallm_platformsession pin
            WHERE pin.external_parent_id = $1 AND pin.external_generation = $2
              AND pin.account_id = $3 AND pin.external_integration_epoch = 0
              AND pin.external_binding_epoch = 0 AND pin.external_subject_epoch = 0 LIMIT 1""",
                child["external_parent_id"],
                child["external_generation"],
                fixture.account_id,
            )
        )[0]["QUERY PLAN"][0]["Plan"]
        nodes = [plan]
        indexed = []
        while nodes:
            node = nodes.pop()
            nodes.extend(node.get("Plans", []))
            if "external_parent_id_external_genera" in node.get("Index Name", ""):
                indexed.append(node)
        assert indexed and all(node["Actual Rows"] <= 1 for node in indexed)
    finally:
        await fixture.db.execute_raw(
            """DELETE FROM deltallm_platformsession child USING deltallm_externalauthparentsession p
            WHERE child.external_parent_id = p.parent_id AND p.integration_id = $1 AND p.parent_id LIKE $2""",
            fixture.integration_id,
            prefix + "%",
        )


async def test_missing_epoch_pin_cannot_reopen_parent(external_database):
    fixture = external_database
    await enable(fixture)
    exchange, _, _ = services(fixture)
    await exchange.exchange(proof(fixture), "first")
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_platformsession WHERE account_id = $1", fixture.account_id
    )
    with pytest.raises(ExternalAuthError) as denied:
        await exchange.exchange(proof(fixture), "missing-pin")
    assert denied.value.code == "external_reauthentication_required"


async def test_required_audit_failure_rolls_back_disable(external_database, monkeypatch):
    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "first")
    administration = admin(fixture)

    async def reject_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(administration.audit, "write", reject_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await administration.set_integration(
            fixture.integration_id,
            enabled=False,
            request=version(0),
            correlation_id="disable",
            approved_by="operator",
        )
    record = (
        await fixture.db.query_raw(
            "SELECT enabled, epoch, version FROM deltallm_externalauthintegration WHERE integration_id = $1",
            fixture.integration_id,
        )
    )[0]
    assert record == {"enabled": True, "epoch": 0, "version": 0}
    assert await sessions.get_context(
        exchange.identities.sessions.hash_token(response.session_token)
    )
