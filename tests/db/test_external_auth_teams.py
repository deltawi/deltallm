from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.auth.external_errors import ExternalAuthError
from src.db.identity.external.external_auth_provisioning import ExternalProvisioningRepository
from src.db.identity.external.external_auth_bindings import ExternalBindingRepository
from src.db.identity.external.external_auth_rollback import ExternalAuthRollbackRepository
from src.middleware.platform_auth import has_scoped_permission
from src.models.external_auth import ExternalTeamGrantRequest, ExternalVersionRequest
from src.services.identity.external.external_auth_teams import ExternalTeamAdministration
from tests.db import external_auth_fixtures as fixtures
from tests.db.external_auth_fixtures import ExternalDatabase
from tests.db.test_external_auth_exchange import enable, make_new, proof, services

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


def teams_admin(fixture: ExternalDatabase) -> ExternalTeamAdministration:
    exchange, _, _ = services(fixture)
    settings = SimpleNamespace(
        integrations=(
            SimpleNamespace(integration_id=fixture.integration_id, identity_issuer=fixture.issuer),
        )
    )
    return ExternalTeamAdministration(exchange.transactions, exchange.audit, settings)


def version(number: int) -> ExternalVersionRequest:
    return ExternalVersionRequest(expected_version=number, reason="approved test change")


def grant_request(fixture: ExternalDatabase, number: int = 0, subject: str | None = None):
    return ExternalTeamGrantRequest(
        expected_version=number,
        reason="approved test change",
        identity_issuer=fixture.issuer,
        subject=subject or fixture.subject_id,
    )


async def add_team(fixture: ExternalDatabase, suffix: str, organization_id: str | None = None):
    team_id = f"{fixture.team_id}-{suffix}"
    await fixture.db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, organization_id, models, updated_at) VALUES ($1, $2, ARRAY[]::text[], NOW())",
        team_id,
        organization_id or fixture.organization_id,
    )
    return team_id


async def attach_and_grant(fixture: ExternalDatabase, suffix: str = "b") -> str:
    team_id = await add_team(fixture, suffix)
    admin = teams_admin(fixture)
    await admin.attach(
        fixture.binding_id, team_id, request=version(0), correlation_id="a", approved_by="op"
    )
    await admin.grant(
        fixture.binding_id,
        team_id,
        request=grant_request(fixture),
        correlation_id="g",
        approved_by="op",
    )
    return team_id


async def context_for(fixture: ExternalDatabase, response):
    exchange, sessions, _ = services(fixture)
    return await sessions.get_context(
        exchange.identities.sessions.hash_token(response.session_token)
    )


async def test_exchange_without_team_keeps_default_and_older_children_validate(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    exchange, _, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "default")
    assert response.team_id == fixture.team_id
    assert response.inference_user_id == fixture.account_id
    stored = await fixture.db.query_raw(
        "SELECT external_team_id, external_team_epoch FROM deltallm_platformsession WHERE session_token_hash = $1",
        exchange.identities.sessions.hash_token(response.session_token),
    )
    assert stored == [{"external_team_id": fixture.team_id, "external_team_epoch": 0}]
    # A child written by an older binary has no team and acts in the default team.
    await fixture.db.execute_raw(
        "UPDATE deltallm_platformsession SET external_team_id = NULL, external_team_epoch = NULL WHERE session_token_hash = $1",
        exchange.identities.sessions.hash_token(response.session_token),
    )
    context = await context_for(fixture, response)
    assert context is not None and context.external_workspace.team_id == fixture.team_id
    renewed = await exchange.exchange(proof(fixture), "renew-older-child")
    assert renewed.session_generation == 2 and renewed.team_id == fixture.team_id


