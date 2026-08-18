from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.audit.actions import AuditAction
from src.config import AppConfig
from src.models.platform_auth import PlatformAuthContext
from src.models.errors import RateLimitError
from src.services.platform_identity_service import AccountInactiveError, LoginSessionCreationError
from src.services.master_session_service import (
    MasterSessionStatus,
    MasterSessionStoreUnavailable,
)
from src.services.sso_state_store import SSOStateStore


class _StubSSOHandler:
    def __init__(self, *, email: str = "user@example.com", subject: str = "subject-1", email_verified: bool | None = True) -> None:
        self.email = email
        self.subject = subject
        self.email_verified = email_verified

    def generate_pkce_pair(self):
        return ("pkce-verifier", "pkce-challenge")

    def get_authorize_url(self, state: str, code_challenge: str | None = None):
        suffix = f"&code_challenge={code_challenge}" if code_challenge else ""
        return f"https://sso.example.com/login?state={state}{suffix}"

    async def handle_callback(self, code: str, code_verifier: str | None = None):
        del code
        del code_verifier
        return {
            "user_id": "legacy-user-id",
            "email": self.email,
            "role": "internal_user",
            "provider_subject": self.subject,
            "email_verified": self.email_verified,
            "token": "provider-session-token",
        }


class _StubIdentityService:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, object]] = {}
        self.identities: dict[tuple[str, str], str] = {}
        self.legacy_upserts: list[dict[str, object]] = []
        self.identity_links: list[dict[str, str]] = []
        self.login_results: list[str] = []
        self.last_logins: list[str] = []
        self.sso_login_calls: list[dict[str, str]] = []

    def add_account(self, *, account_id: str, email: str, role: str = "org_user", is_active: bool = True) -> None:
        self.accounts[account_id] = {
            "account_id": account_id,
            "email": self.normalize_email(email),
            "role": role,
            "is_active": is_active,
        }

    def normalize_email(self, email: str | None) -> str:
        return str(email or "").strip().lower()

    async def get_account_by_sso_identity(self, *, provider: str, subject: str):
        account_id = self.identities.get((provider, subject))
        account = self.accounts.get(account_id or "")
        return dict(account) if account else None

    async def get_account_by_email(self, email: str):
        normalized_email = self.normalize_email(email)
        for account in self.accounts.values():
            if str(account.get("email") or "").lower() == normalized_email:
                return dict(account)
        return None

    async def link_sso_identity(self, *, account_id: str, email: str, provider: str = "sso", subject: str | None = None):
        normalized_subject = subject or self.normalize_email(email)
        existing_account_id = self.identities.get((provider, normalized_subject))
        if existing_account_id is not None and existing_account_id != account_id:
            raise ValueError("SSO identity is already linked to another account")
        self.identities[(provider, normalized_subject)] = account_id
        self.identity_links.append(
            {
                "account_id": account_id,
                "email": self.normalize_email(email),
                "provider": provider,
                "subject": normalized_subject,
            }
        )

    async def reconcile_sso_identity_for_account(
        self,
        *,
        account_id: str,
        email: str,
        provider: str = "sso",
        subject: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
    ):
        normalized_email = self.normalize_email(email)
        email_account = await self.get_account_by_email(normalized_email)
        email_account_id = str((email_account or {}).get("account_id") or "")
        if email_account_id and email_account_id != account_id:
            raise ValueError("SSO email is already linked to another account")
        account = self.accounts.get(account_id)
        if account is None:
            raise RuntimeError("SSO account not found")
        account["email"] = normalized_email
        if role is not None:
            account["role"] = role
        if is_active is not None:
            account["is_active"] = is_active
        await self.link_sso_identity(
            account_id=account_id,
            email=normalized_email,
            provider=provider,
            subject=subject,
        )
        return dict(account)

    async def create_sso_login_for_existing_account(
        self,
        *,
        account_id: str,
        email: str,
        provider: str = "sso",
        subject: str | None = None,
    ):
        self.sso_login_calls.append(
            {
                "account_id": account_id,
                "email": self.normalize_email(email),
                "provider": provider,
                "subject": subject or self.normalize_email(email),
            }
        )
        await self.reconcile_sso_identity_for_account(
            account_id=account_id,
            email=email,
            provider=provider,
            subject=subject,
        )
        login = await self.create_login_result_for_account(account_id)
        if login is None:
            raise RuntimeError("Failed to establish session")
        await self.mark_last_login(account_id)
        return login

    async def create_login_result_for_account(self, account_id: str):
        self.login_results.append(account_id)
        account = self.accounts.get(account_id)
        if account is None or not bool(account.get("is_active", True)):
            return None
        return SimpleNamespace(
            session_token=f"session-{account_id}",
            context=SimpleNamespace(account_id=account_id),
        )

    async def mark_last_login(self, account_id: str):
        self.last_logins.append(account_id)

    async def upsert_sso_account(self, **kwargs):
        self.legacy_upserts.append(dict(kwargs))
        email = self.normalize_email(kwargs["email"])
        existing = await self.get_account_by_email(email)
        account_id = str(existing.get("account_id")) if existing else "acct-legacy"
        if existing is None:
            self.add_account(
                account_id=account_id,
                email=email,
                role="platform_admin" if kwargs.get("is_platform_admin") else "org_user",
                is_active=True,
            )
        await self.link_sso_identity(
            account_id=account_id,
            email=email,
            provider=str(kwargs.get("provider") or "sso"),
            subject=str(kwargs.get("subject") or email),
        )
        login = await self.create_login_result_for_account(account_id)
        if login is None:
            raise LoginSessionCreationError("Failed to establish session")
        await self.mark_last_login(account_id)
        return login


