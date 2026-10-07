from __future__ import annotations

from uuid import uuid4

import pytest

from src.db.platform_passwords import PlatformPasswordRepository
from src.services.platform_identity_service import PlatformIdentityService
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_customer_assets import install_customer

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


@pytest.fixture
async def directory_customer(external_database, test_app, client):
    fixture = external_database
    exchange = await install_customer(fixture, test_app, client)
    peer_team = "peer-" + uuid4().hex
    peer_account = "peer-" + uuid4().hex
    await fixture.db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, organization_id, models, max_budget, updated_at) VALUES ($1, $2, ARRAY[]::text[], 42, NOW())",
        peer_team,
        fixture.organization_id,
    )
    try:
        await fixture.db.execute_raw(
            "INSERT INTO deltallm_platformaccount (account_id, email, updated_at) VALUES ($1, $1 || '@example.com', NOW())",
            peer_account,
        )
        await fixture.db.execute_raw(
            "INSERT INTO deltallm_organizationmembership (membership_id, account_id, organization_id, role, updated_at) VALUES (gen_random_uuid(), $1, $2, 'org_member', NOW())",
            peer_account,
            fixture.organization_id,
        )
        await fixture.db.execute_raw(
            "INSERT INTO deltallm_teammembership (membership_id, account_id, team_id, role, updated_at) VALUES (gen_random_uuid(), $1, $2, 'team_developer', NOW())",
            peer_account,
            peer_team,
        )
        yield fixture, exchange, peer_team, peer_account
    finally:
        await fixture.db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1", peer_account
        )
        await fixture.db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", peer_team)


async def test_customer_lists_only_bound_team_in_shared_organization(directory_customer, client):
    fixture, _, peer_team, _ = directory_customer
    for query in ("", "?organization_id=" + fixture.organization_id, "?search=" + peer_team):
        response = await client.get("/ui/api/teams" + query)
        assert response.status_code == 200
        expected = [] if "search=" in query else [fixture.team_id]
        assert [row["team_id"] for row in response.json()["data"]] == expected
        assert response.json()["pagination"]["total"] == len(expected)
    response = await client.get(f"/ui/api/organizations/{fixture.organization_id}/teams")
    assert response.status_code == 200
    assert [row["team_id"] for row in response.json()] == [fixture.team_id]
    own = await client.get(f"/ui/api/teams/{fixture.team_id}")
    assert own.status_code == 200
    assert own.json()["capabilities"]["edit"] is False
    outside = await client.get("/ui/api/teams", params={"organization_id": "unregistered"})
    assert outside.status_code == 200
    assert outside.json()["data"] == [] and outside.json()["pagination"]["total"] == 0


async def test_operator_team_and_directory_access_is_preserved(directory_customer, client):
    fixture, exchange, peer_team, peer_account = directory_customer
    token = await exchange.identities.sessions.create(
        account_id=fixture.account_id, mfa_verified=False
    )
    client.cookies.set("deltallm_session", token)
    response = await client.get("/ui/api/teams")
    assert response.status_code == 200
    assert {row["team_id"] for row in response.json()["data"]} == {fixture.team_id, peer_team}
    response = await client.get(f"/ui/api/organizations/{fixture.organization_id}/teams")
    assert response.status_code == 200
    assert {row["team_id"] for row in response.json()} == {fixture.team_id, peer_team}
    response = await client.get(f"/ui/api/organizations/{fixture.organization_id}/members")
    assert response.status_code == 200
    assert {row["account_id"] for row in response.json()} == {fixture.account_id, peer_account}
    response = await client.get(
        f"/ui/api/organizations/{fixture.organization_id}/member-candidates",
        params={"search": peer_account},
    )
    assert response.status_code == 200
    assert [row["account_id"] for row in response.json()] == [peer_account]
    response = await client.get(f"/ui/api/teams/{peer_team}")
    assert response.status_code == 200
    await fixture.db.execute_raw(
        "UPDATE deltallm_teammembership SET role = 'team_admin' WHERE account_id = $1",
        fixture.account_id,
    )
    response = await client.put(f"/ui/api/teams/{fixture.team_id}", json={"max_budget": 13})
    assert response.status_code == 200 and response.json()["max_budget"] == 13


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "/members",
        "/member-candidates?search=peer@example.com",
        "/asset-visibility",
        "/asset-access",
    ],
)
async def test_customer_cannot_read_peer_team_or_disclose_its_existence(
    directory_customer, client, suffix
):
    _, _, peer_team, _ = directory_customer
    peer = await client.get(f"/ui/api/teams/{peer_team}{suffix}")
    missing = await client.get(f"/ui/api/teams/missing-{uuid4().hex}{suffix}")
    assert peer.status_code == missing.status_code == 404
    assert peer.json() == missing.json()


@pytest.mark.parametrize("suffix", ["/members", "/member-candidates"])
async def test_customer_cannot_read_organization_directory(directory_customer, client, suffix):
    fixture, _, _, peer_account = directory_customer
    response = await client.get(
        f"/ui/api/organizations/{fixture.organization_id}{suffix}",
        params={"search": peer_account},
    )
    assert response.status_code == 403 and peer_account not in response.text