async def test_granted_team_creates_one_runtime_user_and_reuses_it(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    team_b = await attach_and_grant(fixture)
    exchange, _, _ = services(fixture)
    first = await exchange.exchange(
        proof(fixture, team_id=team_b, external_session_id="tab-b"), "first-b"
    )
    assert first.team_id == team_b and first.account_id == fixture.account_id
    assert first.inference_user_id != fixture.account_id
    runtime = await fixture.db.query_raw(
        "SELECT team_id, user_email, blocked FROM deltallm_usertable WHERE user_id = $1",
        first.inference_user_id,
    )
    assert runtime == [{"team_id": team_b, "user_email": None, "blocked": False}]
    context = await context_for(fixture, first)
    assert context.external_workspace.team_id == team_b
    assert context.external_workspace.inference_user_id == first.inference_user_id
    assert context.team_memberships == [{"team_id": team_b, "role": "team_developer"}]
    assert has_scoped_permission(context, "team.read", team_id=team_b)
    assert not has_scoped_permission(context, "team.read", team_id=fixture.team_id)
    again = await exchange.exchange(
        proof(fixture, team_id=team_b, external_session_id="tab-b-2"), "second-b"
    )
    assert again.inference_user_id == first.inference_user_id
    count = await fixture.db.query_raw(
        "SELECT count(*)::int AS count FROM deltallm_usertable WHERE team_id = $1", team_b
    )
    assert count == [{"count": 1}]
    # Default-team and team-B sessions of one person stay valid together.
    default = await exchange.exchange(proof(fixture), "default")
    assert (await context_for(fixture, default)).external_workspace.team_id == fixture.team_id
    assert (await context_for(fixture, first)) is not None


async def test_unusable_team_selection_fails_with_the_generic_error(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    exchange, _, _ = services(fixture)
    admin = teams_admin(fixture)
    loose = await add_team(fixture, "loose")
    attached = await add_team(fixture, "attached")
    await admin.attach(
        fixture.binding_id, attached, request=version(0), correlation_id="a", approved_by="op"
    )
    detached = await attach_and_grant(fixture, "detached")
    await admin.detach(
        fixture.binding_id, detached, request=version(0), correlation_id="d", approved_by="op"
    )
    foreign_org = f"foreign-{uuid4().hex}"
    await fixture.db.execute_raw(
        "INSERT INTO deltallm_organizationtable (id, organization_id, updated_at) VALUES ($1, $1, NOW())",
        foreign_org,
    )
    foreign = await add_team(fixture, "foreign", foreign_org)
    try:
        for index, team in enumerate((loose, attached, detached, foreign, "missing-team")):
            with pytest.raises(ExternalAuthError) as denied:
                await exchange.exchange(
                    proof(fixture, team_id=team, external_session_id=f"try-{index}"), "deny"
                )
            assert denied.value.code == "external_access_denied"
        with pytest.raises(ExternalAuthError):
            await admin.attach(
                fixture.binding_id,
                foreign,
                request=version(0),
                correlation_id="x",
                approved_by="op",
            )
    finally:
        await fixture.db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", foreign)
        await fixture.db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", foreign_org
        )


async def test_one_parent_serves_one_team(external_database: ExternalDatabase):
    fixture = external_database
    await enable(fixture)
    team_b = await attach_and_grant(fixture)
    exchange, _, _ = services(fixture)
    await exchange.exchange(proof(fixture), "default")
    with pytest.raises(ExternalAuthError) as denied:
        await exchange.exchange(proof(fixture, team_id=team_b), "switch-on-same-parent")
    assert denied.value.code == "external_reauthentication_required"


async def test_grant_removal_ends_team_access_and_a_new_grant_restores_it(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    team_b = await attach_and_grant(fixture)
    exchange, _, _ = services(fixture)
    in_b = await exchange.exchange(proof(fixture, team_id=team_b, external_session_id="tab-b"), "b")
    in_default = await exchange.exchange(proof(fixture), "default")
    admin = teams_admin(fixture)
    with pytest.raises(ExternalAuthError) as stale:
        await admin.revoke(
            fixture.binding_id,
            team_b,
            fixture.subject_id,
            request=version(5),
            correlation_id="r",
            approved_by="op",
        )
    assert stale.value.status_code == 409
    removed = await admin.revoke(
        fixture.binding_id,
        team_b,
        fixture.subject_id,
        request=version(0),
        correlation_id="r",
        approved_by="op",
    )
    assert removed.state == "removed" and removed.epoch == 1
    assert await context_for(fixture, in_b) is None
    assert (await context_for(fixture, in_default)).external_workspace.team_id == fixture.team_id
    blocked = await fixture.db.query_raw(
        "SELECT blocked FROM deltallm_usertable WHERE user_id = $1", in_b.inference_user_id
    )
    assert blocked == [{"blocked": True}]
    assert (
        await fixture.db.query_raw(
            "SELECT 1 FROM deltallm_teammembership WHERE account_id = $1 AND team_id = $2",
            fixture.account_id,
            team_b,
        )
        == []
    )
    outbox = await fixture.db.query_raw(
        "SELECT scope_type FROM deltallm_cacheinvalidationoutbox WHERE scope_id = $1 AND reason = 'external_team_grant_removed'",
        in_b.inference_user_id,
    )
    assert outbox == [{"scope_type": "user"}]
    with pytest.raises(ExternalAuthError):
        await exchange.exchange(proof(fixture, team_id=team_b, external_session_id="tab-b2"), "b2")
    restored = await admin.grant(
        fixture.binding_id,
        team_b,
        request=grant_request(fixture, 1),
        correlation_id="g2",
        approved_by="op",
    )
    assert restored.state == "active" and restored.runtime_user_id == in_b.inference_user_id
    back = await exchange.exchange(
        proof(fixture, team_id=team_b, external_session_id="tab-b3"), "b3"
    )
    assert back.inference_user_id == in_b.inference_user_id
    assert await context_for(fixture, back) is not None
    assert await context_for(fixture, in_b) is None
    unblocked = await fixture.db.query_raw(
        "SELECT blocked FROM deltallm_usertable WHERE user_id = $1", in_b.inference_user_id
    )
    assert unblocked == [{"blocked": False}]


async def test_detach_and_team_deletion_retire_grants_but_not_the_default_team(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    exchange, _, _ = services(fixture)
    admin = teams_admin(fixture)
    team_b = await attach_and_grant(fixture, "b")
    team_c = await attach_and_grant(fixture, "c")
    in_b = await exchange.exchange(proof(fixture, team_id=team_b, external_session_id="b"), "b")
    in_c = await exchange.exchange(proof(fixture, team_id=team_c, external_session_id="c"), "c")
    in_default = await exchange.exchange(proof(fixture), "default")
    detached = await admin.detach(
        fixture.binding_id, team_b, request=version(0), correlation_id="d", approved_by="op"
    )
    assert detached.state == "detached"
    assert await context_for(fixture, in_b) is None
    # Delete team C the way the team endpoint does, in one transaction.
    async with fixture.db.tx() as tx:
        await tx.execute_raw("DELETE FROM deltallm_teammembership WHERE team_id = $1", team_c)
        await tx.execute_raw(
            "UPDATE deltallm_usertable SET team_id = NULL, updated_at = NOW() WHERE team_id = $1",
            team_c,
        )
        await tx.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", team_c)
    assert await context_for(fixture, in_c) is None
    states = await fixture.db.query_raw(
        """SELECT g.team_id, g.state AS grant_state, bt.state AS attachment_state
        FROM deltallm_externalauthsubjectteam g JOIN deltallm_externalauthbindingteam bt
          ON bt.binding_id = g.binding_id AND bt.team_id = g.team_id ORDER BY g.team_id""",
    )
    assert [row for row in states if row["team_id"] in (team_b, team_c)] == [
        {"team_id": team_b, "grant_state": "removed", "attachment_state": "detached"},
        {"team_id": team_c, "grant_state": "removed", "attachment_state": "detached"},
    ]
    assert (await context_for(fixture, in_default)).external_workspace.team_id == fixture.team_id
    binding = await fixture.db.query_raw(
        "SELECT state FROM deltallm_externalauthbinding WHERE binding_id = $1", fixture.binding_id
    )
    assert binding == [{"state": "active"}]


async def test_invite_before_first_sign_in_provisions_default_and_granted_team(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    team_b = await add_team(fixture, "b")
    admin = teams_admin(fixture)
    await admin.attach(
        fixture.binding_id, team_b, request=version(0), correlation_id="a", approved_by="op"
    )
    grant = await admin.grant(
        fixture.binding_id,
        team_b,
        request=grant_request(fixture),
        correlation_id="g",
        approved_by="op",
    )
    assert grant.runtime_user_id is None
    subject = await fixture.db.query_raw(
        "SELECT state, account_id FROM deltallm_externalauthsubject WHERE subject_id = $1",
        grant.subject_id,
    )
    assert subject == [{"state": "pending", "account_id": None}]
    exchange, _, _ = services(fixture)
    response = await exchange.exchange(proof(fixture, team_id=team_b), "first-in-b")
    assert response.team_id == team_b
    memberships = await fixture.db.query_raw(
        "SELECT team_id FROM deltallm_teammembership WHERE account_id = $1 ORDER BY team_id",
        response.account_id,
    )
    assert sorted(row["team_id"] for row in memberships) == sorted([fixture.team_id, team_b])
    listed = await admin.list_subjects(fixture.binding_id, team_id=team_b, after=None, limit=10)
    assert [item.subject_id for item in listed] == [grant.subject_id]
    assert listed[0].team_grants[0].runtime_user_id == response.inference_user_id
    views = await admin.list_teams(fixture.binding_id, after=None, limit=10)
    assert [(view.team_id, view.is_default) for view in views] == [
        (fixture.team_id, True),
        (team_b, False),
    ]


async def test_operator_team_api_refuses_the_default_team_and_foreign_issuers(
    external_database: ExternalDatabase,
):
    fixture = external_database
    admin = teams_admin(fixture)
    for operation in (
        admin.attach(
            fixture.binding_id,
            fixture.team_id,
            request=version(0),
            correlation_id="a",
            approved_by="op",
        ),
        admin.detach(
            fixture.binding_id,
            fixture.team_id,
            request=version(0),
            correlation_id="d",
            approved_by="op",
        ),
        admin.grant(
            fixture.binding_id,
            fixture.team_id,
            request=grant_request(fixture),
            correlation_id="g",
            approved_by="op",
        ),
    ):
        with pytest.raises(ExternalAuthError) as refused:
            await operation
        assert refused.value.status_code == 409
    team_b = await add_team(fixture, "b")
    await admin.attach(
        fixture.binding_id, team_b, request=version(0), correlation_id="a", approved_by="op"
    )
    again = await admin.attach(
        fixture.binding_id, team_b, request=version(0), correlation_id="a2", approved_by="op"
    )
    assert again.state == "active" and again.version == 0
    with pytest.raises(ExternalAuthError):
        await admin.grant(
            fixture.binding_id,
            team_b,
            request=ExternalTeamGrantRequest(
                expected_version=0,
                reason="approved test change",
                identity_issuer="https://other-issuer.example.com",
                subject="someone",
            ),
            correlation_id="g",
            approved_by="op",
        )


async def test_database_rejects_memberships_and_runtime_users_outside_grants(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    team_b = await attach_and_grant(fixture)
    team_x = await add_team(fixture, "x")
    with pytest.raises(Exception, match="dedicated"):
        await fixture.db.execute_raw(
            "INSERT INTO deltallm_teammembership (membership_id, account_id, team_id, role, updated_at) VALUES (gen_random_uuid(), $1, $2, 'team_developer', NOW())",
            fixture.account_id,
            team_x,
        )
    exchange, _, _ = services(fixture)
    response = await exchange.exchange(proof(fixture, team_id=team_b, external_session_id="b"), "b")
    with pytest.raises(Exception, match="outside its workspace"):
        await fixture.db.execute_raw(
            "UPDATE deltallm_usertable SET team_id = $2 WHERE user_id = $1",
            response.inference_user_id,
            team_x,
        )
    with pytest.raises(Exception, match="tenant association"):
        await fixture.db.execute_raw(
            "INSERT INTO deltallm_externalauthbindingteam (binding_id, team_id, updated_at) VALUES ($1, $2, NOW())",
            fixture.binding_id,
            fixture.team_id,
        )


async def test_asset_check_accepts_keys_of_granted_team_runtime_users(
    external_database: ExternalDatabase,
):
    fixture = external_database
    await enable(fixture)
    team_b = await attach_and_grant(fixture)
    exchange, _, _ = services(fixture)
    response = await exchange.exchange(proof(fixture, team_id=team_b, external_session_id="b"), "b")

    async def insert_key(user_id: str) -> None:
        await fixture.db.execute_raw(
            "INSERT INTO deltallm_verificationtoken (id, token, user_id, team_id, owner_account_id, models, updated_at) VALUES ($1, $1, $2, $3, $4, ARRAY[]::text[], NOW())",
            uuid4().hex,
            user_id,
            team_b,
            fixture.account_id,
        )

    async def eligible() -> bool:
        binding = await ExternalBindingRepository(fixture.db).get(fixture.binding_id)
        state = await ExternalProvisioningRepository(fixture.db).eligible_target(
            account_id=fixture.account_id,
            runtime_user_id=fixture.account_id,
            binding=binding,
            subject_id=fixture.subject_id,
            check_assets=True,
        )
        return state is not None

    await insert_key(response.inference_user_id)
    assert await eligible()
    # The default runtime user cannot own a key in team B.
    await insert_key(fixture.account_id)
    assert not await eligible()


async def test_rollback_preview_counts_live_attachments(external_database: ExternalDatabase):
    fixture = external_database
    await attach_and_grant(fixture)
    counts = await ExternalAuthRollbackRepository(fixture.db).counts()
    assert counts["attached_teams"] >= 1