class _StubSelfRegistrationProvisioner:
    def __init__(self, *, identity_service: _StubIdentityService | None = None, error: Exception | None = None) -> None:
        self.identity_service = identity_service
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def provision_sso_from_defaults(
        self,
        *,
        email: str,
        settings,
        provider: str,
        subject: str,
        is_active: bool = True,
    ):  # noqa: ANN001
        self.calls.append({"email": email, "settings": settings, "is_active": is_active})
        if self.error is not None:
            raise self.error
        if self.identity_service is not None:
            self.identity_service.add_account(account_id="acct-self", email=email, is_active=is_active)
            await self.identity_service.link_sso_identity(
                account_id="acct-self",
                email=email,
                provider=provider,
                subject=subject,
            )
            login = await self.identity_service.create_login_result_for_account("acct-self")
        else:
            login = SimpleNamespace(
                session_token="session-acct-self",
                context=SimpleNamespace(account_id="acct-self"),
            )
        if self.identity_service is not None:
            await self.identity_service.mark_last_login("acct-self")
        provisioning = SimpleNamespace(
            account_id="acct-self",
            email=email,
            organization_id="org-sandbox",
            team_id="team-self-serve",
            user_id="acct-self",
            team_role="team_developer",
            account_is_active=is_active,
        )
        return SimpleNamespace(provisioning=provisioning, login=login)


class _RecordingAuditService:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.payloads: list[list[object]] = []

    async def record_event_sync(self, event, *, payloads=None):  # noqa: ANN001, ANN201
        self.events.append(event)
        self.payloads.append(list(payloads or []))
        return event

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        del critical
        self.events.append(event)
        self.payloads.append(list(payloads or []))
        return None


class _StubMasterSessionService:
    def __init__(self, *, master_key: str) -> None:
        self.master_key = master_key
        self.active_tokens: set[str] = set()
        self.created_tokens: list[str] = []
        self.revoked_tokens: list[str] = []
        self.validation_unavailable = False
        self.create_unavailable = False
        self.revoke_unavailable = False

    async def create_session(self, *, master_key: str, ttl_seconds: int) -> str:
        assert master_key == self.master_key
        assert ttl_seconds > 0
        if self.create_unavailable:
            raise MasterSessionStoreUnavailable("database unavailable")
        token = f"dms_test_session_{len(self.created_tokens) + 1}"
        self.created_tokens.append(token)
        self.active_tokens.add(token)
        return token

    async def validate_session(self, token: str | None, *, master_key: str | None) -> MasterSessionStatus:
        if self.validation_unavailable:
            return MasterSessionStatus.UNAVAILABLE
        if master_key != self.master_key or token not in self.active_tokens:
            return MasterSessionStatus.INVALID
        return MasterSessionStatus.ACTIVE

    async def revoke_session(self, token: str | None) -> None:
        if self.revoke_unavailable:
            raise MasterSessionStoreUnavailable("database unavailable")
        if token:
            self.revoked_tokens.append(token)
            self.active_tokens.discard(token)


