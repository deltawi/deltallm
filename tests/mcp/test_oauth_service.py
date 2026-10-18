from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from src.mcp.oauth.errors import (
    MCPConnectionRequiredError,
    MCPOAuthCallbackError,
    MCPOAuthConfigurationError,
)
from src.mcp.oauth.models import OAuth2ServerSettings
from src.secret_envelope import SecretEnvelopeCipher
from tests.mcp.oauth_fakes import (
    ACCOUNT,
    BASE_URL,
    FakeOAuthUpstream,
    OAuthHarness as _Harness,
)


async def test_save_runs_discovery_and_dynamic_registration() -> None:
    harness = _Harness()
    server = await harness.configured_server({"client_id": "", "scopes": "repo read:user"})
    settings = OAuth2ServerSettings.from_auth_config(server.auth_config)

    assert settings.client_id == "dyn-client-1"
    assert settings.client_id_source == "dynamic"
    assert settings.scopes == ("repo", "read:user")
    assert settings.discovery is not None and settings.discovery_error is None
    register = harness.upstream.calls("/register", "POST")[0].json()
    assert register["redirect_uris"] == [f"{BASE_URL}/mcp/oauth/callback"]


async def test_client_secret_is_encrypted_and_write_only() -> None:
    harness = _Harness()
    server = await harness.configured_server({"client_id": "own-client", "client_secret": "s3cret"})

    assert "s3cret" not in str(server.auth_config)
    public = OAuth2ServerSettings.from_auth_config(server.auth_config).to_public_json()
    assert public["client_secret_set"] is True
    assert "s3cret" not in str(public)
    assert harness.upstream.calls("/register") == []

    kept = await harness.service.prepare_server_auth_config(
        base_url=server.base_url,
        requested={"scopes": ["repo"]},
        existing=server.auth_config,
        existing_base_url=server.base_url,
    )
    assert kept["client_secret_ciphertext"] == server.auth_config["client_secret_ciphertext"]
    cleared = await harness.service.prepare_server_auth_config(
        base_url=server.base_url,
        requested={"clear_client_secret": True},
        existing=server.auth_config,
        existing_base_url=server.base_url,
    )
    assert cleared["client_secret_ciphertext"] is None


async def test_save_requires_encryption_key_and_public_url() -> None:
    no_key = _Harness(key=b"")
    with pytest.raises(MCPOAuthConfigurationError, match="mcp_oauth_encryption_key"):
        await no_key.configured_server()
    no_url = _Harness(base_url=None)
    with pytest.raises(MCPOAuthConfigurationError, match="email_base_url"):
        await no_url.configured_server()


async def test_discovery_failure_does_not_block_save() -> None:
    harness = _Harness(FakeOAuthUpstream(serve_oauth_metadata=False))
    server = await harness.configured_server()
    settings = OAuth2ServerSettings.from_auth_config(server.auth_config)
    assert settings.discovery is None
    assert settings.discovery_error


async def test_authorization_url_uses_pkce_s256_and_stores_hashed_state() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    url = await harness.service.start_authorization(server, account_id=ACCOUNT)

    parts = urlsplit(url)
    params = {key: values[0] for key, values in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://auth.example.com/authorize"
    assert params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == f"{BASE_URL}/mcp/oauth/callback"
    assert params["resource"] == "https://mcp.example.com/mcp"
    state_hash = hashlib.sha256(params["state"].encode()).hexdigest()
    stored = harness.repository.states[state_hash]
    assert params["state"] not in str(stored)
    verifier = harness.cipher.try_decrypt(
        stored.code_verifier_ciphertext,
        aad=f"deltallm:mcp-oauth:verifier:v1:{state_hash}".encode(),
    )
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).decode().rstrip("=")
    assert params["code_challenge"] == expected


async def test_start_refuses_server_without_pkce_s256() -> None:
    harness = _Harness(FakeOAuthUpstream(code_challenge_methods=["plain"]))
    server = await harness.configured_server()
    with pytest.raises(MCPOAuthConfigurationError, match="PKCE"):
        await harness.service.start_authorization(server, account_id=ACCOUNT)


async def test_callback_stores_encrypted_tokens_and_state_is_single_use() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    url = await harness.service.start_authorization(server, account_id=ACCOUNT)
    state = parse_qs(urlsplit(url).query)["state"][0]

    connected = await harness.service.complete_authorization(
        state=state, code="good-code", error=None, account_id=ACCOUNT
    )
    assert connected.server_key == "github"
    record = harness.repository.connections[("srv-1", ACCOUNT)]
    assert record.status == "connected"
    assert record.account_label == "dana-k"
    assert "access-1" not in record.access_token_ciphertext
    token_form = harness.upstream.calls("/token", "POST")[0].form()
    assert token_form["code_verifier"]

    with pytest.raises(MCPOAuthCallbackError) as reused:
        await harness.service.complete_authorization(
            state=state, code="good-code", error=None, account_id=ACCOUNT
        )
    assert reused.value.reason == "invalid_state"


