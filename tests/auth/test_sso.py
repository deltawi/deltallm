from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.auth.sso import InMemoryUserRepository, SSOAuthHandler, SSOConfig, SSOProvider


class MockSSOHTTPClient:
    def __init__(self) -> None:
        self.post_calls = 0
        self.get_calls = 0

    async def post(self, url: str, data: dict[str, str]):
        self.post_calls += 1
        assert url == "https://sso.example.com/oauth/token"
        assert data["grant_type"] == "authorization_code"
        return httpx.Response(200, json={"access_token": "provider-access-token"})

    async def get(self, url: str, headers: dict[str, str]):
        self.get_calls += 1
        assert url == "https://sso.example.com/userinfo"
        assert headers["Authorization"] == "Bearer provider-access-token"
        return httpx.Response(200, json={"email": "admin@example.com"})


@pytest.mark.asyncio
async def test_sso_get_authorize_url_contains_required_query_params():
    handler = SSOAuthHandler(
        config=SSOConfig(
            provider=SSOProvider.GENERIC_OIDC,
            client_id="client-id",
            client_secret="client-secret",
            authorize_url="https://sso.example.com/authorize",
            token_url="https://sso.example.com/oauth/token",
            userinfo_url="https://sso.example.com/userinfo",
            redirect_uri="https://proxy.example.com/auth/callback",
        ),
        user_repository=InMemoryUserRepository(),
    )

    authorize_url = handler.get_authorize_url("state-123")
    parsed = urlparse(authorize_url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "sso.example.com"
    assert params["client_id"] == ["client-id"]
    assert params["response_type"] == ["code"]
    assert params["state"] == ["state-123"]


@pytest.mark.asyncio
async def test_sso_callback_creates_user_and_returns_session_token():
    user_repo = InMemoryUserRepository()
    handler = SSOAuthHandler(
        config=SSOConfig(
            provider=SSOProvider.OKTA,
            client_id="client-id",
            client_secret="client-secret",
            authorize_url="https://sso.example.com/authorize",
            token_url="https://sso.example.com/oauth/token",
            userinfo_url="https://sso.example.com/userinfo",
            redirect_uri="https://proxy.example.com/auth/callback",
            admin_email_list=["admin@example.com"],
            default_team_id="default-team",
        ),
        user_repository=user_repo,
        http_client=MockSSOHTTPClient(),
    )

    response = await handler.handle_callback("authorization-code")

    assert response["email"] == "admin@example.com"
    assert response["role"] == "proxy_admin"
    assert response["team_id"] == "default-team"
    assert response["token"].startswith("sso:")