def _configure_master_session(test_app, master_key: str = "mk-browser-session") -> _StubMasterSessionService:
    setattr(test_app.state.settings, "master_key", master_key)
    test_app.state.salt_key = "master-session-test-salt"
    service = _StubMasterSessionService(master_key=master_key)
    test_app.state.master_session_service = service
    return service


def _self_registration_app_config(
    *,
    enabled: bool,
    allowed_domains: list[str] | None = None,
    admin_emails: list[str] | None = None,
    require_admin_approval: bool = False,
    mode: str = "sso_allowed_domain",
) -> AppConfig:
    general_settings: dict[str, object] = {
        "sso_provider": "oidc",
        "sso_admin_email_list": admin_emails or [],
        "auth_session_ttl_hours": 12,
        "self_registration": {"enabled": False},
    }
    if enabled:
        general_settings["self_registration"] = {
            "enabled": True,
            "mode": mode,
            "allowed_domains": allowed_domains or ["example.com"],
            "require_admin_approval": require_admin_approval,
            "default_org": {"id": "org-sandbox"},
            "default_team": {"id": "team-self-serve"},
        }
    payload: dict[str, object] = {"general_settings": general_settings}
    return AppConfig.model_validate(payload)


async def _start_sso_and_callback(
    *,
    client,
    test_app,
    state: str,
    email: str,
    subject: str = "subject-1",
    email_verified: bool | None = True,
):
    test_app.state.sso_auth_handler = _StubSSOHandler(
        email=email,
        subject=subject,
        email_verified=email_verified,
    )
    test_app.state.sso_state_store = SSOStateStore(redis_client=test_app.state.redis, ttl_seconds=600)

    login = await client.get("/auth/login", params={"state": state})
    assert login.status_code == 200
    return await client.get("/auth/callback", params={"code": "oauth-code", "state": state})


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
async def test_master_key_login_persists_as_http_only_cookie_session(client, test_app):
    service = _configure_master_session(test_app)

    login = await client.post("/auth/master/login", json={"master_key": "mk-browser-session"})

    assert login.status_code == 200
    assert login.json()["auth_mode"] == "master_key"
    set_cookie = login.headers.get("set-cookie", "")
    assert "deltallm_master_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "mk-browser-session" not in set_cookie
    assert login.headers["cache-control"] == "no-store"
    issued_token = login.cookies.get("deltallm_master_session")
    assert issued_token in service.active_tokens

    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert me.json()["auth_mode"] == "master_key"
    assert me.json()["role"] == "platform_admin"
    assert me.json()["ui_access"]["settings"] is True
    assert me.headers["cache-control"] == "no-store"
    assert me.headers["vary"] == "Cookie"

    protected = await client.get("/ui/api/models")
    assert protected.status_code == 200
    scope_protected = await client.get("/ui/api/batches/feature-status")
    assert scope_protected.status_code == 200

    logout = await client.post("/auth/internal/logout")
    assert logout.status_code == 200
    assert "deltallm_master_session=" in logout.headers.get("set-cookie", "")
    assert issued_token in service.revoked_tokens
    assert issued_token not in service.active_tokens

    replay_headers = {"Cookie": f"deltallm_master_session={issued_token}"}
    after_logout = await client.get("/auth/me", headers=replay_headers)
    assert after_logout.status_code == 200
    assert after_logout.json()["authenticated"] is False
    replay_protected = await client.get("/ui/api/models", headers=replay_headers)
    assert replay_protected.status_code == 401


@pytest.mark.asyncio
async def test_master_key_login_rejects_invalid_key_and_secures_forwarded_https_cookie(client, test_app):
    _configure_master_session(test_app)

    invalid = await client.post("/auth/master/login", json={"master_key": "wrong"})
    assert invalid.status_code == 401
    assert "deltallm_master_session=" not in invalid.headers.get("set-cookie", "")

    secure = await client.post(
        "/auth/master/login",
        headers={"X-Forwarded-Proto": "https"},
        json={"master_key": "mk-browser-session"},
    )
    assert secure.status_code == 200
    assert "Secure" in secure.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_master_key_login_revokes_replaced_browser_session(client, test_app):
    service = _configure_master_session(test_app)

    first = await client.post("/auth/master/login", json={"master_key": "mk-browser-session"})
    first_token = first.cookies.get("deltallm_master_session")
    second = await client.post("/auth/master/login", json={"master_key": "mk-browser-session"})
    second_token = second.cookies.get("deltallm_master_session")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first_token != second_token
    assert first_token in service.revoked_tokens
    assert first_token not in service.active_tokens
    assert second_token in service.active_tokens


