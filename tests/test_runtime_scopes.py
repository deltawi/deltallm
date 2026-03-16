from __future__ import annotations

from fastapi import Request
import pytest

from src.middleware.auth import authenticate_request
from src.models.responses import UserAPIKeyAuth
from src.services.runtime_scopes import annotate_auth_metadata, resolve_runtime_scope_context


def test_resolve_runtime_scope_context_for_api_key_auth() -> None:
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(
            api_key="hashed-key",
            user_id="user-1",
            team_id="team-1",
            organization_id="org-1",
        ),
        auth_source="api_key",
        api_key_scope_id="hashed-key",
    )

    context = resolve_runtime_scope_context(auth)

    assert context.auth_source == "api_key"
    assert context.actor_id == "user-1"
    assert context.binding_scopes == (
        ("api_key", "hashed-key"),
        ("team", "team-1"),
        ("organization", "org-1"),
    )


def test_resolve_runtime_scope_context_for_jwt_omits_api_key_scope() -> None:
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(
            api_key="jwt:user-1",
            user_id="user-1",
            team_id="team-1",
            organization_id="org-1",
        ),
        auth_source="jwt",
    )

    context = resolve_runtime_scope_context(auth)

    assert context.auth_source == "jwt"
    assert context.api_key_scope_id is None
    assert context.binding_scopes == (
        ("team", "team-1"),
        ("organization", "org-1"),
    )


def test_resolve_runtime_scope_context_for_custom_auth_uses_explicit_api_key_scope() -> None:
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(api_key="opaque-custom", team_id="team-1"),
        auth_source="custom",
        api_key_scope_id="svc-account-1",
    )

    context = resolve_runtime_scope_context(auth)

    assert context.auth_source == "custom"
    assert context.api_key_scope_id == "svc-account-1"
    assert context.binding_scopes == (
        ("api_key", "svc-account-1"),
        ("team", "team-1"),
    )


@pytest.mark.asyncio
async def test_auth_middleware_attaches_runtime_scope_context_for_jwt(test_app) -> None:
    class StubJWTHandler:
        async def validate_token(self, token: str):
            assert token == "jwt-token"
            return {"user_id": "u-1", "team_id": "team-1", "organization_id": "org-1"}

    test_app.state.jwt_auth_handler = StubJWTHandler()
    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "app": test_app,
            "method": "GET",
            "path": "/_test/runtime-scopes",
            "headers": [(b"authorization", b"Bearer jwt-token")],
        },
        receive=_receive,
    )

    await authenticate_request(request, authorization="Bearer jwt-token")

    context = request.state.runtime_scope_context
    assert context.auth_source == "jwt"
    assert context.is_master_key is False
    assert context.binding_scopes == (("team", "team-1"), ("organization", "org-1"))


@pytest.mark.asyncio
async def test_auth_middleware_attaches_runtime_scope_context_for_master_key(test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "app": test_app,
            "method": "GET",
            "path": "/_test/runtime-scopes-master",
            "headers": [(b"authorization", b"Bearer mk-test")],
        },
        receive=_receive,
    )

    await authenticate_request(request, authorization="Bearer mk-test")

    context = request.state.runtime_scope_context
    assert context.auth_source == "master_key"
    assert context.is_master_key is True
    assert context.binding_scopes == ()
