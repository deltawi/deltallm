from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.bootstrap import BootstrapStatus
from src.bootstrap.auth import init_auth_runtime


def _auth_config(*, enable_sso: bool, enable_jwt: bool, custom_auth: str | None, jwt_issuer: str | None = "issuer") -> SimpleNamespace:
    return SimpleNamespace(
        general_settings=SimpleNamespace(
            api_key_auth_cache_ttl_seconds=300,
            auth_session_ttl_hours=12,
            redis_degraded_mode="fail_open",
            platform_bootstrap_admin_email="admin@example.com",
            platform_bootstrap_admin_password="secret",
            enable_sso=enable_sso,
            sso_provider="oidc",
            sso_client_id="client-id",
            sso_client_secret="client-secret",
            sso_authorize_url="https://idp.example.com/auth",
            sso_token_url="https://idp.example.com/token",
            sso_userinfo_url="https://idp.example.com/userinfo",
            sso_redirect_uri="https://gateway.example.com/callback",
            sso_scope="openid email profile",
            sso_admin_email_list=["admin@example.com"],
            sso_default_team_id="team-1",
            enable_jwt_auth=enable_jwt,
            jwt_public_key_url="https://idp.example.com/jwks.json",
            jwt_audience="gateway",
            jwt_issuer=jwt_issuer,
            jwt_claims_mapping={"user_id": "sub"},
            custom_auth=custom_auth,
        )
    )


@pytest.mark.asyncio
async def test_init_auth_runtime_wires_enabled_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakePlatformIdentityService:
        def __init__(self, *, db_client, salt, session_ttl_hours) -> None:  # noqa: ANN001
            self.db_client = db_client
            self.salt = salt
            self.session_ttl_hours = session_ttl_hours
            self.bootstrap_calls: list[tuple[str | None, str | None]] = []
            created["platform_identity_service"] = self

        async def ensure_bootstrap_admin(self, email: str | None, password: str | None) -> None:
            self.bootstrap_calls.append((email, password))

    monkeypatch.setattr("src.bootstrap.auth.KeyRepository", lambda client: ("key-repo", client))
    monkeypatch.setattr("src.bootstrap.auth.KeyService", lambda **kwargs: ("key-service", kwargs))
    monkeypatch.setattr("src.bootstrap.auth.PlatformIdentityService", FakePlatformIdentityService)
    monkeypatch.setattr("src.bootstrap.auth.LimitCounter", lambda **kwargs: ("limit-counter", kwargs))
    monkeypatch.setattr("src.bootstrap.auth.InMemoryUserRepository", lambda: "user-repo")
    monkeypatch.setattr("src.bootstrap.auth.SSOStateStore", lambda **kwargs: ("sso-state-store", kwargs))
    monkeypatch.setattr("src.bootstrap.auth.SSOAuthHandler", lambda **kwargs: ("sso-handler", kwargs))
    monkeypatch.setattr("src.bootstrap.auth.JWTAuthHandler", lambda **kwargs: ("jwt-handler", kwargs))

    class FakeCustomAuthManager:
        def __init__(self) -> None:
            self.registered: list[str] = []

        def register(self, handler_path: str) -> None:
            self.registered.append(handler_path)

    monkeypatch.setattr("src.bootstrap.auth.CustomAuthManager", FakeCustomAuthManager)

    app = SimpleNamespace(
        state=SimpleNamespace(
            prisma_manager=SimpleNamespace(client="db-client"),
            redis="redis-client",
            salt_key="salt",
            settings=SimpleNamespace(redis_degraded_mode="fail_open"),
            http_client="http-client",
        )
    )

    runtime = await init_auth_runtime(app, _auth_config(enable_sso=True, enable_jwt=True, custom_auth="module.handler"))

    assert app.state.key_service[0] == "key-service"
    assert created["platform_identity_service"].bootstrap_calls == [("admin@example.com", "secret")]
    assert app.state.limit_counter[0] == "limit-counter"
    assert app.state.sso_user_repository == "user-repo"
    assert app.state.sso_state_store[0] == "sso-state-store"
    assert app.state.sso_auth_handler[0] == "sso-handler"
    assert app.state.jwt_auth_handler[0] == "jwt-handler"
    assert app.state.custom_auth_manager.registered == ["module.handler"]
    assert runtime.statuses == (
        BootstrapStatus("key_service", "ready"),
        BootstrapStatus("platform_identity", "ready"),
        BootstrapStatus("sso_state_store", "ready"),
        BootstrapStatus("sso_auth", "ready"),
        BootstrapStatus("jwt_auth", "ready"),
        BootstrapStatus("custom_auth", "ready"),
    )


