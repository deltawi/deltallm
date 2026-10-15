from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.auth.roles import OrganizationRole, PlatformRole
from src.config import AppConfig
from src.models.platform_auth import PlatformAuthContext
from src.services.identity.sso_group_access import (
    SSOGroupAccessChange,
    SSOGroupAccessDiff,
)
from src.services.identity.sso_group_sync import SSOGroupSyncResult
from tests.test_auth import (
    _StubIdentityService,
    _StubSelfRegistrationProvisioner,
    _StubSSOHandler,
    _self_registration_app_config,
)
from src.services.identity.sso_state_store import SSOStateStore

pytestmark = pytest.mark.asyncio

SYNC = {
    "enabled": True,
    "claim": "deltallm_role",
    "admin_values": ["admin"],
    "mappings": [{"value": "team-ml", "team_id": "team_ml", "team_role": "team_developer"}],
}


class _ClaimsHandler(_StubSSOHandler):
    def __init__(self, claims: dict[str, object] | None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.claims = claims

    async def handle_callback(self, code: str, code_verifier: str | None = None):
        payload = await super().handle_callback(code, code_verifier)
        payload["claim_sources"] = (self.claims,) if self.claims is not None else ()
        return payload


class _GroupIdentityService(_StubIdentityService):
    def __init__(self, group_sync: SSOGroupSyncResult | None = None) -> None:
        super().__init__()
        self.group_sync = group_sync
        self.existing_calls: list[dict[str, Any]] = []

    async def create_sso_login_for_existing_account(self, **kwargs: Any):
        self.existing_calls.append(dict(kwargs))
        kwargs.pop("group_access", None)
        return await super().create_sso_login_for_existing_account(**kwargs)

    async def create_login_result_for_account(self, account_id: str):
        login = await super().create_login_result_for_account(account_id)
        if login is not None:
            login.group_sync = self.group_sync
        return login


def _app_config(*, self_registration: bool, **sync: object) -> AppConfig:
    config = _self_registration_app_config(enabled=self_registration)
    data = config.model_dump()
    data["general_settings"]["sso_group_sync"] = {**SYNC, **sync}
    return AppConfig.model_validate(data)


async def _callback(client, test_app, *, claims: dict[str, object] | None, state: str):
    test_app.state.sso_auth_handler = _ClaimsHandler(claims, email="new@example.com")
    test_app.state.sso_state_store = SSOStateStore(
        redis_client=test_app.state.redis, ttl_seconds=600
    )
    login = await client.get("/auth/login", params={"state": state})
    assert login.status_code == 200
    return await client.get("/auth/callback", params={"code": "oauth-code", "state": state})


async def test_mapped_value_skips_sandbox_and_admin_value_grants_admin(client, test_app):
    identity = _GroupIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _app_config(self_registration=True)

    response = await _callback(
        client, test_app, claims={"deltallm_role": ["team-ml", "admin"]}, state="mapped"
    )

    assert response.status_code == 302
    assert provisioner.calls == []
    upsert = identity.legacy_upserts[0]
    assert upsert["is_platform_admin"] is True
    assert upsert["group_access"].team_roles == {"team_ml": "team_developer"}


async def test_missing_claim_skips_sync_without_denying(client, test_app):
    identity = _GroupIdentityService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = _StubSelfRegistrationProvisioner()
    test_app.state.app_config = _app_config(self_registration=False)

    response = await _callback(client, test_app, claims={"email": "x"}, state="missing")

    assert response.status_code == 302
    assert "group_access" not in identity.legacy_upserts[0]


@pytest.mark.parametrize("claims", [{"email": "x"}, {"deltallm_role": ["unmapped"]}])
async def test_require_mapped_value_denies_before_account_changes(client, test_app, claims):
    identity = _GroupIdentityService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = _StubSelfRegistrationProvisioner()
    test_app.state.app_config = _app_config(self_registration=False, require_mapped_value=True)

    response = await _callback(client, test_app, claims=claims, state="denied")

    assert response.status_code == 403
    assert "no mapped DeltaLLM group" in response.json()["detail"]
    assert identity.legacy_upserts == []
    assert identity.login_results == []


async def test_existing_account_gets_sync_and_deleted_keys_are_invalidated(client, test_app):
    removed = SSOGroupAccessChange("team", "remove", "team_support", "team_viewer", None)
    result = SSOGroupSyncResult(
        diff=SSOGroupAccessDiff(changes=(removed,), skipped=()),
        revoked_key_hashes=("hash-1",),
    )
    identity = _GroupIdentityService(group_sync=result)
    identity.add_account(account_id="acct-1", email="new@example.com")
    identity.identities[("oidc", "subject-1")] = "acct-1"
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = _StubSelfRegistrationProvisioner()
    test_app.state.app_config = _app_config(self_registration=True, remove_unlisted=True)
    key_service = SimpleNamespace(invalidate_key_cache_by_hash=AsyncMock())
    test_app.state.key_service = key_service

    response = await _callback(client, test_app, claims={"deltallm_role": []}, state="existing")

    assert response.status_code == 302
    plan = identity.existing_calls[0]["group_access"]
    assert plan.remove_unlisted is True and plan.has_grants is False
    key_service.invalidate_key_cache_by_hash.assert_awaited_once_with("hash-1")


# --- Admin API ---------------------------------------------------------------


class _TargetsDB:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        normalized = " ".join(query.split())
        if "FROM deltallm_teamtable t" in normalized:
            return [
                {
                    "team_id": team_id,
                    "team_alias": "ML Platform",
                    "organization_id": "org_acme",
                    "organization_active": True,
                    "external_managed": team_id == "team_cloud",
                }
                for team_id in params[0]
                if team_id in {"team_ml", "team_cloud"}
            ]
        if "FROM deltallm_organizationtable" in normalized:
            return [
                {"organization_id": org, "organization_name": "Acme", "active": True}
                for org in params[0]
                if org == "org_acme"
            ]
        if "FROM deltallm_platformaccount WHERE email" in normalized:
            return [{"account_id": "acct-1"}] if params[0] == "priya@acme.com" else []
        if "SELECT account_id, role FROM deltallm_platformaccount" in normalized:
            return [{"account_id": "acct-1", "role": "org_user"}]
        if "FROM deltallm_organizationmembership" in normalized:
            return [{"kind": "team", "target_id": "team_ml", "role": "team_viewer"}]
        return []

    async def execute_raw(self, query: str, *params: Any) -> int:
        raise AssertionError("the admin API must not write memberships")


def _context(role: str = PlatformRole.ORG_USER, org_role: str | None = None) -> PlatformAuthContext:
    memberships = [{"organization_id": "org_acme", "role": org_role}] if org_role else []
    return PlatformAuthContext(
        account_id="acc-1",
        email="user@example.com",
        role=role,
        organization_memberships=memberships,
        team_memberships=[],
    )


def _admin_api(test_app, monkeypatch, context: PlatformAuthContext | None) -> AsyncMock:
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)
    monkeypatch.setattr(
        "src.middleware.platform_auth.get_platform_auth_context", lambda request: context
    )
    test_app.state.prisma_manager = SimpleNamespace(client=_TargetsDB())
    test_app.state.app_config = _app_config(self_registration=False)
    update = AsyncMock()
    test_app.state.dynamic_config_manager = SimpleNamespace(update_config=update)
    monkeypatch.setattr(
        "src.api.admin.endpoints.sso_group_sync.emit_admin_mutation_audit", AsyncMock()
    )
    return update