async def test_callback_rejects_state_of_another_account() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    url = await harness.service.start_authorization(server, account_id=ACCOUNT)
    state = parse_qs(urlsplit(url).query)["state"][0]

    with pytest.raises(MCPOAuthCallbackError) as caught:
        await harness.service.complete_authorization(
            state=state, code="good-code", error=None, account_id="acct-mallory"
        )
    assert caught.value.reason == "wrong_account"
    assert caught.value.server_key is None
    assert harness.repository.connections == {}
    # The state is gone, so the real owner cannot use it after an attack either.
    assert harness.repository.states == {}
    assert harness.upstream.calls("/token") == []


async def test_callback_rejects_expired_state() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    url = await harness.service.start_authorization(server, account_id=ACCOUNT)
    state = parse_qs(urlsplit(url).query)["state"][0]
    harness.repository.expire_all_states()

    with pytest.raises(MCPOAuthCallbackError) as caught:
        await harness.service.complete_authorization(
            state=state, code="good-code", error=None, account_id=ACCOUNT
        )
    assert caught.value.reason == "expired_state"
    assert harness.upstream.calls("/token") == []


async def test_callback_with_provider_error_consumes_state() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    url = await harness.service.start_authorization(server, account_id=ACCOUNT)
    state = parse_qs(urlsplit(url).query)["state"][0]

    with pytest.raises(MCPOAuthCallbackError) as caught:
        await harness.service.complete_authorization(
            state=state, code=None, error="access_denied", account_id=ACCOUNT
        )
    assert caught.value.reason == "denied"
    assert harness.repository.states == {}


async def test_callback_rejects_bad_code() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    url = await harness.service.start_authorization(server, account_id=ACCOUNT)
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(MCPOAuthCallbackError) as caught:
        await harness.service.complete_authorization(
            state=state, code="stolen-code", error=None, account_id=ACCOUNT
        )
    assert caught.value.reason == "token_exchange_failed"


async def test_access_token_without_connection_requires_connect() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    with pytest.raises(MCPConnectionRequiredError) as caught:
        await harness.service.access_token_for(
            server_id=server.mcp_server_id,
            server_key=server.server_key,
            auth_config=server.auth_config,
            account_id=ACCOUNT,
        )
    assert caught.value.reason == "not_connected"


async def test_valid_token_needs_one_read_and_no_upstream_call() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    await harness.connect(server)
    harness.repository.calls.clear()
    before = len(harness.upstream.requests)

    token = await harness.service.access_token_for(
        server_id=server.mcp_server_id,
        server_key=server.server_key,
        auth_config=server.auth_config,
        account_id=ACCOUNT,
    )
    assert token.value == "access-1"
    assert harness.repository.calls == ["get_connection"]
    assert len(harness.upstream.requests) == before


def _expire(harness: _Harness) -> None:
    key = ("srv-1", ACCOUNT)
    harness.repository.connections[key] = replace(
        harness.repository.connections[key],
        expires_at=datetime.now(tz=UTC) - timedelta(seconds=5),
    )


async def test_expired_token_is_refreshed_once_for_concurrent_calls() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    await harness.connect(server)
    _expire(harness)

    async def call() -> str:
        token = await harness.service.access_token_for(
            server_id=server.mcp_server_id,
            server_key=server.server_key,
            auth_config=server.auth_config,
            account_id=ACCOUNT,
        )
        return token.value

    values = await asyncio.gather(*(call() for _ in range(5)))
    assert set(values) == {"access-2"}
    refreshes = [
        item
        for item in harness.upstream.calls("/token")
        if item.form()["grant_type"] == "refresh_token"
    ]
    assert len(refreshes) == 1
    record = harness.repository.connections[("srv-1", ACCOUNT)]
    assert record.refresh_lease_owner is None
    assert record.token_version == 2