@pytest.mark.asyncio
async def test_init_auth_runtime_leaves_optional_handlers_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePlatformIdentityService:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

        async def ensure_bootstrap_admin(self, email: str | None, password: str | None) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr("src.bootstrap.auth.PlatformIdentityService", FakePlatformIdentityService)

    app = SimpleNamespace(
        state=SimpleNamespace(
            prisma_manager=SimpleNamespace(client="db-client"),
            redis=None,
            salt_key="salt",
            settings=SimpleNamespace(redis_degraded_mode="fail_open"),
            http_client="http-client",
        )
    )

    runtime = await init_auth_runtime(app, _auth_config(enable_sso=False, enable_jwt=False, custom_auth=None))

    assert app.state.sso_auth_handler is None
    assert app.state.sso_state_store is None
    assert app.state.jwt_auth_handler is None
    assert app.state.custom_auth_manager is None
    assert runtime.statuses == (
        BootstrapStatus("key_service", "ready"),
        BootstrapStatus("platform_identity", "ready"),
        BootstrapStatus("sso_state_store", "disabled"),
        BootstrapStatus("sso_auth", "disabled"),
        BootstrapStatus("jwt_auth", "disabled"),
        BootstrapStatus("custom_auth", "disabled"),
    )


@pytest.mark.asyncio
async def test_init_auth_runtime_requires_jwt_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePlatformIdentityService:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

        async def ensure_bootstrap_admin(self, email: str | None, password: str | None) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr("src.bootstrap.auth.PlatformIdentityService", FakePlatformIdentityService)

    app = SimpleNamespace(
        state=SimpleNamespace(
            prisma_manager=SimpleNamespace(client="db-client"),
            redis=None,
            salt_key="salt",
            settings=SimpleNamespace(redis_degraded_mode="fail_open"),
            http_client="http-client",
        )
    )

    with pytest.raises(ValueError, match="JWT issuer must be configured"):
        await init_auth_runtime(app, _auth_config(enable_sso=False, enable_jwt=True, custom_auth=None, jwt_issuer=None))


@pytest.mark.asyncio
async def test_init_auth_runtime_marks_incomplete_sso_degraded(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakePlatformIdentityService:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

        async def ensure_bootstrap_admin(self, email: str | None, password: str | None) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr("src.bootstrap.auth.PlatformIdentityService", FakePlatformIdentityService)

    cfg = _auth_config(enable_sso=True, enable_jwt=False, custom_auth=None)
    cfg.general_settings.sso_client_secret = None
    app = SimpleNamespace(
        state=SimpleNamespace(
            prisma_manager=SimpleNamespace(client="db-client"),
            redis=None,
            salt_key="salt",
            settings=SimpleNamespace(redis_degraded_mode="fail_open"),
            http_client="http-client",
        )
    )

    runtime = await init_auth_runtime(app, cfg)

    assert BootstrapStatus("sso_state_store", "degraded", "configuration incomplete") in runtime.statuses
    assert BootstrapStatus("sso_auth", "degraded", "configuration incomplete") in runtime.statuses
    assert "sso enabled but configuration is incomplete" in caplog.text
