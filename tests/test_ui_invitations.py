from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeInvitationService:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.resend_calls: list[tuple[str, str | None]] = []
        self.cancel_calls: list[str] = []

    async def list_invitations(self, *, status=None, search=None):  # noqa: ANN001, ANN201
        del status, search
        return [
            {
                "invitation_id": "inv-1",
                "account_id": "acct-1",
                "email": "user@example.com",
                "status": "sent",
                "invite_scope_type": "organization",
                "expires_at": "2026-01-01T00:00:00+00:00",
                "accepted_at": None,
                "cancelled_at": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "invited_by_account_id": "acct-admin",
                "inviter_email": "admin@example.com",
                "message_email_id": "email-1",
                "metadata": {"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
            }
        ]

    async def get_invitation(self, invitation_id: str):  # noqa: ANN201
        invitation = next(
            (
                item
                for item in await self.list_invitations()
                if item["invitation_id"] == invitation_id
            ),
            None,
        )
        return invitation

    async def create_invitation(self, **kwargs):  # noqa: ANN003, ANN201
        self.create_calls.append(kwargs)
        return {
            "invitation_id": "inv-2",
            "account_id": "acct-2",
            "email": kwargs["email"],
            "status": "sent",
            "invite_scope_type": "organization",
            "expires_at": "2026-01-01T00:00:00+00:00",
            "metadata": {"organization_invites": [{"organization_id": kwargs["organization_id"], "role": kwargs["organization_role"]}]},
            "message_email_id": "email-2",
        }

    async def resend_invitation(self, *, invitation_id: str, invited_by_account_id: str | None):  # noqa: ANN201
        self.resend_calls.append((invitation_id, invited_by_account_id))
        return {"invitation_id": invitation_id, "status": "sent"}

    async def cancel_invitation(self, *, invitation_id: str):  # noqa: ANN201
        self.cancel_calls.append(invitation_id)
        return True


@pytest.mark.asyncio
async def test_list_invitations_returns_paginated_payload(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.invitation_service = _FakeInvitationService()

    response = await client.get("/ui/api/invitations", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["invitation_id"] == "inv-1"


@pytest.mark.asyncio
async def test_create_invitation_with_master_key_calls_service(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    service = _FakeInvitationService()
    test_app.state.invitation_service = service

    async def _query_raw(*args):  # noqa: ANN002, ANN202
        del args
        return []

    test_app.state.prisma_manager = SimpleNamespace(client=SimpleNamespace(query_raw=_query_raw))

    response = await client.post(
        "/ui/api/invitations",
        headers={"Authorization": "Bearer mk-test"},
        json={"email": "user@example.com", "organization_id": "org-1", "organization_role": "org_member"},
    )

    assert response.status_code == 200
    assert response.json()["invitation_id"] == "inv-2"
    assert service.create_calls[0]["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_create_invitation_rejects_insufficient_scope_permissions(client, test_app, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.admin.endpoints import invitations as invitation_endpoint

    test_app.state.invitation_service = _FakeInvitationService()

    async def _team_query_raw(query: str, *params):  # noqa: ANN001, ANN202
        if "FROM deltallm_teamtable" in query:
            return [{"organization_id": "org-1"}]
        return []

    test_app.state.prisma_manager = SimpleNamespace(client=SimpleNamespace(query_raw=_team_query_raw))
    monkeypatch.setattr(
        invitation_endpoint,
        "get_auth_scope",
        lambda request, authorization=None, x_master_key=None: SimpleNamespace(  # noqa: ARG005
            is_platform_admin=False,
            account_id="acct-1",
            org_permissions_by_id={},
            team_permissions_by_id={},
        ),
    )

    response = await client.post(
        "/ui/api/invitations",
        json={"email": "user@example.com", "team_id": "team-1", "team_role": "team_viewer"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_invitation_rejects_mixed_scope_when_only_one_scope_is_authorized(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api.admin.endpoints import invitations as invitation_endpoint

    service = _FakeInvitationService()
    test_app.state.invitation_service = service

    async def _team_query_raw(query: str, *params):  # noqa: ANN001, ANN202
        if "FROM deltallm_teamtable" in query:
            return [{"organization_id": "org-1"}]
        return []

    test_app.state.prisma_manager = SimpleNamespace(client=SimpleNamespace(query_raw=_team_query_raw))
    monkeypatch.setattr(
        invitation_endpoint,
        "get_auth_scope",
        lambda request, authorization=None, x_master_key=None: SimpleNamespace(  # noqa: ARG005
            is_platform_admin=False,
            account_id="acct-1",
            org_permissions_by_id={},
            team_permissions_by_id={"team-1": {"team.update"}},
        ),
    )

    response = await client.post(
        "/ui/api/invitations",
        json={
            "email": "user@example.com",
            "organization_id": "org-1",
            "organization_role": "org_member",
            "team_id": "team-1",
            "team_role": "team_viewer",
        },
    )

    assert response.status_code == 403
    assert service.create_calls == []


@pytest.mark.asyncio
async def test_create_invitation_rejects_mismatched_team_and_org(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    service = _FakeInvitationService()
    test_app.state.invitation_service = service

    async def _team_query_raw(query: str, *params):  # noqa: ANN001, ANN202
        if "FROM deltallm_teamtable" in query:
            return [{"organization_id": "org-2"}]
        return []

    test_app.state.prisma_manager = SimpleNamespace(client=SimpleNamespace(query_raw=_team_query_raw))

    response = await client.post(
        "/ui/api/invitations",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "email": "user@example.com",
            "organization_id": "org-1",
            "organization_role": "org_member",
            "team_id": "team-1",
            "team_role": "team_viewer",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "team_id does not belong to organization_id"
    assert service.create_calls == []


@pytest.mark.asyncio
async def test_create_invitation_rejects_invalid_role_payload(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    service = _FakeInvitationService()
    test_app.state.invitation_service = service

    async def _query_raw(*args):  # noqa: ANN002, ANN202
        del args
        return []

    test_app.state.prisma_manager = SimpleNamespace(client=SimpleNamespace(query_raw=_query_raw))

    response = await client.post(
        "/ui/api/invitations",
        headers={"Authorization": "Bearer mk-test"},
        json={"email": "user@example.com", "organization_id": "org-1", "organization_role": "bad-role"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid organization role"
    assert service.create_calls == []


@pytest.mark.asyncio
async def test_resend_and_cancel_invitation_with_master_key(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    service = _FakeInvitationService()
    test_app.state.invitation_service = service

    resend = await client.post("/ui/api/invitations/inv-1/resend", headers={"Authorization": "Bearer mk-test"})
    cancel = await client.post("/ui/api/invitations/inv-1/cancel", headers={"Authorization": "Bearer mk-test"})

    assert resend.status_code == 200
    assert cancel.status_code == 200
    assert service.resend_calls == [("inv-1", None)]
    assert service.cancel_calls == ["inv-1"]


@pytest.mark.asyncio
async def test_resend_rejects_partial_scope_access_for_mixed_invitation(client, test_app, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.admin.endpoints import invitations as invitation_endpoint

    class _ScopedService(_FakeInvitationService):
        async def get_invitation(self, invitation_id: str):  # noqa: ANN201
            assert invitation_id == "inv-mixed"
            return {
                "invitation_id": "inv-mixed",
                "account_id": "acct-2",
                "email": "user@example.com",
                "status": "sent",
                "invite_scope_type": "mixed",
                "expires_at": "2026-01-01T00:00:00+00:00",
                "accepted_at": None,
                "cancelled_at": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "invited_by_account_id": "acct-admin",
                "inviter_email": "admin@example.com",
                "message_email_id": "email-1",
                "metadata": {
                    "organization_invites": [{"organization_id": "org-2", "role": "org_member"}],
                    "team_invites": [{"team_id": "team-1", "organization_id": "org-1", "role": "team_viewer"}],
                },
            }

    test_app.state.invitation_service = _ScopedService()
    monkeypatch.setattr(
        invitation_endpoint,
        "get_auth_scope",
        lambda request, authorization=None, x_master_key=None: SimpleNamespace(  # noqa: ARG005
            is_platform_admin=False,
            account_id="acct-1",
            org_permissions_by_id={"org-1": {"org.update"}},
            team_permissions_by_id={},
        ),
    )

    response = await client.post("/ui/api/invitations/inv-mixed/resend")

    assert response.status_code == 403
