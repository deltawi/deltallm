"""Admin and user routes for OAuth 2.0 (per user) MCP servers. Fake HTTP only."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from src.audit.actions import AuditAction
from src.mcp.health import MCPHealthProbe
from src.mcp.oauth.service import MCPOAuthService
from src.models.platform_auth import PlatformAuthContext
from tests.mcp.oauth_fakes import BASE_URL, OAuthHarness
from tests.test_ui_mcp import (
    _FakeMCPRegistryService,
    _FakeMCPRepository,
    _FakeTransportClient,
    _RecordingAuditService,
)

MASTER = {"Authorization": "Bearer mk-test"}
ACCOUNT = "acct-1"


class _Env:
    def __init__(self, test_app, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
        setattr(test_app.state.settings, "master_key", "mk-test")
        self.monkeypatch = monkeypatch
        self.repository = _FakeMCPRepository()
        self.registry = _FakeMCPRegistryService(self.repository)
        self.transport = _RecordingTokenTransport()
        self.audit = _RecordingAuditService()
        self.harness = OAuthHarness()
        mcp_repository = self.repository

        oauth_repository = self.harness.repository
        original_store = oauth_repository.store_server_oauth_config

        async def store(*, server_id: str, auth_config: dict[str, Any]) -> bool:
            await original_store(server_id=server_id, auth_config=auth_config)
            server = mcp_repository.servers.get(server_id)
            if server is None or server.auth_mode != "oauth2":
                return False
            mcp_repository.servers[server_id] = replace(server, auth_config=auth_config)
            return True

        oauth_repository.store_server_oauth_config = store  # type: ignore[method-assign]
        self.service = MCPOAuthService(
            repository=oauth_repository,
            protocol=self.harness.service.protocol,
            cipher=self.harness.cipher,
            public_base_url=lambda: BASE_URL,
            server_loader=self.repository.get_server,
        )
        test_app.state.mcp_repository = self.repository
        test_app.state.mcp_registry_service = self.registry
        test_app.state.mcp_transport_client = self.transport
        test_app.state.mcp_health_probe = MCPHealthProbe(self.registry, self.transport)
        test_app.state.audit_service = self.audit
        test_app.state.mcp_oauth_service = self.service

    def sign_in(self, *, role: str = "platform_admin", account_id: str = ACCOUNT) -> None:
        context = PlatformAuthContext(account_id=account_id, email="dana@example.com", role=role)
        for target in (
            "src.middleware.platform_auth.get_platform_auth_context",
            "src.middleware.admin.get_platform_auth_context",
            "src.api.admin.endpoints.mcp.routes.connections.get_platform_auth_context",
        ):
            self.monkeypatch.setattr(target, lambda request, context=context: context)

    async def create_server(self, client, **overrides: Any) -> dict[str, Any]:  # noqa: ANN001
        payload = {
            "server_key": "github",
            "name": "GitHub",
            "transport": "streamable_http",
            "base_url": "https://mcp.example.com/mcp",
            "auth_mode": "oauth2",
            "auth_config": {"client_id": "", "scopes": "repo read:user"},
            **overrides,
        }
        response = await client.post("/ui/api/mcp-servers", headers=MASTER, json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    async def connect(self, client) -> str:  # noqa: ANN001
        start = await client.post("/ui/api/mcp-connections/github/connect")
        assert start.status_code == 200, start.text
        query = parse_qs(urlsplit(start.json()["authorization_url"]).query)
        callback = await client.get(
            "/mcp/oauth/callback", params={"state": query["state"][0], "code": "good-code"}
        )
        assert callback.status_code == 303
        return callback.headers["location"]


class _RecordingTokenTransport(_FakeTransportClient):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[str | None] = []
        self.tools_by_server["github"] = list(self.tools_by_server["docs"])

    async def initialize(self, server, *, request_headers=None):  # noqa: ANN001, ANN201
        self.tokens.append(server.oauth_access_token)
        return await super().initialize(server, request_headers=request_headers)


async def test_create_oauth2_server_runs_discovery_and_hides_the_secret(
    client, test_app, monkeypatch
):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    created = await env.create_server(
        client,
        auth_config={"client_id": "own-client", "client_secret": "s3cret", "scopes": ["repo"]},
    )

    oauth = created["oauth"]
    assert oauth["client_id"] == "own-client"
    assert oauth["client_secret_set"] is True
    assert oauth["discovery"]["authorization_server"] == "https://auth.example.com"
    assert oauth["discovery_error"] is None
    assert created["auth_credentials_present"] is True
    assert "s3cret" not in str(created)
    stored = env.repository.servers[created["mcp_server_id"]].auth_config
    assert "s3cret" not in str(stored)

    listing = await client.get("/ui/api/mcp-servers", headers=MASTER)
    assert "s3cret" not in listing.text
    assert "client_secret_ciphertext" not in listing.text


async def test_dynamic_registration_on_save_and_discovery_run_again(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    created = await env.create_server(client)
    assert created["oauth"]["client_id"] == "dyn-client-1"
    assert created["oauth"]["client_id_source"] == "dynamic"

    again = await client.post(
        f"/ui/api/mcp-servers/{created['mcp_server_id']}/oauth/discovery", headers=MASTER
    )
    assert again.status_code == 200, again.text
    assert again.json()["oauth"]["discovery"]["token_endpoint"] == "https://auth.example.com/token"
    actions = [call[0].action for call in env.audit.sync_calls]
    assert AuditAction.ADMIN_MCP_SERVER_OAUTH_DISCOVERY in actions


async def test_discovery_error_is_shown_and_does_not_block_the_save(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    env.harness.upstream.serve_oauth_metadata = False
    created = await env.create_server(client, auth_config={"client_id": "cid"})

    assert created["oauth"]["discovery"] is None
    assert created["oauth"]["discovery_error"]


async def test_settings_and_discovery_preview(client, test_app, monkeypatch):  # noqa: ANN001
    _Env(test_app, monkeypatch)
    settings = await client.get("/ui/api/mcp-oauth/settings", headers=MASTER)
    assert settings.json() == {
        "redirect_url": f"{BASE_URL}/mcp/oauth/callback",
        "public_base_url_configured": True,
        "encryption_configured": True,
    }

    preview = await client.post(
        "/ui/api/mcp-servers/oauth/discover",
        headers=MASTER,
        json={"base_url": "https://mcp.example.com/mcp"},
    )
    assert preview.status_code == 200
    assert preview.json()["discovery"]["found_from"] == "WWW-Authenticate header"


async def test_oauth2_server_needs_encryption_key(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    env.service.cipher = None
    response = await client.post(
        "/ui/api/mcp-servers",
        headers=MASTER,
        json={
            "server_key": "github",
            "name": "GitHub",
            "base_url": "https://mcp.example.com/mcp",
            "auth_mode": "oauth2",
            "auth_config": {"client_id": "cid"},
        },
    )
    assert response.status_code == 400
    assert "mcp_oauth_encryption_key" in response.json()["detail"]


async def test_connect_callback_list_and_disconnect(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    await env.create_server(client)
    env.sign_in()

    before = await client.get("/ui/api/mcp-connections")
    assert before.json()["account_connectable"] is True
    assert [(row["server_key"], row["status"]) for row in before.json()["data"]] == [
        ("github", "not_connected")
    ]

    location = await env.connect(client)
    assert location == f"{BASE_URL}/ui/connect/github?result=connected"

    after = (await client.get("/ui/api/mcp-connections")).json()["data"][0]
    assert after["status"] == "connected"
    assert after["account_label"] == "dana-k"
    assert after["renews_automatically"] is True
    assert after["expires_at"]

    delete = await client.delete("/ui/api/mcp-connections/github")
    assert delete.json() == {"deleted": True, "revoked": True, "server_key": "github"}
    assert env.harness.upstream.calls("/revoke", "POST")
    rows = (await client.get("/ui/api/mcp-connections")).json()["data"]
    assert rows[0]["status"] == "not_connected"
    missing = await client.delete("/ui/api/mcp-connections/github")
    assert missing.status_code == 404


async def test_callback_rejects_a_state_from_another_account(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    await env.create_server(client)
    env.sign_in(account_id="acct-1")
    start = await client.post("/ui/api/mcp-connections/github/connect")
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]

    env.sign_in(account_id="acct-2")
    callback = await client.get("/mcp/oauth/callback", params={"state": state, "code": "good-code"})

    assert callback.status_code == 303
    assert callback.headers["location"] == (
        f"{BASE_URL}/connected-accounts?result=error&reason=wrong_account"
    )
    assert callback.headers["cache-control"] == "no-store"
    assert env.harness.repository.connections == {}


async def test_callback_without_session_asks_to_sign_in(client, test_app, monkeypatch):  # noqa: ANN001
    _Env(test_app, monkeypatch)
    callback = await client.get("/mcp/oauth/callback", params={"state": "x", "code": "y"})
    assert callback.status_code == 303
    assert "reason=sign_in_required" in callback.headers["location"]


async def test_master_key_cannot_connect(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    await env.create_server(client)
    response = await client.post("/ui/api/mcp-connections/github/connect", headers=MASTER)
    assert response.status_code == 403


async def test_user_without_access_does_not_see_the_server(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    await env.create_server(client)
    env.sign_in(role="org_user")

    listing = await client.get("/ui/api/mcp-connections")
    assert listing.json()["data"] == []
    start = await client.post("/ui/api/mcp-connections/github/connect")
    assert start.status_code == 404


async def test_refresh_capabilities_uses_the_admin_connection(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    created = await env.create_server(client)
    url = f"/ui/api/mcp-servers/{created['mcp_server_id']}/refresh-capabilities"

    master = await client.post(url, headers=MASTER)
    assert master.status_code == 409
    assert "master key" in master.json()["detail"]

    env.sign_in()
    not_connected = await client.post(url)
    assert not_connected.status_code == 409
    assert "Connect your GitHub account first" in not_connected.json()["detail"]

    await env.connect(client)
    refreshed = await client.post(url)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["server"]["tool_count"] == 1
    assert env.transport.tokens == ["access-1"]


async def test_new_base_url_deletes_user_connections(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    created = await env.create_server(client)
    env.sign_in()
    await env.connect(client)
    assert env.harness.repository.connections

    same = await client.patch(
        f"/ui/api/mcp-servers/{created['mcp_server_id']}", json={"name": "GitHub (work)"}
    )
    assert same.status_code == 200, same.text
    assert env.harness.repository.connections

    moved = await client.patch(
        f"/ui/api/mcp-servers/{created['mcp_server_id']}",
        json={"base_url": "https://mcp.example.com/v2/mcp"},
    )
    assert moved.status_code == 200, moved.text
    assert env.harness.repository.connections == {}


async def test_user_who_lost_access_can_disconnect_but_not_connect(client, test_app, monkeypatch):  # noqa: ANN001
    env = _Env(test_app, monkeypatch)
    created = await env.create_server(client)
    env.sign_in()
    await env.connect(client)
    env.sign_in(role="org_user")

    rows = (await client.get("/ui/api/mcp-connections")).json()["data"]
    assert [(row["server_key"], row["status"], row["connect_available"]) for row in rows] == [
        ("github", "connected", False)
    ]
    start = await client.post("/ui/api/mcp-connections/github/connect")
    assert start.status_code == 404
    delete = await client.delete("/ui/api/mcp-connections/github")
    assert delete.status_code == 200
    assert created["mcp_server_id"] not in {key[0] for key in env.harness.repository.connections}
