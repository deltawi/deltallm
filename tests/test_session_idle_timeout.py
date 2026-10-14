from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.config import GeneralSettings
from src.models.platform_auth import PlatformAuthContext
from src.services.identity.master_session_service import (
    MasterSessionCheck,
    MasterSessionStatus,
)


def test_idle_timeout_settings_defaults_and_bounds() -> None:
    settings = GeneralSettings()
    assert settings.auth_session_idle_timeout_minutes == 60
    assert settings.auth_master_session_ttl_hours is None
    assert (
        GeneralSettings(auth_session_idle_timeout_minutes=0).auth_session_idle_timeout_minutes == 0
    )
    for invalid in (-1, 3, 1441):
        with pytest.raises(ValidationError):
            GeneralSettings(auth_session_idle_timeout_minutes=invalid)
    with pytest.raises(ValidationError):
        GeneralSettings(auth_master_session_ttl_hours=0)


class _RecordingIdentityService:
    def __init__(self, context: PlatformAuthContext | None) -> None:
        self.context = context
        self.calls: list[dict[str, object]] = []

    async def get_context_for_session(self, token: str, **kwargs: object):
        self.calls.append({"token": token, **kwargs})
        return self.context


def _operator(**overrides: object) -> PlatformAuthContext:
    values: dict[str, object] = {
        "account_id": "acct-1",
        "email": "operator@example.com",
        "role": "platform_admin",
        "mfa_verified": True,
    }
    values.update(overrides)
    return PlatformAuthContext(**values)


@pytest.mark.asyncio
async def test_requests_renew_sessions_unless_marked_background(client, test_app) -> None:
    test_app.state.app_config.general_settings.auth_session_idle_timeout_minutes = 30
    service = _RecordingIdentityService(_operator())
    test_app.state.platform_identity_service = service
    cookies = {"deltallm_session": "session-token"}

    await client.get("/auth/me", cookies=cookies)
    await client.get("/auth/me", cookies=cookies, headers={"X-DeltaLLM-Background": "1"})
    await client.get("/ui/api/models", cookies=cookies, headers={"X-DeltaLLM-Background": "1"})

    assert [(call["idle_timeout_seconds"], call["renew"]) for call in service.calls] == [
        (1800, True),
        (1800, False),
        (1800, False),
    ]


@pytest.mark.asyncio
async def test_auth_me_reports_idle_deadline_for_operator_sessions(client, test_app) -> None:
    test_app.state.app_config.general_settings.auth_session_idle_timeout_minutes = 60
    expires_at = datetime.now(UTC) + timedelta(hours=10)
    idle_expires_at = datetime.now(UTC) + timedelta(minutes=45)
    test_app.state.platform_identity_service = _RecordingIdentityService(
        _operator(session_expires_at=expires_at, session_idle_expires_at=idle_expires_at)
    )

    me = await client.get("/auth/me", cookies={"deltallm_session": "session-token"})

    body = me.json()
    assert body["authenticated"] is True
    assert body["idle_timeout_seconds"] == 3600
    assert datetime.fromisoformat(body["idle_expires_at"]) == idle_expires_at
    assert datetime.fromisoformat(body["expires_at"]) == expires_at
    server_time = datetime.fromisoformat(body["server_time"])
    assert abs((server_time - datetime.now(UTC)).total_seconds()) < 60


@pytest.mark.asyncio
async def test_expired_session_is_anonymous_and_api_calls_get_401(client, test_app) -> None:
    test_app.state.app_config.general_settings.auth_session_idle_timeout_minutes = 60
    test_app.state.platform_identity_service = _RecordingIdentityService(None)
    cookies = {"deltallm_session": "idle-session-token"}

    me = await client.get("/auth/me", cookies=cookies)
    protected = await client.get("/ui/api/models", cookies=cookies)

    assert me.status_code == 200
    assert me.json() == {**me.json(), "authenticated": False}
    assert protected.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_reports_master_session_deadlines(client, test_app) -> None:
    test_app.state.app_config.general_settings.auth_session_idle_timeout_minutes = 30
    setattr(test_app.state.settings, "master_key", "mk-idle")
    expires_at = datetime.now(UTC) + timedelta(hours=3)
    idle_expires_at = datetime.now(UTC) + timedelta(minutes=29)
    calls: list[dict[str, object]] = []

    class _MasterSessions:
        async def check_session(self, token, *, master_key, **kwargs):  # noqa: ANN001, ANN201
            calls.append({"token": token, "master_key": master_key, **kwargs})
            return MasterSessionCheck(MasterSessionStatus.ACTIVE, expires_at, idle_expires_at)

    test_app.state.master_session_service = _MasterSessions()

    me = await client.get(
        "/auth/me",
        cookies={"deltallm_master_session": "dms_token"},
        headers={"X-DeltaLLM-Background": "1"},
    )

    body = me.json()
    assert body["auth_mode"] == "master_key"
    assert body["idle_timeout_seconds"] == 1800
    assert datetime.fromisoformat(body["expires_at"]) == expires_at
    assert datetime.fromisoformat(body["idle_expires_at"]) == idle_expires_at
    assert calls == [
        {
            "token": "dms_token",
            "master_key": "mk-idle",
            "idle_timeout_seconds": 1800,
            "renew": False,
        }
    ]


@pytest.mark.asyncio
async def test_master_login_uses_its_own_hard_cap(client, test_app) -> None:
    test_app.state.app_config.general_settings.auth_master_session_ttl_hours = 4
    setattr(test_app.state.settings, "master_key", "mk-cap")
    test_app.state.salt_key = "master-session-test-salt"
    ttls: list[int] = []

    class _MasterSessions:
        async def create_session(self, *, master_key: str, ttl_seconds: int) -> str:
            ttls.append(ttl_seconds)
            return "dms_capped"

        async def check_session(self, token, **_kwargs):  # noqa: ANN001, ANN201
            return MasterSessionCheck(MasterSessionStatus.MISSING)

    test_app.state.master_session_service = _MasterSessions()

    login = await client.post("/auth/master/login", json={"master_key": "mk-cap"})

    assert login.status_code == 200
    assert ttls == [4 * 3600]
    assert "Max-Age=14400" in login.headers["set-cookie"]
