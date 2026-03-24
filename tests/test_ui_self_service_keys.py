from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.api.admin.endpoints.common import get_auth_scope
from src.auth.roles import OrganizationRole, Permission, TeamRole
from src.models.platform_auth import PlatformAuthContext


class _FakeKeyDB:
    def __init__(
        self,
        keys: dict[str, dict[str, Any]],
        *,
        teams: dict[str, dict[str, Any]] | None = None,
        account_ids: set[str] | None = None,
    ) -> None:
        self.keys = dict(keys)
        self.teams = dict(teams or {})
        self.account_ids = set(account_ids or set())

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        token_hash = str(params[0]) if params else ""

        if "from deltallm_verificationtoken vt" in normalized and "left join deltallm_usertable" in normalized:
            row = self.keys.get(token_hash)
            if row is None:
                return []
            return [
                {
                    "token": token_hash,
                    "user_id": row.get("user_id"),
                    "team_id": row.get("team_id"),
                    "organization_id": row.get("organization_id"),
                }
            ]

        if normalized.startswith("select owner_account_id from deltallm_verificationtoken"):
            row = self.keys.get(token_hash)
            if row is None:
                return []
            return [{"owner_account_id": row.get("owner_account_id")}]

        if (
            "from deltallm_teamtable" in normalized
            and "where team_id = $1" in normalized
            and "team_alias" in normalized
        ):
            team_id = token_hash
            row = self.teams.get(team_id)
            return [dict(row)] if row is not None else []

        if normalized.startswith("select self_service_keys_enabled"):
            team_id = token_hash
            row = self.teams.get(team_id)
            return [dict(row)] if row is not None else []

        if normalized.startswith("select rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit from deltallm_teamtable"):
            team_id = token_hash
            row = self.teams.get(team_id)
            return [dict(row)] if row is not None else []

        if normalized.startswith("select count(*) as cnt from deltallm_verificationtoken where team_id = $1 and owner_account_id = $2"):
            team_id = str(params[0])
            owner_account_id = str(params[1])
            count = sum(
                1
                for row in self.keys.values()
                if str(row.get("team_id") or "") == team_id
                and str(row.get("owner_account_id") or "") == owner_account_id
            )
            return [{"cnt": count}]

        if normalized.startswith("select account_id from deltallm_platformaccount"):
            account_id = token_hash
            return [{"account_id": account_id}] if account_id in self.account_ids else []

        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if normalized.startswith("delete from deltallm_verificationtoken where token = $1"):
            token_hash = str(params[0])
            return 1 if self.keys.pop(token_hash, None) is not None else 0
        if normalized.startswith("insert into deltallm_verificationtoken"):
            (
                token_hash,
                key_name,
                user_id,
                team_id,
                owner_account_id,
                owner_service_account_id,
                max_budget,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                expires,
            ) = params
            team = self.teams.get(str(team_id), {})
            self.keys[str(token_hash)] = {
                "token": token_hash,
                "key_name": key_name,
                "user_id": user_id,
                "team_id": team_id,
                "organization_id": team.get("organization_id"),
                "owner_account_id": owner_account_id,
                "owner_service_account_id": owner_service_account_id,
                "max_budget": max_budget,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "rph_limit": rph_limit,
                "rpd_limit": rpd_limit,
                "tpd_limit": tpd_limit,
                "expires": expires,
            }
            return 1
        return 0


def _set_auth_context(monkeypatch: pytest.MonkeyPatch, context: PlatformAuthContext | None) -> None:
    monkeypatch.setattr("src.middleware.platform_auth.get_platform_auth_context", lambda request: context)
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)
    monkeypatch.setattr("src.api.admin.endpoints.keys.get_platform_auth_context", lambda request: context)


def _make_context(
    *,
    account_id: str,
    org_memberships: list[dict[str, Any]] | None = None,
    team_memberships: list[dict[str, Any]] | None = None,
) -> PlatformAuthContext:
    return PlatformAuthContext(
        account_id=account_id,
        email=f"{account_id}@example.com",
        role="platform_user",
        organization_memberships=org_memberships or [],
        team_memberships=team_memberships or [],
    )


def _fake_request() -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_config=None,
                settings=SimpleNamespace(master_key=None),
            )
        )
    )


def _install_key_db(
    test_app: Any,
    keys: dict[str, dict[str, Any]],
    *,
    teams: dict[str, dict[str, Any]] | None = None,
    account_ids: set[str] | None = None,
) -> _FakeKeyDB:
    fake_db = _FakeKeyDB(keys, teams=teams, account_ids=account_ids)
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    return fake_db


