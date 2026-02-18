from __future__ import annotations

import pytest


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
        def get_authorize_url(self, state: str):
            return f"https://sso.example.com/login?state={state}"

        async def handle_callback(self, code: str):
            return {"user_id": "user-1", "email": "user@example.com", "role": "internal_user", "token": "session-token"}

    test_app.state.sso_auth_handler = StubSSOHandler()

    login = await client.get("/auth/login", params={"state": "abc"})
    assert login.status_code == 200
    assert "sso.example.com" in login.json()["authorize_url"]

    callback = await client.get("/auth/callback", params={"code": "oauth-code", "state": "abc"})
    assert callback.status_code == 200
    assert callback.json()["token"] == "session-token"
    assert callback.json()["state"] == "abc"
