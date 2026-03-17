from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException

from src.auth.sso import InMemoryUserRepository, SSOAuthHandler, SSOConfig, SSOProvider
from src.models.errors import RateLimitError


class MockSSOHTTPClient:
    def __init__(self) -> None:
        self.post_calls = 0
        self.get_calls = 0
        self.last_post_data: dict[str, str] | None = None

    async def post(self, url: str, data: dict[str, str]):
        self.post_calls += 1
        self.last_post_data = data
        assert url == "https://sso.example.com/oauth/token"
        assert data["grant_type"] == "authorization_code"
        return httpx.Response(200, json={"access_token": "provider-access-token"})

    async def get(self, url: str, headers: dict[str, str]):
        self.get_calls += 1
        assert url == "https://sso.example.com/userinfo"
        assert headers["Authorization"] == "Bearer provider-access-token"
        return httpx.Response(200, json={"email": "admin@example.com"})


class AlwaysLimitedRateLimiter:
    async def check_rate_limit(self, scope: str, entity_id: str, limit: int):
        del scope, entity_id, limit
        raise RateLimitError(retry_after=42)


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

    authorize_url = handler.get_authorize_url("state-123", code_challenge="challenge-abc")
    parsed = urlparse(authorize_url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "sso.example.com"
    assert params["client_id"] == ["client-id"]
    assert params["response_type"] == ["code"]
    assert params["state"] == ["state-123"]
    assert params["code_challenge"] == ["challenge-abc"]
    assert params["code_challenge_method"] == ["S256"]


@pytest.mark.asyncio
async def test_sso_callback_creates_user_and_returns_session_token():
    user_repo = InMemoryUserRepository()
    http_client = MockSSOHTTPClient()
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
        http_client=http_client,
    )

    response = await handler.handle_callback("authorization-code", code_verifier="pkce-verifier-123")

    assert response["email"] == "admin@example.com"
    assert response["role"] == "proxy_admin"
    assert response["team_id"] == "default-team"
    assert response["token"].startswith("sso:")
    assert len(response["token"].split(":")[-1]) >= 43
    assert http_client.last_post_data is not None
    assert http_client.last_post_data["code_verifier"] == "pkce-verifier-123"


def test_sso_pkce_pair_generation_format():
    verifier, challenge = SSOAuthHandler.generate_pkce_pair()
    assert len(verifier) >= 43
    assert challenge


@pytest.mark.asyncio
async def test_sso_callback_is_rate_limited_by_email():
    handler = SSOAuthHandler(
        config=SSOConfig(
            provider=SSOProvider.OKTA,
            client_id="client-id",
            client_secret="client-secret",
            authorize_url="https://sso.example.com/authorize",
            token_url="https://sso.example.com/oauth/token",
            userinfo_url="https://sso.example.com/userinfo",
            redirect_uri="https://proxy.example.com/auth/callback",
        ),
        user_repository=InMemoryUserRepository(),
        http_client=MockSSOHTTPClient(),
        rate_limiter=AlwaysLimitedRateLimiter(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await handler.handle_callback("authorization-code")

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "42"}