@pytest.mark.asyncio
async def test_master_session_store_outage_is_not_treated_as_anonymous(client, test_app):
    service = _configure_master_session(test_app)
    login = await client.post("/auth/master/login", json={"master_key": "mk-browser-session"})
    assert login.status_code == 200

    service.validation_unavailable = True

    me = await client.get("/auth/me")
    protected = await client.get("/ui/api/models")
    break_glass = await client.get(
        "/ui/api/models",
        headers={"X-Master-Key": "mk-browser-session"},
    )

    assert me.status_code == 503
    assert me.json()["detail"] == "Authentication service unavailable"
    assert me.headers["cache-control"] == "no-store"
    assert me.headers["retry-after"] == "5"
    assert me.headers["vary"] == "Cookie"
    assert protected.status_code == 503
    assert break_glass.status_code == 200


@pytest.mark.asyncio
async def test_platform_session_remains_available_during_master_session_store_outage(client, test_app):
    service = _configure_master_session(test_app)
    login = await client.post("/auth/master/login", json={"master_key": "mk-browser-session"})
    master_token = login.cookies.get("deltallm_master_session")
    service.validation_unavailable = True

    class _PlatformIdentityService:
        async def get_context_for_session(self, token: str) -> PlatformAuthContext:
            assert token == "platform-session-token"
            return PlatformAuthContext(
                account_id="acct-platform-admin",
                email="admin@example.com",
                role="platform_admin",
                mfa_enabled=False,
                mfa_verified=True,
            )

    test_app.state.platform_identity_service = _PlatformIdentityService()
    headers = {
        "Cookie": (
            f"deltallm_master_session={master_token}; "
            "deltallm_session=platform-session-token"
        )
    }

    me = await client.get("/auth/me", headers=headers)
    protected = await client.get("/ui/api/models", headers=headers)

    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert me.json()["auth_mode"] == "session"
    assert protected.status_code == 200


@pytest.mark.asyncio
async def test_master_logout_failure_preserves_browser_session(client, test_app):
    service = _configure_master_session(test_app)
    login = await client.post("/auth/master/login", json={"master_key": "mk-browser-session"})
    issued_token = login.cookies.get("deltallm_master_session")
    service.revoke_unavailable = True

    logout = await client.post("/auth/internal/logout")

    assert logout.status_code == 503
    assert logout.json()["detail"] == "Authentication service unavailable"
    assert "deltallm_master_session=" not in logout.headers.get("set-cookie", "")
    assert issued_token in service.active_tokens
    assert client.cookies.get("deltallm_master_session") == issued_token


@pytest.mark.asyncio
async def test_master_login_store_failure_does_not_issue_cookie(client, test_app):
    service = _configure_master_session(test_app)
    service.create_unavailable = True

    response = await client.post("/auth/master/login", json={"master_key": "mk-browser-session"})

    assert response.status_code == 503
    assert "deltallm_master_session=" not in response.headers.get("set-cookie", "")


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

    return_to = "/models/deployment-1?tab=usage#cost"
    login = await client.get("/auth/login", params={"state": "abc", "return_to": return_to})
    assert login.status_code == 200
    assert "sso.example.com" in login.json()["authorize_url"]

    callback = await client.get("/auth/callback", params={"code": "oauth-code", "state": "abc"})
    assert callback.status_code == 302
    assert callback.headers["location"] == return_to
    assert "deltallm_session=session-token" in callback.headers.get("set-cookie", "")

    replay = await client.get("/auth/callback", params={"code": "oauth-code", "state": "abc"})
    assert replay.status_code == 400
    assert replay.json()["detail"] == "Invalid or expired SSO state"

    unsafe_login = await client.get(
        "/auth/login",
        params={"state": "unsafe", "return_to": "//evil.example/path"},
    )
    assert unsafe_login.status_code == 200
    unsafe_callback = await client.get(
        "/auth/callback",
        params={"code": "oauth-code", "state": "unsafe"},
    )
    assert unsafe_callback.status_code == 302
    assert unsafe_callback.headers["location"] == "/"

    loop_login = await client.get(
        "/auth/login",
        params={"state": "loop", "return_to": "/login/?returnTo=/models"},
    )
    assert loop_login.status_code == 200
    loop_callback = await client.get(
        "/auth/callback",
        params={"code": "oauth-code", "state": "loop"},
    )
    assert loop_callback.status_code == 302
    assert loop_callback.headers["location"] == "/"