@pytest.mark.parametrize(
    "context",
    [_context(), _context(org_role=OrganizationRole.OWNER), _context(org_role="org_admin")],
)
async def test_admin_api_is_platform_admin_only(client, test_app, monkeypatch, context):
    update = _admin_api(test_app, monkeypatch, context)
    assert (await client.get("/ui/api/settings/sso-group-sync")).status_code == 403
    put = await client.put("/ui/api/settings/sso-group-sync", json=SYNC)
    assert put.status_code == 403
    preview = await client.post(
        "/ui/api/settings/sso-group-sync/preview", json={"settings": SYNC, "values": []}
    )
    assert preview.status_code == 403
    update.assert_not_awaited()


async def test_admin_api_reads_saves_and_rejects_unavailable_targets(client, test_app, monkeypatch):
    update = _admin_api(test_app, monkeypatch, _context(PlatformRole.ADMIN))

    current = await client.get("/ui/api/settings/sso-group-sync")
    assert current.status_code == 200
    body = current.json()
    assert body["settings"]["claim"] == "deltallm_role"
    assert body["targets"] == [
        {
            "id": "team_ml",
            "kind": "team",
            "name": "ML Platform",
            "organization_id": "org_acme",
            "organization_name": "Acme",
            "status": "ok",
        }
    ]

    saved = await client.put(
        "/ui/api/settings/sso-group-sync", json={**SYNC, "remove_unlisted": True}
    )
    assert saved.status_code == 200
    update.assert_awaited_once()
    assert update.await_args.args[0]["general_settings"]["sso_group_sync"]["remove_unlisted"]

    for team_id in ("team_missing", "team_cloud"):
        bad = {
            **SYNC,
            "mappings": [{"value": "x", "team_id": team_id, "team_role": "team_viewer"}],
        }
        rejected = await client.put("/ui/api/settings/sso-group-sync", json=bad)
        assert rejected.status_code == 400
        assert team_id in rejected.json()["detail"]
    invalid = await client.put("/ui/api/settings/sso-group-sync", json={"claim": "bad claim"})
    assert invalid.status_code == 422
    assert update.await_count == 1


async def test_admin_preview_shows_changes_without_writing(client, test_app, monkeypatch):
    _admin_api(test_app, monkeypatch, _context(PlatformRole.ADMIN))

    response = await client.post(
        "/ui/api/settings/sso-group-sync/preview",
        json={
            "settings": SYNC,
            "email": "Priya@Acme.com",
            "claims": {"deltallm_role": "team-ml admin"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account_found"] is True
    assert body["matched_values"] == ["admin", "team-ml"]
    assert {(c["kind"], c["action"], c["role_after"]) for c in body["changes"]} == {
        ("platform_role", "update", "platform_admin"),
        ("team", "update", "team_developer"),
        ("organization", "add", "org_member"),
    }

    missing = await client.post(
        "/ui/api/settings/sso-group-sync/preview",
        json={"settings": {**SYNC, "require_mapped_value": True}, "claims": {"email": "x"}},
    )
    assert missing.json()["claim_found"] is False
    assert missing.json()["sign_in_denied"] is True
    assert missing.json()["changes"] == []
