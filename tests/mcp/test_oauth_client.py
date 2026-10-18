from __future__ import annotations

import pytest

from src.mcp.oauth.client import MCPOAuthProtocolClient
from src.mcp.oauth.errors import MCPOAuthDiscoveryError, MCPOAuthError, MCPOAuthTokenError
from src.mcp.oauth.models import OAuthClient
from tests.mcp.oauth_fakes import PRIVATE_ADDRESS, FakeOAuthUpstream, make_http


def _client(upstream: FakeOAuthUpstream, **kwargs) -> MCPOAuthProtocolClient:  # noqa: ANN003
    return MCPOAuthProtocolClient(make_http(upstream, **kwargs))


async def test_discovery_follows_www_authenticate_resource_metadata() -> None:
    upstream = FakeOAuthUpstream()
    result = await _client(upstream).discover("https://mcp.example.com/mcp")

    assert result.found_from == "WWW-Authenticate header"
    assert result.metadata_url == "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    assert result.authorization_server == "https://auth.example.com"
    assert result.authorization_endpoint == "https://auth.example.com/authorize"
    assert result.token_endpoint == "https://auth.example.com/token"
    assert result.resource == "https://mcp.example.com/mcp"
    assert result.pkce_s256_supported is True
    assert result.dynamic_registration_supported is True
    assert result.scopes_supported == ("repo", "read:user")
    # The probe sends no credentials.
    probe = upstream.calls("/mcp", "POST")[0]
    assert "authorization" not in probe.headers


async def test_discovery_uses_well_known_path_without_challenge_header() -> None:
    upstream = FakeOAuthUpstream(challenge_header=False)
    result = await _client(upstream).discover("https://mcp.example.com/mcp")

    assert result.found_from == "/.well-known/oauth-protected-resource/mcp"
    assert result.authorization_server == "https://auth.example.com"


async def test_discovery_falls_back_to_openid_configuration() -> None:
    upstream = FakeOAuthUpstream(serve_oauth_metadata=False, serve_openid_metadata=True)
    result = await _client(upstream).discover("https://mcp.example.com/mcp")

    assert result.token_endpoint == "https://auth.example.com/token"
    assert upstream.calls("/.well-known/openid-configuration")


async def test_discovery_without_resource_metadata_uses_server_origin() -> None:
    upstream = FakeOAuthUpstream(
        challenge_header=False,
        serve_resource_metadata=False,
        auth_host="mcp.example.com",
    )
    result = await _client(upstream).discover("https://mcp.example.com/mcp")

    assert result.authorization_server == "https://mcp.example.com"
    assert result.found_from == "/.well-known/oauth-authorization-server"
    assert result.resource == "https://mcp.example.com/mcp"


async def test_discovery_rejects_metadata_for_another_resource() -> None:
    upstream = FakeOAuthUpstream(resource_value="https://other.example.com/mcp")
    with pytest.raises(MCPOAuthDiscoveryError):
        # Without valid resource metadata the origin is tried as the
        # authorization server; it serves no metadata, so discovery fails.
        await _client(upstream).discover("https://mcp.example.com/mcp")


async def test_discovery_rejects_issuer_mismatch() -> None:
    upstream = FakeOAuthUpstream(issuer_override="https://auth.example.com")
    upstream_metadata = upstream._metadata  # noqa: SLF001

    def wrong_issuer():  # noqa: ANN202
        payload = upstream_metadata()
        payload["issuer"] = "https://evil.example.com"
        return payload

    upstream._metadata = wrong_issuer  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(MCPOAuthDiscoveryError):
        await _client(upstream).discover("https://mcp.example.com/mcp")


async def test_discovery_blocks_private_and_metadata_addresses() -> None:
    upstream = FakeOAuthUpstream()

    async def private_resolver(hostname: str, port: int) -> tuple[str, ...]:
        return (PRIVATE_ADDRESS,)

    with pytest.raises(MCPOAuthDiscoveryError):
        await _client(upstream, resolver=private_resolver).discover("https://mcp.example.com/mcp")
    # No request reached the upstream: the policy refused each address first.
    assert upstream.requests == []