@pytest.mark.asyncio
async def test_sso_callback_self_registration_disabled_uses_legacy_sso_path(client, test_app):
    identity = _StubIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(enabled=False)

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="legacy-state",
        email="User@Example.com",
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert "deltallm_session=session-acct-legacy" in response.headers.get("set-cookie", "")
    assert len(identity.legacy_upserts) == 1
    assert identity.legacy_upserts[0]["email"] == "user@example.com"
    assert identity.last_logins == ["acct-legacy"]
    assert provisioner.calls == []


@pytest.mark.asyncio
async def test_sso_callback_self_registration_disabled_maps_session_creation_failure(client, test_app):
    class FailingSessionIdentityService(_StubIdentityService):
        async def upsert_sso_account(self, **kwargs):  # noqa: ANN003, ANN201
            self.legacy_upserts.append(dict(kwargs))
            raise LoginSessionCreationError("Failed to establish session")

    identity = FailingSessionIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(enabled=False)

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="legacy-session-failure",
        email="User@Example.com",
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to establish session"
    assert len(identity.legacy_upserts) == 1
    assert identity.last_logins == []
    assert provisioner.calls == []


@pytest.mark.asyncio
async def test_sso_callback_self_registration_disabled_maps_identity_conflict(client, test_app):
    class ConflictingIdentityService(_StubIdentityService):
        async def upsert_sso_account(self, **kwargs):  # noqa: ANN003, ANN201
            self.legacy_upserts.append(dict(kwargs))
            raise ValueError("SSO email is already linked to another account")

    identity = ConflictingIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(enabled=False)

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="legacy-identity-conflict",
        email="User@Example.com",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "SSO email is already linked to another account"
    assert len(identity.legacy_upserts) == 1
    assert identity.last_logins == []
    assert provisioner.calls == []


@pytest.mark.asyncio
async def test_sso_callback_self_registration_provisions_allowed_domain_user(client, test_app):
    identity = _StubIdentityService()
    provisioner = _StubSelfRegistrationProvisioner(identity_service=identity)
    audit = _RecordingAuditService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.audit_service = audit
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-allowed",
        email="Developer@Example.com",
        subject="oidc-subject-1",
    )

    assert response.status_code == 302
    assert "deltallm_session=session-acct-self" in response.headers.get("set-cookie", "")
    assert provisioner.calls == [
        {
            "email": "developer@example.com",
            "settings": test_app.state.app_config.general_settings.self_registration,
            "is_active": True,
        }
    ]
    assert identity.identity_links == [
        {
            "account_id": "acct-self",
            "email": "developer@example.com",
            "provider": "oidc",
            "subject": "oidc-subject-1",
        }
    ]
    assert identity.last_logins == ["acct-self"]
    assert AuditAction.AUTH_SELF_REGISTRATION_ACCEPTED.value in [event.action for event in audit.events]


@pytest.mark.asyncio
async def test_sso_callback_self_registration_blocks_unapproved_domain(client, test_app):
    identity = _StubIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    audit = _RecordingAuditService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.audit_service = audit
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-blocked",
        email="developer@blocked.example",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Email domain is not allowed for self-registration"
    assert provisioner.calls == []
    assert identity.identity_links == []
    assert AuditAction.AUTH_SELF_REGISTRATION_BLOCKED.value in [event.action for event in audit.events]


@pytest.mark.asyncio
async def test_sso_callback_self_registration_blocks_unverified_email_when_required(client, test_app):
    identity = _StubIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    audit = _RecordingAuditService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.audit_service = audit
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-unverified-email",
        email="developer@example.com",
        email_verified=False,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "SSO email is not verified"
    assert provisioner.calls == []
    assert identity.identity_links == []
    assert AuditAction.AUTH_SELF_REGISTRATION_BLOCKED.value in [event.action for event in audit.events]


