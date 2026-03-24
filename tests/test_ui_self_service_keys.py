from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.api.admin.endpoints.common import get_auth_scope
from src.auth.roles import OrganizationRole, Permission, TeamRole
from src.models.platform_auth import PlatformAuthContext


class _FakeKeyDB:
    def __init__(self, keys: dict[str, dict[str, Any]]) -> None:
        self.keys = dict(keys)

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

        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if normalized.startswith("delete from deltallm_verificationtoken where token = $1"):
            token_hash = str(params[0])
            return 1 if self.keys.pop(token_hash, None) is not None else 0
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


def _install_key_db(test_app: Any, keys: dict[str, dict[str, Any]]) -> _FakeKeyDB:
    fake_db = _FakeKeyDB(keys)
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