async def test_discovery_rejects_http_urls_unless_allowed() -> None:
    upstream = FakeOAuthUpstream()
    with pytest.raises(MCPOAuthDiscoveryError):
        await _client(upstream).discover("http://mcp.example.com/mcp")


async def test_response_size_is_bounded() -> None:
    upstream = FakeOAuthUpstream()
    http = make_http(upstream, max_response_bytes=16)
    with pytest.raises(MCPOAuthError, match="too large"):
        await http.get("https://auth.example.com/.well-known/oauth-authorization-server")


async def test_dynamic_client_registration_sends_redirect_and_public_client() -> None:
    upstream = FakeOAuthUpstream()
    protocol = _client(upstream)
    discovery = await protocol.discover("https://mcp.example.com/mcp")
    client = await protocol.register_client(
        discovery, redirect_uri="https://gw.example.com/mcp/oauth/callback", scopes=["repo"]
    )

    assert client.client_id == "dyn-client-1"
    request = upstream.calls("/register", "POST")[0].json()
    assert request["redirect_uris"] == ["https://gw.example.com/mcp/oauth/callback"]
    assert request["token_endpoint_auth_method"] == "none"
    assert request["grant_types"] == ["authorization_code", "refresh_token"]
    assert request["scope"] == "repo"


async def test_registration_requires_endpoint() -> None:
    upstream = FakeOAuthUpstream(registration_enabled=False)
    protocol = _client(upstream)
    discovery = await protocol.discover("https://mcp.example.com/mcp")
    with pytest.raises(MCPOAuthDiscoveryError, match="client ID"):
        await protocol.register_client(discovery, redirect_uri="https://gw/cb", scopes=[])


async def test_code_exchange_sends_pkce_verifier_and_resource() -> None:
    upstream = FakeOAuthUpstream()
    protocol = _client(upstream)
    discovery = await protocol.discover("https://mcp.example.com/mcp")
    tokens = await protocol.exchange_code(
        discovery,
        OAuthClient(client_id="cid"),
        code="good-code",
        code_verifier="verifier-123",
        redirect_uri="https://gw/cb",
    )

    form = upstream.calls("/token", "POST")[0].form()
    assert form["code_verifier"] == "verifier-123"
    assert form["resource"] == "https://mcp.example.com/mcp"
    assert form["client_id"] == "cid"
    assert tokens.access_token == "access-1"
    assert tokens.refresh_token == "refresh-1"
    assert tokens.account_label == "dana-k"
    assert "access-1" not in repr(tokens)


async def test_confidential_client_uses_basic_auth() -> None:
    upstream = FakeOAuthUpstream()
    protocol = _client(upstream)
    discovery = await protocol.discover("https://mcp.example.com/mcp")
    await protocol.exchange_code(
        discovery,
        OAuthClient(client_id="cid", client_secret="s3cret"),
        code="good-code",
        code_verifier="v",
        redirect_uri="https://gw/cb",
    )
    request = upstream.calls("/token", "POST")[0]
    assert request.headers["authorization"].startswith("Basic ")
    assert "client_secret" not in request.form()


async def test_refresh_invalid_grant_has_error_code_without_upstream_text() -> None:
    upstream = FakeOAuthUpstream(refresh_behavior="invalid_grant")
    protocol = _client(upstream)
    discovery = await protocol.discover("https://mcp.example.com/mcp")
    with pytest.raises(MCPOAuthTokenError) as caught:
        await protocol.refresh(discovery, OAuthClient(client_id="cid"), refresh_token="r")
    assert caught.value.error_code == "invalid_grant"
    assert "secret detail" not in str(caught.value)


async def test_revoke_posts_token_with_hint() -> None:
    upstream = FakeOAuthUpstream()
    protocol = _client(upstream)
    discovery = await protocol.discover("https://mcp.example.com/mcp")
    assert await protocol.revoke(
        discovery, OAuthClient(client_id="cid"), token="refresh-9", token_type_hint="refresh_token"
    )
    form = upstream.calls("/revoke", "POST")[0].form()
    assert form == {"token": "refresh-9", "token_type_hint": "refresh_token", "client_id": "cid"}