@pytest.mark.parametrize(
    "org_role,team_role",
    [
        ("org_owner", "team_developer"),
        ("org_member", "team_admin"),
    ],
)
async def test_customer_administrator_roles_cannot_mutate_team(
    directory_customer, client, org_role, team_role
):
    fixture, _, _, peer_account = directory_customer
    await fixture.db.execute_raw(
        "UPDATE deltallm_organizationmembership SET role = $2 WHERE account_id = $1",
        fixture.account_id,
        org_role,
    )
    await fixture.db.execute_raw(
        "UPDATE deltallm_teammembership SET role = $2 WHERE account_id = $1",
        fixture.account_id,
        team_role,
    )
    before = await fixture.db.query_raw(
        "SELECT * FROM deltallm_teamtable WHERE team_id = $1", fixture.team_id
    )
    members = await fixture.db.query_raw(
        "SELECT * FROM deltallm_teammembership WHERE team_id = $1", fixture.team_id
    )
    for method, suffix, payload in (
        ("PUT", "", {"max_budget": 9999, "self_service_keys_enabled": True}),
        ("PUT", "/asset-access", {}),
        ("POST", "/members", {"account_id": peer_account, "role": "team_admin"}),
        ("DELETE", "/members/" + fixture.account_id, None),
        ("DELETE", "", None),
    ):
        response = await client.request(
            method, f"/ui/api/teams/{fixture.team_id}{suffix}", json=payload
        )
        assert response.status_code == 403, response.text
    assert (
        await fixture.db.query_raw(
            "SELECT * FROM deltallm_teamtable WHERE team_id = $1", fixture.team_id
        )
        == before
    )
    assert (
        await fixture.db.query_raw(
            "SELECT * FROM deltallm_teammembership WHERE team_id = $1", fixture.team_id
        )
        == members
    )


async def test_new_console_customer_cannot_create_independent_password_login(
    directory_customer, client
):
    fixture, exchange, _, _ = directory_customer
    password = "test-only-new-password-123"
    response = await client.post("/auth/internal/change-password", json={"new_password": password})
    assert response.status_code == 400
    stored = await fixture.db.query_raw(
        "SELECT password_hash FROM deltallm_platformaccount WHERE account_id = $1",
        fixture.account_id,
    )
    assert stored == [{"password_hash": None}]
    assert (
        await exchange.identities.login_internal(fixture.account_id + "@example.com", password)
        is None
    )


async def test_linked_password_change_requires_proof_and_keeps_external_session(
    directory_customer, client
):
    fixture, exchange, _, _ = directory_customer
    old_password, new_password = "test-only-old-password-123", "test-only-new-password-123"
    await exchange.identities.set_password(account_id=fixture.account_id, new_password=old_password)
    await fixture.db.execute_raw(
        "UPDATE deltallm_platformaccount SET force_password_change = true WHERE account_id = $1",
        fixture.account_id,
    )
    wrong = await client.post(
        "/auth/internal/change-password",
        json={"new_password": new_password, "current_password": "wrong-password"},
    )
    assert wrong.status_code == 400
    response = await client.post(
        "/auth/internal/change-password",
        json={"new_password": new_password, "current_password": old_password},
    )
    assert response.status_code == 200 and "set-cookie" not in response.headers
    context = await exchange.identities.get_context_for_session(
        client.cookies.get("deltallm_session")
    )
    assert context.external_workspace is not None and not context.force_password_change
    stored = await PlatformPasswordRepository(fixture.db).get(fixture.account_id)
    assert exchange.identities._verify_password(new_password, stored.password_hash)
    assert not exchange.identities._verify_password(old_password, stored.password_hash)


async def test_external_password_change_cannot_bypass_linked_mfa(directory_customer, client):
    fixture, exchange, _, _ = directory_customer
    old_password = "test-only-old-password-123"
    await exchange.identities.set_password(account_id=fixture.account_id, new_password=old_password)
    await fixture.db.execute_raw(
        "UPDATE deltallm_platformaccount SET mfa_enabled = true, mfa_secret = 'JBSWY3DPEHPK3PXP' WHERE account_id = $1",
        fixture.account_id,
    )
    before = await PlatformPasswordRepository(fixture.db).get(fixture.account_id)
    response = await client.post(
        "/auth/internal/change-password",
        json={"new_password": "test-only-new-password-123", "current_password": old_password},
    )
    assert response.status_code == 403
    assert await PlatformPasswordRepository(fixture.db).get(fixture.account_id) == before


async def test_concurrent_password_replacement_cannot_overwrite_newer_password(external_database):
    fixture = external_database
    identity = PlatformIdentityService(fixture.db, salt="test-password-salt")
    await identity.set_password(
        account_id=fixture.account_id, new_password="test-only-first-password"
    )
    repository = PlatformPasswordRepository(fixture.db)
    before = await repository.get(fixture.account_id)
    await identity.with_db(fixture.writer).set_password(
        account_id=fixture.account_id, new_password="test-only-second-password"
    )
    assert not await repository.replace(
        fixture.account_id, expected_hash=before.password_hash, password_hash="stale-proof"
    )
    after = await repository.get(fixture.account_id)
    assert identity._verify_password("test-only-second-password", after.password_hash)
