from __future__ import annotations

from types import SimpleNamespace

import pytest
from prisma.errors import RawQueryError

from src.api.audit import redact_sensitive
from src.auth.roles import TeamRole
from src.db.identity.key_repository import KeyRecord
from src.models.errors import AuthenticationError
from src.services.identity.keys.key_material import InvalidKeyMaterial, prepare_key_material
from src.services.identity.keys.key_service import KeyService
from tests.test_key_service import InMemoryRepo
from tests.test_ui_key_notifications import _FakeKeyNotificationDB, _FakeNotificationService
from tests.test_ui_self_service_keys import (
    _install_key_db,
    _make_context,
    _self_service_team,
    _set_auth_context,
)

SECRET = "sk-supplied-test-credential-123456"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sk-short",
        "other-prefix-123456",
        123,
        True,
        [],
        {},
        "sk-contains a space",
        "sk-contains\na-newline",
        "sk-秘密-secret-123456",
        "sk-" + "x" * 8190,
    ],
)
def test_invalid_key_material_does_not_echo_the_input(value):
    with pytest.raises(InvalidKeyMaterial) as failure:
        prepare_key_material(value, hasher=KeyService(InMemoryRepo({})), master_key=None)
    if isinstance(value, str) and value:
        assert value not in str(failure.value)


async def test_exact_supplied_key_uses_the_existing_authentication_path():
    repo = InMemoryRepo({})
    keys = KeyService(repo, salt="supplied-test-salt")
    material = prepare_key_material(SECRET, hasher=keys, master_key=None)
    assert SECRET not in repr(material)
    repo.records[material.token_hash] = KeyRecord(token=material.token_hash, team_id="team-1")
    assert (await keys.validate_key(SECRET)).team_id == "team-1"
    with pytest.raises(AuthenticationError):
        await keys.validate_key(SECRET + "-changed")
    with pytest.raises(InvalidKeyMaterial):
        prepare_key_material(SECRET, hasher=keys, master_key=SECRET)


@pytest.mark.parametrize("length", [16, 8192])
def test_supplied_key_length_boundaries(length):
    raw = "sk-" + "x" * (length - 3)
    assert (
        prepare_key_material(raw, hasher=KeyService(InMemoryRepo({})), master_key=None).raw_key
        == raw
    )


@pytest.fixture
def creation(test_app):
    db = _FakeKeyNotificationDB()
    test_app.state.prisma_manager = SimpleNamespace(client=db)
    test_app.state.key_notification_service = _FakeNotificationService()
    test_app.state.settings.master_key = "mk-test"
    return db


async def create(client, **extra):
    return await client.post(
        "/ui/api/keys",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "key_name": "Imported key",
            "team_id": "team-1",
            "owner_account_id": "acct-owner",
            **extra,
        },
    )


@pytest.mark.parametrize("extra", [{}, {"key": None}, {"key": SECRET}])
async def test_creation_keeps_generation_and_exact_supplied_values(
    client, test_app, creation, extra
):
    response = await create(client, **extra)
    assert response.status_code == 200
    body = response.json()
    assert body["raw_key"].startswith("sk-")
    assert body["token"] == test_app.state.key_service.hash_key(body["raw_key"])
    assert body["token"] in creation.keys
    assert response.headers["cache-control"] == "no-store"
    if extra.get("key"):
        assert body["raw_key"] == SECRET
    listed = await client.get("/ui/api/keys", headers={"Authorization": "Bearer mk-test"})
    assert SECRET not in listed.text


@pytest.mark.parametrize(
    "value", ["", "sk-short", "sk-with spaces-123456", {"secret": SECRET}, [SECRET], 42, True]
)
async def test_bad_key_returns_safe_400_without_inserting(client, creation, value):
    response = await create(client, key=value)
    assert response.status_code == 400
    assert SECRET not in response.text
    assert len(creation.keys) == 1


@pytest.mark.parametrize("body", [[{"key": SECRET}], [SECRET], SECRET])
async def test_invalid_body_shape_does_not_echo_secret(client, creation, body):
    response = await client.post(
        "/ui/api/keys",
        headers={"Authorization": "Bearer mk-test"},
        json=body,
    )
    assert SECRET not in response.text
    assert response.status_code == 400
    assert len(creation.keys) == 1


async def test_master_key_value_is_refused(client, test_app, creation):
    test_app.state.settings.master_key = SECRET
    response = await client.post(
        "/ui/api/keys",
        headers={"Authorization": f"Bearer {SECRET}"},
        json={
            "key_name": "No master",
            "team_id": "team-1",
            "owner_account_id": "acct-owner",
            "key": SECRET,
        },
    )
    assert response.status_code == 400
    assert SECRET not in response.text


async def test_database_conflict_returns_generic_409_and_redacts_failure_audit(
    client, test_app, creation
):
    audited = []

    async def record(event, **kwargs):
        audited.extend(payload.content_json for payload in kwargs["payloads"])

    async def conflict(*args):
        raise RawQueryError(
            {
                "user_facing_error": {
                    "error_code": "P2010",
                    "meta": {"code": "PKEY1", "message": "API key value is unavailable"},
                }
            }
        )

    creation.execute_raw = conflict
    test_app.state.audit_service = SimpleNamespace(record_event_sync=record)
    response = await create(client, key=SECRET)
    assert response.status_code == 409
    assert response.json()["detail"] == "API key value is unavailable"
    assert audited and SECRET not in str(audited)


async def test_success_audit_masks_request_and_response_secrets(client, test_app, creation):
    audited = []

    async def record(event, **kwargs):
        audited.extend(payload.content_json for payload in kwargs["payloads"])

    test_app.state.audit_service = SimpleNamespace(record_event_sync=record)
    response = await create(client, key=SECRET)
    assert response.status_code == 200
    assert audited and SECRET not in str(audited)
    assert redact_sensitive({"nested": {"key": SECRET, "new_key": SECRET}}) == {
        "nested": {"key": "***REDACTED***", "new_key": "***REDACTED***"}
    }


@pytest.mark.parametrize(
    "team_id,budget,expected",
    [
        ("team-sandbox", 4, 200),
        ("team-sandbox", 6, 400),
        ("team-foreign", 4, 403),
    ],
)
async def test_supplied_key_keeps_self_service_owner_budget_and_tenant_rules(
    client,
    test_app,
    monkeypatch,
    team_id,
    budget,
    expected,
):
    db = _install_key_db(
        test_app,
        {},
        teams={
            "team-sandbox": _self_service_team(self_service_require_expiry=False),
            "team-foreign": _self_service_team(team_id="team-foreign"),
        },
        users={
            "account-1": {
                "user_id": "account-1",
                "user_email": "account-1@example.com",
                "team_id": "team-sandbox",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="account-1",
            team_memberships=[
                {"team_id": "team-sandbox", "role": TeamRole.DEVELOPER},
            ],
        ),
    )
    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "Personal import",
            "team_id": team_id,
            "key": SECRET,
            "max_budget": budget,
            "owner_account_id": "someone-else",
        },
    )
    assert response.status_code == expected
    if expected == 200:
        assert response.json()["owner_account_id"] == "account-1"
        assert response.json()["user_id"] == "account-1"
    else:
        assert not db.keys