async def test_refresh_waits_for_lease_held_by_another_replica() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    await harness.connect(server)
    _expire(harness)
    record = harness.repository.connections[("srv-1", ACCOUNT)]
    assert await harness.repository.claim_refresh_lease(
        connection_id=record.connection_id,
        token_version=record.token_version,
        owner="other",
        lease_seconds=30,
    )

    async def other_replica_finishes() -> None:
        await asyncio.sleep(0.05)
        await harness.repository.complete_refresh(
            connection_id=record.connection_id,
            owner="other",
            access_token_ciphertext=harness.cipher.encrypt(
                b"access-from-other", aad=b"deltallm:mcp-oauth:access:v1:srv-1:" + ACCOUNT.encode()
            ),
            refresh_token_ciphertext=None,
            scopes=None,
            expires_in_seconds=3600,
        )

    finisher = asyncio.create_task(other_replica_finishes())
    token = await harness.service.access_token_for(
        server_id=server.mcp_server_id,
        server_key=server.server_key,
        auth_config=server.auth_config,
        account_id=ACCOUNT,
    )
    await finisher
    assert token.value == "access-from-other"
    assert [
        item
        for item in harness.upstream.calls("/token")
        if item.form()["grant_type"] == "refresh_token"
    ] == []


async def test_invalid_grant_marks_connection_needs_reconnect() -> None:
    upstream = FakeOAuthUpstream()
    harness = _Harness(upstream)
    server = await harness.configured_server()
    await harness.connect(server)
    _expire(harness)
    upstream.refresh_behavior = "invalid_grant"

    with pytest.raises(MCPConnectionRequiredError) as caught:
        await harness.service.access_token_for(
            server_id=server.mcp_server_id,
            server_key=server.server_key,
            auth_config=server.auth_config,
            account_id=ACCOUNT,
        )
    assert caught.value.reason == "needs_reconnect"
    record = harness.repository.connections[("srv-1", ACCOUNT)]
    assert record.status == "needs_reconnect"
    assert record.status_reason == "The server revoked the token."


async def test_transient_refresh_failure_keeps_connection() -> None:
    upstream = FakeOAuthUpstream()
    harness = _Harness(upstream)
    server = await harness.configured_server()
    await harness.connect(server)
    _expire(harness)
    upstream.refresh_behavior = "server_error"

    with pytest.raises(Exception) as caught:
        await harness.service.access_token_for(
            server_id=server.mcp_server_id,
            server_key=server.server_key,
            auth_config=server.auth_config,
            account_id=ACCOUNT,
        )
    assert not isinstance(caught.value, MCPConnectionRequiredError)
    record = harness.repository.connections[("srv-1", ACCOUNT)]
    assert record.status == "connected"
    assert record.refresh_lease_owner is None


async def test_rejected_token_forces_refresh() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    await harness.connect(server)
    token = await harness.service.access_token_for(
        server_id=server.mcp_server_id,
        server_key=server.server_key,
        auth_config=server.auth_config,
        account_id=ACCOUNT,
    )
    refreshed = await harness.service.access_token_for(
        server_id=server.mcp_server_id,
        server_key=server.server_key,
        auth_config=server.auth_config,
        account_id=ACCOUNT,
        rejected_token_version=token.token_version,
    )
    assert refreshed.value == "access-2"


async def test_changed_encryption_key_requires_reconnect() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    await harness.connect(server)
    harness.service.cipher = SecretEnvelopeCipher(os.urandom(32))
    with pytest.raises(MCPConnectionRequiredError) as caught:
        await harness.service.access_token_for(
            server_id=server.mcp_server_id,
            server_key=server.server_key,
            auth_config=server.auth_config,
            account_id=ACCOUNT,
        )
    assert caught.value.reason == "needs_reconnect"


async def test_tokens_are_bound_to_their_account() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    await harness.connect(server)
    # Copy dana's row to another account: the AAD binding makes it unreadable.
    stolen = replace(harness.repository.connections[("srv-1", ACCOUNT)], account_id="acct-mallory")
    harness.repository.connections[("srv-1", "acct-mallory")] = stolen
    with pytest.raises(MCPConnectionRequiredError):
        await harness.service.access_token_for(
            server_id=server.mcp_server_id,
            server_key=server.server_key,
            auth_config=server.auth_config,
            account_id="acct-mallory",
        )


async def test_disconnect_revokes_refresh_token_and_deletes() -> None:
    harness = _Harness()
    server = await harness.configured_server()
    await harness.connect(server)

    deleted, revoked = await harness.service.disconnect(server, account_id=ACCOUNT)
    assert (deleted, revoked) == (True, True)
    form = harness.upstream.calls("/revoke", "POST")[0].form()
    assert form["token"] == "refresh-1"
    assert form["token_type_hint"] == "refresh_token"
    assert harness.repository.connections == {}
    assert await harness.service.disconnect(server, account_id=ACCOUNT) == (False, False)


async def test_disconnect_without_revocation_endpoint_still_deletes() -> None:
    harness = _Harness(FakeOAuthUpstream(revocation_enabled=False))
    server = await harness.configured_server()
    await harness.connect(server)
    assert await harness.service.disconnect(server, account_id=ACCOUNT) == (True, False)
    assert harness.repository.connections == {}
