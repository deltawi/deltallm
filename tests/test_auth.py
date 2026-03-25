from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.models.platform_auth import PlatformAuthContext
from src.models.errors import RateLimitError
from src.services.sso_state_store import SSOStateStore


@pytest.mark.asyncio
async def test_auth_missing_header_returns_401(client):
    response = await client.get("/v1/models")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_valid_key_uses_cache_then_db(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}

    first = await client.get("/v1/models", headers=headers)
    second = await client.get("/v1/models", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert test_app.state._test_repo.calls == 1


@pytest.mark.asyncio
async def test_auth_jwt_fallback_allows_request(client, test_app):
    class StubJWTHandler:
        async def validate_token(self, token: str):
            assert token == "jwt-token"
            return {"user_id": "u-1", "email": "user@example.com", "team_id": "t-1", "user_role": "internal_user"}

    test_app.state.jwt_auth_handler = StubJWTHandler()
    headers = {"Authorization": "Bearer jwt-token"}
    response = await client.get("/v1/models", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_auth_login_and_callback_routes(client, test_app):
    class StubSSOHandler:
        def generate_pkce_pair(self):
            return ("pkce-verifier", "pkce-challenge")

        def get_authorize_url(self, state: str, code_challenge: str | None = None):
            suffix = f"&code_challenge={code_challenge}" if code_challenge else ""
            return f"https://sso.example.com/login?state={state}{suffix}"

        async def handle_callback(self, code: str, code_verifier: str | None = None):
            del code_verifier
            return {"user_id": "user-1", "email": "user@example.com", "role": "internal_user", "token": "session-token"}

    class StubIdentityService:
        async def get_context_for_session(self, token: str):
            del token
            return None

        async def upsert_sso_account(self, **kwargs):
            del kwargs
            return SimpleNamespace(session_token="session-token")

    test_app.state.sso_auth_handler = StubSSOHandler()
    test_app.state.platform_identity_service = StubIdentityService()
    test_app.state.sso_state_store = SSOStateStore(redis_client=test_app.state.redis, ttl_seconds=600)

    login = await client.get("/auth/login", params={"state": "abc"})
    assert login.status_code == 200
    assert "sso.example.com" in login.json()["authorize_url"]

    callback = await client.get("/auth/callback", params={"code": "oauth-code", "state": "abc"})
    assert callback.status_code == 302
    assert callback.headers["location"] == "/"
    assert "deltallm_session=session-token" in callback.headers.get("set-cookie", "")

    replay = await client.get("/auth/callback", params={"code": "oauth-code", "state": "abc"})
    assert replay.status_code == 400
    assert replay.json()["detail"] == "Invalid or expired SSO state"


@pytest.mark.asyncio
async def test_internal_login_is_rate_limited(client, test_app):
    class StubLimitCounter:
        async def check_rate_limit(self, scope: str, entity_id: str, limit: int):
            del scope, entity_id, limit
            raise RateLimitError(retry_after=17)

    test_app.state.limit_counter = StubLimitCounter()
    test_app.state.platform_identity_service = object()

    response = await client.post("/auth/internal/login", json={"email": "user@example.com", "password": "bad-password"})
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many login attempts; please try again later"
    assert response.headers.get("retry-after") == "17"


@pytest.mark.asyncio
async def test_auth_callback_is_rate_limited(client, test_app):
    class StubLimitCounter:
        async def check_rate_limit(self, scope: str, entity_id: str, limit: int):
            del scope, entity_id, limit
            raise RateLimitError(retry_after=9)

    class StubSSOHandler:
        def generate_pkce_pair(self):
            return ("pkce-verifier", "pkce-challenge")

        def get_authorize_url(self, state: str, code_challenge: str | None = None):
            del code_challenge
            return f"https://sso.example.com/login?state={state}"

        async def handle_callback(self, code: str, code_verifier: str | None = None):
            del code
            del code_verifier
            return {"user_id": "user-1", "email": "user@example.com", "role": "internal_user", "token": "session-token"}

    test_app.state.limit_counter = StubLimitCounter()
    test_app.state.sso_auth_handler = StubSSOHandler()
    test_app.state.sso_state_store = SSOStateStore(redis_client=test_app.state.redis, ttl_seconds=600)

    login = await client.get("/auth/login", params={"state": "rl-state"})
    assert login.status_code == 200

    callback = await client.get("/auth/callback", params={"code": "oauth-code", "state": "rl-state"})
    assert callback.status_code == 429
    assert callback.json()["detail"] == "Too many SSO callback attempts; please try again later"
    assert callback.headers.get("retry-after") == "9"


@pytest.mark.asyncio
async def test_auth_login_requires_shared_sso_state_store(client, test_app):
    class StubSSOHandler:
        def generate_pkce_pair(self):
            return ("pkce-verifier", "pkce-challenge")

        def get_authorize_url(self, state: str, code_challenge: str | None = None):
            suffix = f"&code_challenge={code_challenge}" if code_challenge else ""
            return f"https://sso.example.com/login?state={state}{suffix}"

    test_app.state.sso_auth_handler = StubSSOHandler()
    test_app.state.sso_state_store = None

    response = await client.get("/auth/login", params={"state": "missing-store"})

    assert response.status_code == 503
    assert response.json()["detail"] == "SSO state storage unavailable"


@pytest.mark.asyncio
async def test_unverified_mfa_session_is_blocked_until_mfa_verify(client, test_app):
    class StubIdentityService:
        def __init__(self) -> None:
            self.verified = False

        async def get_context_for_session(self, token: str):
            if token != "session-token":
                return None
            return PlatformAuthContext(
                account_id="acct-1",
                email="user@example.com",
                role="platform_admin",
                mfa_enabled=True,
                mfa_verified=self.verified,
                permissions=[],
                organization_memberships=[],
                team_memberships=[],
                force_password_change=False,
            )

        async def verify_mfa_for_session(self, *, session_token: str, code: str) -> bool:
            assert session_token == "session-token"
            if code != "123456":
                return False
            self.verified = True
            return True

    class StubEmailRepository:
        async def summarize_status_counts(self):  # noqa: ANN201
            return []

        async def list_recent(self, *, limit: int = 20):  # noqa: ANN201
            del limit
            return []

    test_app.state.platform_identity_service = StubIdentityService()
    test_app.state.email_outbox_repository = StubEmailRepository()

    me = await client.get("/auth/me", cookies={"deltallm_session": "session-token"})
    assert me.status_code == 200
    assert me.json()["mfa_enabled"] is True
    assert me.json()["mfa_verified"] is False

    blocked = await client.get("/ui/api/email/outbox/summary", cookies={"deltallm_session": "session-token"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "MFA verification required"

    verified = await client.post(
        "/auth/mfa/verify",
        cookies={"deltallm_session": "session-token"},
        json={"code": "123456"},
    )
    assert verified.status_code == 200
    assert verified.json() == {"mfa_verified": True}

    allowed = await client.get("/ui/api/email/outbox/summary", cookies={"deltallm_session": "session-token"})
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_unverified_mfa_session_is_blocked_for_scope_endpoints(client, test_app):
    class StubIdentityService:
        async def get_context_for_session(self, token: str):
            if token != "session-token":
                return None
            return PlatformAuthContext(
                account_id="acct-1",
                email="user@example.com",
                role="org_user",
                mfa_enabled=True,
                mfa_verified=False,
                permissions=[],
                organization_memberships=[{"organization_id": "org-1", "role": "org_admin"}],
                team_memberships=[],
                force_password_change=False,
            )

    test_app.state.platform_identity_service = StubIdentityService()
    test_app.state.invitation_service = SimpleNamespace()

    response = await client.get("/ui/api/invitations", cookies={"deltallm_session": "session-token"})

    assert response.status_code == 403
    assert response.json()["detail"] == "MFA verification required"