@pytest.mark.asyncio
async def test_sso_callback_self_registration_fails_closed_when_admin_approval_is_required(client, test_app):
    identity = _StubIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    audit = _RecordingAuditService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.audit_service = audit
    test_app.state.app_config = _self_registration_app_config(
        enabled=True,
        allowed_domains=["example.com"],
        require_admin_approval=True,
    )

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-approval-required",
        email="developer@example.com",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Self-registration requires admin approval"
    assert provisioner.calls == []
    assert identity.identity_links == []
    assert AuditAction.AUTH_SELF_REGISTRATION_BLOCKED.value in [event.action for event in audit.events]


@pytest.mark.asyncio
async def test_sso_callback_self_registration_existing_account_skips_provisioning_and_domain_gate(client, test_app):
    identity = _StubIdentityService()
    identity.add_account(account_id="acct-existing", email="developer@blocked.example")
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-existing",
        email="developer@blocked.example",
        subject="existing-subject",
    )

    assert response.status_code == 302
    assert "deltallm_session=session-acct-existing" in response.headers.get("set-cookie", "")
    assert provisioner.calls == []
    assert identity.legacy_upserts == []
    assert identity.identity_links == [
        {
            "account_id": "acct-existing",
            "email": "developer@blocked.example",
            "provider": "oidc",
            "subject": "existing-subject",
        }
    ]
    assert identity.sso_login_calls == [
        {
            "account_id": "acct-existing",
            "email": "developer@blocked.example",
            "provider": "oidc",
            "subject": "existing-subject",
        }
    ]
    assert identity.last_logins == ["acct-existing"]


@pytest.mark.asyncio
async def test_sso_callback_self_registration_existing_identity_reconciles_changed_email(client, test_app):
    identity = _StubIdentityService()
    identity.add_account(account_id="acct-existing", email="old@example.com")
    identity.identities[("oidc", "existing-subject")] = "acct-existing"
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-existing-email-change",
        email="Developer@Example.com",
        subject="existing-subject",
    )

    assert response.status_code == 302
    assert "deltallm_session=session-acct-existing" in response.headers.get("set-cookie", "")
    assert provisioner.calls == []
    assert identity.accounts["acct-existing"]["email"] == "developer@example.com"
    assert identity.identity_links == [
        {
            "account_id": "acct-existing",
            "email": "developer@example.com",
            "provider": "oidc",
            "subject": "existing-subject",
        }
    ]
    assert identity.sso_login_calls == [
        {
            "account_id": "acct-existing",
            "email": "developer@example.com",
            "provider": "oidc",
            "subject": "existing-subject",
        }
    ]
    assert identity.last_logins == ["acct-existing"]


@pytest.mark.asyncio
async def test_sso_callback_self_registration_existing_account_requires_atomic_login_capability(client, test_app):
    class MissingAtomicSSOLoginIdentityService:
        def __init__(self) -> None:
            self.accounts = {
                "acct-existing": {
                    "account_id": "acct-existing",
                    "email": "developer@example.com",
                    "role": "org_user",
                    "is_active": True,
                }
            }

        def normalize_email(self, email: str | None) -> str:
            return str(email or "").strip().lower()

        async def get_account_by_sso_identity(self, *, provider: str, subject: str):  # noqa: ANN001, ANN201
            del provider
            del subject
            return None

        async def get_account_by_email(self, email: str):  # noqa: ANN201
            normalized_email = self.normalize_email(email)
            for account in self.accounts.values():
                if str(account.get("email") or "").lower() == normalized_email:
                    return dict(account)
            return None

    identity = MissingAtomicSSOLoginIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-existing-missing-atomic-login",
        email="developer@example.com",
        subject="existing-subject",
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Auth service unavailable"
    assert provisioner.calls == []
    assert identity.accounts["acct-existing"]["email"] == "developer@example.com"


@pytest.mark.asyncio
async def test_sso_callback_self_registration_rejects_existing_identity_email_owned_by_other_account(client, test_app):
    identity = _StubIdentityService()
    identity.add_account(account_id="acct-existing", email="old@example.com")
    identity.add_account(account_id="acct-other", email="developer@example.com")
    identity.identities[("oidc", "existing-subject")] = "acct-existing"
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-existing-email-conflict",
        email="developer@example.com",
        subject="existing-subject",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "SSO email is already linked to another account"
    assert provisioner.calls == []
    assert identity.accounts["acct-existing"]["email"] == "old@example.com"
    assert identity.identity_links == []
    assert identity.login_results == []


