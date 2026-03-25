from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_get_invitation_token_returns_validation_payload(client, test_app) -> None:
    test_app.state.invitation_service = SimpleNamespace(
        describe_invitation_token=lambda token: SimpleNamespace(
            invitation_id="inv-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            inviter_email="admin@example.com",
            expires_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"),
            metadata={"organization_invites": [{"organization_id": "org-1"}]},
            password_required=True,
        )
        if token == "valid-token"
        else None
    )

    async def _describe(token: str):  # noqa: ANN202
        if token != "valid-token":
            return None
        return SimpleNamespace(
            invitation_id="inv-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            inviter_email="admin@example.com",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1"}]},
            password_required=True,
        )

    test_app.state.invitation_service = SimpleNamespace(describe_invitation_token=_describe)

    response = await client.get("/auth/invitations/valid-token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["email"] == "user@example.com"
    assert payload["password_required"] is True


@pytest.mark.asyncio
async def test_get_invitation_token_can_return_sso_style_password_not_required(client, test_app) -> None:
    async def _describe(token: str):  # noqa: ANN202
        if token != "valid-token":
            return None
        return SimpleNamespace(
            invitation_id="inv-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            inviter_email="admin@example.com",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1"}]},
            password_required=False,
        )

    test_app.state.invitation_service = SimpleNamespace(describe_invitation_token=_describe)

    response = await client.get("/auth/invitations/valid-token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["password_required"] is False


@pytest.mark.asyncio
async def test_accept_invitation_sets_session_cookie(client, test_app) -> None:
    async def _accept_invitation(raw_token: str, password: str | None):  # noqa: ANN202
        assert raw_token == "valid-token"
        assert password == "very-secure-password"
        return SimpleNamespace(
            account_id="acct-1",
            email="user@example.com",
            role="org_user",
            mfa_enabled=False,
            force_password_change=False,
            session_established=True,
            next_step="session_established",
            session_token="session-token",
            mfa_required=False,
            mfa_prompt=True,
        )

    test_app.state.invitation_service = SimpleNamespace(accept_invitation=_accept_invitation)

    response = await client.post(
        "/auth/invitations/accept",
        json={"token": "valid-token", "password": "very-secure-password"},
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == "acct-1"
    assert response.json()["session_established"] is True
    assert "deltallm_session=session-token" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_accept_invitation_allows_sso_only_style_accept_without_password(client, test_app) -> None:
    async def _accept_invitation(raw_token: str, password: str | None):  # noqa: ANN202
        assert raw_token == "valid-token"
        assert password is None
        return SimpleNamespace(
            account_id="acct-1",
            email="user@example.com",
            role="org_user",
            mfa_enabled=False,
            force_password_change=False,
            session_established=True,
            next_step="session_established",
            session_token="session-token",
            mfa_required=False,
            mfa_prompt=True,
        )

    test_app.state.invitation_service = SimpleNamespace(accept_invitation=_accept_invitation)

    response = await client.post(
        "/auth/invitations/accept",
        json={"token": "valid-token", "password": None},
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == "acct-1"
    assert response.json()["session_established"] is True


@pytest.mark.asyncio
async def test_accept_invitation_requires_follow_up_login_for_mfa_enabled_account(client, test_app) -> None:
    async def _accept_invitation(raw_token: str, password: str | None):  # noqa: ANN202
        assert raw_token == "valid-token"
        assert password is None
        return SimpleNamespace(
            account_id="acct-1",
            email="user@example.com",
            role="org_user",
            mfa_enabled=True,
            force_password_change=False,
            session_established=False,
            next_step="login",
            session_token=None,
            mfa_required=True,
            mfa_prompt=False,
        )

    test_app.state.invitation_service = SimpleNamespace(accept_invitation=_accept_invitation)

    response = await client.post(
        "/auth/invitations/accept",
        json={"token": "valid-token", "password": None},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["account_id"] == "acct-1"
    assert payload["session_established"] is False
    assert payload["next_step"] == "login"
    assert payload["mfa_required"] is True
    assert "deltallm_session=" not in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_accept_invitation_rejects_password_change_for_existing_account(client, test_app) -> None:
    async def _accept_invitation(raw_token: str, password: str | None):  # noqa: ANN202
        assert raw_token == "valid-token"
        assert password == "very-secure-password"
        raise ValueError("password cannot be changed when accepting this invitation")

    test_app.state.invitation_service = SimpleNamespace(accept_invitation=_accept_invitation)

    response = await client.post(
        "/auth/invitations/accept",
        json={"token": "valid-token", "password": "very-secure-password"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "password cannot be changed when accepting this invitation"


@pytest.mark.asyncio
async def test_forgot_password_returns_generic_success_for_missing_account(client, test_app) -> None:
    async def _get_account_by_email(email: str):  # noqa: ANN202
        assert email == "missing@example.com"
        return None

    test_app.state.platform_identity_service = SimpleNamespace(get_account_by_email=_get_account_by_email)
    test_app.state.email_token_service = SimpleNamespace()
    test_app.state.email_outbox_service = SimpleNamespace()

    response = await client.post("/auth/internal/forgot-password", json={"email": "missing@example.com"})

    assert response.status_code == 200
    assert response.json() == {"requested": True}


@pytest.mark.asyncio
async def test_forgot_password_returns_generic_success_when_email_queueing_fails(client, test_app) -> None:
    issued_token_ids: list[str] = []
    consumed_token_ids: list[str] = []
    invalidated: list[str] = []

    async def _get_account_by_email(email: str):  # noqa: ANN202
        assert email == "user@example.com"
        return {"account_id": "acct-1", "email": email, "password_hash": "existing-hash"}

    async def _issue_password_reset_token(*, account_id: str, email: str, created_by_account_id: str | None = None):  # noqa: ANN202
        del created_by_account_id
        assert account_id == "acct-1"
        assert email == "user@example.com"
        issued_token_ids.append("tok-1")
        return SimpleNamespace(raw_token="raw-reset", record=SimpleNamespace(token_id="tok-1"))

    async def _invalidate_active_tokens(  # noqa: ANN202
        *,
        purpose: str,
        account_id: str | None = None,
        invitation_id: str | None = None,
        exclude_token_id: str | None = None,
    ) -> int:
        del purpose, invitation_id, exclude_token_id
        invalidated.append(account_id or "")
        return 0

    async def _consume_token(*, token_id: str) -> bool:  # noqa: ANN202
        consumed_token_ids.append(token_id)
        return True

    async def _enqueue_template_email(**kwargs):  # noqa: ANN003, ANN202
        raise RuntimeError("enqueue failed")

    test_app.state.platform_identity_service = SimpleNamespace(get_account_by_email=_get_account_by_email)
    test_app.state.email_token_service = SimpleNamespace(
        issue_password_reset_token=_issue_password_reset_token,
        invalidate_active_tokens=_invalidate_active_tokens,
        consume_token=_consume_token,
        build_action_url=lambda *, path, raw_token: f"{path}?token={raw_token}",
    )
    test_app.state.email_outbox_service = SimpleNamespace(enqueue_template_email=_enqueue_template_email)
    test_app.state.prisma_manager = SimpleNamespace(client=SimpleNamespace())

    response = await client.post("/auth/internal/forgot-password", json={"email": "user@example.com"})

    assert response.status_code == 200
    assert response.json() == {"requested": True}
    assert issued_token_ids == ["tok-1"]
    assert consumed_token_ids == ["tok-1"]
    assert invalidated == []


@pytest.mark.asyncio
async def test_reset_password_rejects_invalid_token(client, test_app) -> None:
    async def _claim_token(*, purpose: str, raw_token: str):  # noqa: ANN202
        assert purpose == "password_reset"
        assert raw_token == "bad-token"
        return None

    test_app.state.email_token_service = SimpleNamespace(claim_token=_claim_token)
    test_app.state.platform_identity_service = SimpleNamespace(validate_password_policy=lambda password: None)

    response = await client.post(
        "/auth/internal/reset-password",
        json={"token": "bad-token", "new_password": "very-secure-password"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Reset token is invalid or expired"


@pytest.mark.asyncio
async def test_reset_password_rejects_replay_after_claim(client, test_app) -> None:
    claims = {"count": 0}
    password_calls: list[tuple[str, str]] = []
    revoked: list[str] = []
    invalidated: list[tuple[str, str]] = []

    async def _claim_token(*, purpose: str, raw_token: str):  # noqa: ANN202
        assert purpose == "password_reset"
        assert raw_token == "good-token"
        claims["count"] += 1
        if claims["count"] > 1:
            return None
        return SimpleNamespace(token_id="tok-1", account_id="acct-1", email="user@example.com")

    async def _set_password(*, account_id: str, new_password: str) -> None:  # noqa: ANN202
        password_calls.append((account_id, new_password))

    async def _invalidate_active_tokens(  # noqa: ANN202
        *,
        purpose: str,
        account_id: str | None = None,
        invitation_id: str | None = None,
        exclude_token_id: str | None = None,
    ) -> int:
        del invitation_id
        del exclude_token_id
        invalidated.append((purpose, account_id or ""))
        return 0

    async def _revoke_all_sessions_for_account(account_id: str) -> None:  # noqa: ANN202
        revoked.append(account_id)

    test_app.state.email_token_service = SimpleNamespace(
        claim_token=_claim_token,
        invalidate_active_tokens=_invalidate_active_tokens,
    )
    test_app.state.platform_identity_service = SimpleNamespace(
        validate_password_policy=lambda password: None,
        set_password=_set_password,
        revoke_all_sessions_for_account=_revoke_all_sessions_for_account,
    )

    first = await client.post(
        "/auth/internal/reset-password",
        json={"token": "good-token", "new_password": "very-secure-password"},
    )
    second = await client.post(
        "/auth/internal/reset-password",
        json={"token": "good-token", "new_password": "very-secure-password"},
    )

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["detail"] == "Reset token is invalid or expired"
    assert password_calls == [("acct-1", "very-secure-password")]
    assert invalidated == [("password_reset", "acct-1")]
    assert revoked == ["acct-1"]


@pytest.mark.asyncio
async def test_reset_password_uses_transactional_service_clones_when_db_supports_tx(client, test_app) -> None:
    class _TxDB:
        def __init__(self) -> None:
            self.entries = 0

        def tx(self):  # noqa: ANN201
            return self

        async def __aenter__(self):  # noqa: ANN202
            self.entries += 1
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN202
            del exc_type, exc, tb
            return False

    class _TxAwareTokenService:
        def __init__(self) -> None:
            self.repositories: list[object] = []
            self.invalidated: list[tuple[str, str]] = []

        def with_repository(self, repository):  # noqa: ANN201
            self.repositories.append(repository)
            return self

        async def claim_token(self, *, purpose: str, raw_token: str):  # noqa: ANN202
            assert purpose == "password_reset"
            assert raw_token == "good-token"
            return SimpleNamespace(token_id="tok-1", account_id="acct-1", email="user@example.com")

        async def invalidate_active_tokens(  # noqa: ANN202
            self,
            *,
            purpose: str,
            account_id: str | None = None,
            invitation_id: str | None = None,
            exclude_token_id: str | None = None,
        ) -> int:
            del invitation_id
            del exclude_token_id
            self.invalidated.append((purpose, account_id or ""))
            return 0

    class _TxAwareIdentityService:
        def __init__(self) -> None:
            self.db_clients: list[object] = []
            self.password_calls: list[tuple[str, str]] = []
            self.revoked: list[str] = []

        def validate_password_policy(self, password: str) -> None:
            assert password == "very-secure-password"

        def with_db(self, db_client):  # noqa: ANN201
            self.db_clients.append(db_client)
            return self

        async def set_password(self, *, account_id: str, new_password: str) -> None:  # noqa: ANN202
            self.password_calls.append((account_id, new_password))

        async def revoke_all_sessions_for_account(self, account_id: str) -> None:  # noqa: ANN202
            self.revoked.append(account_id)

    tx_db = _TxDB()
    token_service = _TxAwareTokenService()
    identity_service = _TxAwareIdentityService()
    test_app.state.prisma_manager = SimpleNamespace(client=tx_db)
    test_app.state.email_token_service = token_service
    test_app.state.platform_identity_service = identity_service

    response = await client.post(
        "/auth/internal/reset-password",
        json={"token": "good-token", "new_password": "very-secure-password"},
    )

    assert response.status_code == 200
    assert tx_db.entries == 1
    assert token_service.repositories
    assert identity_service.db_clients == [tx_db]
    assert token_service.invalidated == [("password_reset", "acct-1")]
    assert identity_service.password_calls == [("acct-1", "very-secure-password")]
    assert identity_service.revoked == ["acct-1"]