def test_get_auth_scope_tracks_effective_permissions_beyond_endpoint_permissions(monkeypatch: pytest.MonkeyPatch):
    context = _make_context(
        account_id="acct-admin",
        org_memberships=[{"organization_id": "org-1", "role": OrganizationRole.ADMIN}],
    )
    _set_auth_context(monkeypatch, context)

    scope = get_auth_scope(
        _fake_request(),
        any_permission=[Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF],
    )

    assert scope.granted_permissions == {Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF}
    assert Permission.KEY_UPDATE not in scope.granted_permissions
    assert Permission.KEY_UPDATE in scope.effective_permissions
    assert Permission.KEY_REVOKE in scope.org_permissions_by_id["org-1"]
    assert Permission.KEY_CREATE_SELF in scope.org_permissions_by_id["org-1"]


def test_get_auth_scope_keeps_permissions_separated_by_team(monkeypatch: pytest.MonkeyPatch):
    context = _make_context(
        account_id="acct-mixed",
        team_memberships=[
            {"team_id": "team-admin", "role": TeamRole.ADMIN},
            {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
        ],
    )
    _set_auth_context(monkeypatch, context)

    scope = get_auth_scope(
        _fake_request(),
        any_permission=[Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF],
    )

    assert Permission.KEY_REVOKE in scope.team_permissions_by_id["team-admin"]
    assert Permission.KEY_CREATE_SELF in scope.team_permissions_by_id["team-admin"]
    assert Permission.KEY_REVOKE not in scope.team_permissions_by_id["team-dev"]
    assert Permission.KEY_CREATE_SELF in scope.team_permissions_by_id["team-dev"]


@pytest.mark.asyncio
async def test_org_admin_can_revoke_non_owned_key_within_org_scope(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-org-1": {
                "owner_account_id": "acct-other",
                "team_id": "team-1",
                "organization_id": "org-1",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-admin",
            org_memberships=[{"organization_id": "org-1", "role": OrganizationRole.ADMIN}],
        ),
    )

    response = await client.post("/ui/api/keys/key-org-1/revoke")

    assert response.status_code == 200
    assert response.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_self_service_user_cannot_revoke_owned_key_outside_team_scope(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-cross-scope": {
                "owner_account_id": "acct-dev",
                "team_id": "team-2",
                "organization_id": "org-2",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-1", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post("/ui/api/keys/key-cross-scope/revoke")

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


@pytest.mark.asyncio
async def test_self_service_user_can_revoke_owned_key_within_team_scope(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-team-1": {
                "owner_account_id": "acct-dev",
                "team_id": "team-1",
                "organization_id": "org-1",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-1", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post("/ui/api/keys/key-team-1/revoke")

    assert response.status_code == 200
    assert response.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_mixed_role_user_cannot_revoke_non_owned_key_in_self_service_team(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-team-dev-other": {
                "owner_account_id": "acct-other",
                "team_id": "team-dev",
                "organization_id": "org-dev",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    response = await client.post("/ui/api/keys/key-team-dev-other/revoke")

    assert response.status_code == 403
    assert response.json()["detail"] == "You can only manage your own keys"


@pytest.mark.asyncio
async def test_mixed_role_user_can_revoke_non_owned_key_in_admin_team(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-team-admin-other": {
                "owner_account_id": "acct-other",
                "team_id": "team-admin",
                "organization_id": "org-admin",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    response = await client.post("/ui/api/keys/key-team-admin-other/revoke")

    assert response.status_code == 200
    assert response.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_mixed_role_user_can_create_self_service_key_in_developer_team(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {},
        teams={
            "team-dev": {
                "team_id": "team-dev",
                "team_alias": "Developer Team",
                "organization_id": "org-dev",
                "self_service_keys_enabled": True,
                "self_service_max_keys_per_user": None,
                "self_service_budget_ceiling": None,
                "self_service_require_expiry": False,
                "self_service_max_expiry_days": None,
                "rpm_limit": None,
                "tpm_limit": None,
                "rph_limit": None,
                "rpd_limit": None,
                "tpd_limit": None,
            },
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "dev-self-service",
            "team_id": "team-dev",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["team_id"] == "team-dev"
    assert body["owner_account_id"] == "acct-mixed"
    assert body["owner_service_account_id"] is None
    assert body["self_service"] is True