@pytest.mark.asyncio
async def test_sso_callback_self_registration_existing_account_inactive_service_error_returns_401(client, test_app):
    class InactiveDuringLoginIdentityService(_StubIdentityService):
        async def create_sso_login_for_existing_account(self, **kwargs):  # noqa: ANN003, ANN201
            self.sso_login_calls.append(
                {
                    "account_id": str(kwargs["account_id"]),
                    "email": self.normalize_email(str(kwargs["email"])),
                    "provider": str(kwargs.get("provider") or "sso"),
                    "subject": str(kwargs.get("subject") or kwargs["email"]),
                }
            )
            raise AccountInactiveError("Account is inactive")

    identity = InactiveDuringLoginIdentityService()
    identity.add_account(account_id="acct-existing", email="developer@example.com", is_active=True)
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-existing-inactive-race",
        email="developer@example.com",
        subject="existing-subject",
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Account is inactive"
    assert provisioner.calls == []
    assert identity.identity_links == []
    assert identity.login_results == []
    assert identity.last_logins == []
    assert identity.sso_login_calls == [
        {
            "account_id": "acct-existing",
            "email": "developer@example.com",
            "provider": "oidc",
            "subject": "existing-subject",
        }
    ]


@pytest.mark.asyncio
async def test_sso_callback_self_registration_blocks_unverified_email_before_linking_existing_account(client, test_app):
    identity = _StubIdentityService()
    identity.add_account(account_id="acct-existing", email="developer@example.com")
    provisioner = _StubSelfRegistrationProvisioner()
    audit = _RecordingAuditService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.audit_service = audit
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-existing-unverified",
        email="developer@example.com",
        subject="unlinked-subject",
        email_verified=False,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "SSO email is not verified"
    assert provisioner.calls == []
    assert identity.identity_links == []
    assert identity.login_results == []
    assert AuditAction.AUTH_SELF_REGISTRATION_BLOCKED.value in [event.action for event in audit.events]


@pytest.mark.asyncio
async def test_sso_callback_self_registration_provisioning_failure_is_audited(client, test_app):
    identity = _StubIdentityService()
    provisioner = _StubSelfRegistrationProvisioner(error=RuntimeError("database unavailable"))
    audit = _RecordingAuditService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.audit_service = audit
    test_app.state.app_config = _self_registration_app_config(enabled=True, allowed_domains=["example.com"])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-failure",
        email="developer@example.com",
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to provision self-registration account"
    assert provisioner.calls
    assert AuditAction.AUTH_SELF_REGISTRATION_PROVISIONING_FAILED.value in [event.action for event in audit.events]


@pytest.mark.asyncio
async def test_sso_callback_platform_admin_email_does_not_use_self_registration(client, test_app):
    identity = _StubIdentityService()
    provisioner = _StubSelfRegistrationProvisioner()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.app_config = _self_registration_app_config(
        enabled=True,
        allowed_domains=["example.com"],
        admin_emails=["admin@other.example"],
    )

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="self-reg-admin",
        email="Admin@Other.Example",
    )

    assert response.status_code == 302
    assert provisioner.calls == []
    assert len(identity.legacy_upserts) == 1
    assert identity.legacy_upserts[0]["email"] == "admin@other.example"
    assert identity.legacy_upserts[0]["is_platform_admin"] is True


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
async def test_sso_config_reports_disabled_when_handler_is_unavailable(client, test_app):
    test_app.state.sso_auth_handler = None
    test_app.state.sso_state_store = None

    response = await client.get("/auth/sso-config")

    assert response.status_code == 200
    assert response.json() == {
        "sso_enabled": False,
        "self_registration": {
            "enabled": False,
            "mode": None,
            "sandbox_access_enabled": False,
        },
    }


@pytest.mark.asyncio
async def test_sso_config_reports_self_registration_sandbox_status(client, test_app):
    test_app.state.sso_auth_handler = object()
    test_app.state.app_config = _self_registration_app_config(
        enabled=True,
        allowed_domains=["example.com"],
    )

    response = await client.get("/auth/sso-config")

    assert response.status_code == 200
    assert response.json() == {
        "sso_enabled": True,
        "provider": "oidc",
        "self_registration": {
            "enabled": True,
            "mode": "sso_allowed_domain",
            "sandbox_access_enabled": True,
        },
    }


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

        async def count_delivery_audits_by_status(self) -> dict[str, int]:
            return {}

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
