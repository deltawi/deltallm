"""Fake OAuth upstream and in-memory repository for MCP OAuth tests. No real network."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx

from src.db.mcp.mcp import MCPServerRecord
from src.db.mcp.mcp_oauth import (
    MCPOAuthConnectionRecord,
    MCPOAuthRepository,
    MCPOAuthStateRecord,
)
from src.mcp.oauth.client import MCPOAuthProtocolClient
from src.mcp.oauth.http import MCPOAuthHTTP
from src.mcp.oauth.service import MCPOAuthService
from src.outbound.network_policy import OutboundNetworkPolicy
from src.secret_envelope import SecretEnvelopeCipher

PUBLIC_ADDRESS = "93.184.216.34"
PRIVATE_ADDRESS = "10.0.0.7"


@dataclass
class RecordedRequest:
    method: str
    host: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes

    def form(self) -> dict[str, str]:
        return {key: values[0] for key, values in parse_qs(self.body.decode()).items()}

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode())


def id_token(claims: dict[str, Any]) -> str:
    def segment(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment(claims)}.sig"


@dataclass
class FakeOAuthUpstream:
    """MCP server at mcp.example.com/mcp, authorization server at auth.example.com."""

    mcp_host: str = "mcp.example.com"
    auth_host: str = "auth.example.com"
    challenge_header: bool = True
    serve_resource_metadata: bool = True
    resource_value: str | None = None
    serve_oauth_metadata: bool = True
    serve_openid_metadata: bool = False
    issuer_override: str | None = None
    code_challenge_methods: list[str] = field(default_factory=lambda: ["S256"])
    registration_enabled: bool = True
    revocation_enabled: bool = True
    refresh_behavior: str = "ok"  # ok | invalid_grant | server_error
    token_lifetime: int | None = 3600
    issue_refresh_token: bool = True
    requests: list[RecordedRequest] = field(default_factory=list)
    token_counter: int = 0
    registered_clients: int = 0
    mcp_tool_responses: list[httpx.Response] = field(default_factory=list)

    @property
    def issuer(self) -> str:
        return self.issuer_override or f"https://{self.auth_host}"

    def calls(self, path: str, method: str | None = None) -> list[RecordedRequest]:
        return [
            item
            for item in self.requests
            if item.path == path and (method is None or item.method == method)
        ]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", "")
        body = request.read()
        recorded = RecordedRequest(
            method=request.method,
            host=host,
            path=request.url.path,
            query=request.url.query.decode()
            if isinstance(request.url.query, bytes)
            else str(request.url.query),
            headers={key.lower(): value for key, value in request.headers.items()},
            body=body,
        )
        self.requests.append(recorded)
        if host == self.mcp_host:
            response = self._mcp(recorded)
            if response.status_code != 404 or host != self.auth_host:
                return response
        if host == self.auth_host:
            return self._auth(recorded)
        return httpx.Response(404)

    def _mcp(self, request: RecordedRequest) -> httpx.Response:
        if request.method == "POST" and request.path == "/mcp":
            headers = {}
            if self.challenge_header:
                headers["WWW-Authenticate"] = (
                    'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"'
                )
            return httpx.Response(401, headers=headers)
        if (
            request.path
            in {
                "/.well-known/oauth-protected-resource/mcp",
                "/.well-known/oauth-protected-resource",
            }
            and self.serve_resource_metadata
        ):
            return httpx.Response(
                200,
                json={
                    "resource": self.resource_value or "https://mcp.example.com/mcp",
                    "authorization_servers": [self.issuer],
                    "scopes_supported": ["repo", "read:user"],
                },
            )
        return httpx.Response(404)

    def _metadata(self) -> dict[str, Any]:
        base = f"https://{self.auth_host}"
        metadata: dict[str, Any] = {
            "issuer": self.issuer,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "code_challenge_methods_supported": self.code_challenge_methods,
        }
        if self.registration_enabled:
            metadata["registration_endpoint"] = f"{base}/register"
        if self.revocation_enabled:
            metadata["revocation_endpoint"] = f"{base}/revoke"
        return metadata

    def _auth(self, request: RecordedRequest) -> httpx.Response:
        if request.path == "/.well-known/oauth-authorization-server" and self.serve_oauth_metadata:
            return httpx.Response(200, json=self._metadata())
        if request.path == "/.well-known/openid-configuration" and self.serve_openid_metadata:
            return httpx.Response(200, json=self._metadata())
        if request.path == "/register" and request.method == "POST":
            self.registered_clients += 1
            return httpx.Response(201, json={"client_id": f"dyn-client-{self.registered_clients}"})
        if request.path == "/token" and request.method == "POST":
            return self._token(request.form())
        if request.path == "/revoke" and request.method == "POST":
            return httpx.Response(200)
        return httpx.Response(404)

    def _token(self, form: dict[str, str]) -> httpx.Response:
        grant = form.get("grant_type")
        if grant == "refresh_token" and self.refresh_behavior == "invalid_grant":
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "secret detail"}
            )
        if grant == "refresh_token" and self.refresh_behavior == "server_error":
            return httpx.Response(503, json={"error": "temporarily_unavailable"})
        if grant == "authorization_code" and form.get("code") != "good-code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.token_counter += 1
        payload: dict[str, Any] = {
            "access_token": f"access-{self.token_counter}",
            "token_type": "Bearer",
            "scope": "repo read:user",
        }
        if self.token_lifetime is not None:
            payload["expires_in"] = self.token_lifetime
        if self.issue_refresh_token:
            payload["refresh_token"] = f"refresh-{self.token_counter}"
        if grant == "authorization_code":
            payload["id_token"] = id_token({"preferred_username": "dana-k"})
        return httpx.Response(200, json=payload)


async def _public_resolver(hostname: str, port: int) -> tuple[str, ...]:
    return (PUBLIC_ADDRESS,)


def make_http(
    upstream: FakeOAuthUpstream,
    *,
    resolver: Any = None,
    allow_http: bool = False,
    max_response_bytes: int = 256 * 1024,
) -> MCPOAuthHTTP:
    return MCPOAuthHTTP(
        transport=upstream.transport(),
        policy=OutboundNetworkPolicy(
            allow_http=allow_http,
            allowed_ports=(443, 80) if allow_http else (443,),
            resolver=resolver or _public_resolver,
        ),
        max_response_bytes=max_response_bytes,
    )


class InMemoryOAuthRepository(MCPOAuthRepository):
    """Same contract as the SQL repository, in memory. Counts every call."""

    def __init__(self) -> None:
        super().__init__(prisma_client=None)
        self.connections: dict[tuple[str, str], MCPOAuthConnectionRecord] = {}
        self.states: dict[str, MCPOAuthStateRecord] = {}
        self.server_configs: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.lease_expires: dict[str, datetime] = {}

    async def get_connection(self, *, server_id: str, account_id: str):  # noqa: ANN201
        self.calls.append("get_connection")
        return self.connections.get((server_id, account_id))

    async def list_connections_for_account(self, account_id: str):  # noqa: ANN201
        self.calls.append("list_connections_for_account")
        return [item for (_, account), item in self.connections.items() if account == account_id]

    async def upsert_connection(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append("upsert_connection")
        key = (kwargs["server_id"], kwargs["account_id"])
        previous = self.connections.get(key)
        expires_in = kwargs["expires_in_seconds"]
        record = MCPOAuthConnectionRecord(
            connection_id=previous.connection_id if previous else str(uuid4()),
            server_id=kwargs["server_id"],
            account_id=kwargs["account_id"],
            status="connected",
            client_id=kwargs["client_id"],
            access_token_ciphertext=kwargs["access_token_ciphertext"],
            refresh_token_ciphertext=kwargs["refresh_token_ciphertext"],
            token_type=kwargs["token_type"],
            scopes=tuple(kwargs["scopes"]),
            account_label=kwargs["account_label"],
            expires_at=(
                datetime.now(tz=UTC) + timedelta(seconds=expires_in)
                if expires_in is not None
                else None
            ),
            token_version=(previous.token_version + 1) if previous else 1,
            connected_at=datetime.now(tz=UTC),
        )
        self.connections[key] = record
        return record

    def _by_id(self, connection_id: str) -> tuple[tuple[str, str], MCPOAuthConnectionRecord] | None:
        for key, item in self.connections.items():
            if item.connection_id == connection_id:
                return key, item
        return None

    async def claim_refresh_lease(self, *, connection_id, token_version, owner, lease_seconds):  # noqa: ANN001, ANN201
        self.calls.append("claim_refresh_lease")
        found = self._by_id(connection_id)
        if found is None:
            return False
        key, item = found
        now = datetime.now(tz=UTC)
        lease_until = self.lease_expires.get(connection_id)
        if item.token_version != token_version or item.status != "connected":
            return False
        if item.refresh_lease_owner and lease_until and lease_until > now:
            return False
        self.connections[key] = replace(item, refresh_lease_owner=owner)
        self.lease_expires[connection_id] = now + timedelta(seconds=lease_seconds)
        return True

    async def complete_refresh(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append("complete_refresh")
        found = self._by_id(kwargs["connection_id"])
        if found is None:
            return None
        key, item = found
        if item.refresh_lease_owner != kwargs["owner"]:
            return None
        expires_in = kwargs["expires_in_seconds"]
        record = replace(
            item,
            access_token_ciphertext=kwargs["access_token_ciphertext"],
            refresh_token_ciphertext=kwargs["refresh_token_ciphertext"]
            or item.refresh_token_ciphertext,
            scopes=tuple(kwargs["scopes"]) if kwargs["scopes"] is not None else item.scopes,
            expires_at=(
                datetime.now(tz=UTC) + timedelta(seconds=expires_in)
                if expires_in is not None
                else None
            ),
            token_version=item.token_version + 1,
            refresh_lease_owner=None,
            last_refreshed_at=datetime.now(tz=UTC),
        )
        self.connections[key] = record
        self.lease_expires.pop(item.connection_id, None)
        return record

    async def release_refresh_lease(self, *, connection_id: str, owner: str) -> None:
        self.calls.append("release_refresh_lease")
        found = self._by_id(connection_id)
        if found is not None and found[1].refresh_lease_owner == owner:
            self.connections[found[0]] = replace(found[1], refresh_lease_owner=None)
            self.lease_expires.pop(connection_id, None)

    async def mark_needs_reconnect(self, *, connection_id, token_version, reason):  # noqa: ANN001, ANN201
        self.calls.append("mark_needs_reconnect")
        found = self._by_id(connection_id)
        if found is None or found[1].token_version != token_version:
            return False
        self.connections[found[0]] = replace(
            found[1], status="needs_reconnect", status_reason=reason, refresh_lease_owner=None
        )
        return True

    async def delete_connection(self, *, server_id: str, account_id: str):  # noqa: ANN201
        self.calls.append("delete_connection")
        return self.connections.pop((server_id, account_id), None)

    async def delete_connections_for_server(self, server_id: str) -> int:
        self.calls.append("delete_connections_for_server")
        keys = [key for key in self.connections if key[0] == server_id]
        for key in keys:
            del self.connections[key]
        self.states = {k: v for k, v in self.states.items() if v.server_id != server_id}
        return len(keys)

    async def store_server_oauth_config(
        self, *, server_id: str, auth_config: dict[str, Any]
    ) -> bool:
        self.calls.append("store_server_oauth_config")
        self.server_configs[server_id] = auth_config
        return True

    async def create_state(self, **kwargs) -> None:  # noqa: ANN003
        self.calls.append("create_state")
        self.states[kwargs["state_hash"]] = MCPOAuthStateRecord(
            state_hash=kwargs["state_hash"],
            server_id=kwargs["server_id"],
            account_id=kwargs["account_id"],
            client_id=kwargs["client_id"],
            redirect_uri=kwargs["redirect_uri"],
            code_verifier_ciphertext=kwargs["code_verifier_ciphertext"],
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=kwargs["ttl_seconds"]),
            is_live=True,
        )

    async def consume_state(self, state_hash: str):  # noqa: ANN201
        self.calls.append("consume_state")
        record = self.states.pop(state_hash, None)
        if record is None:
            return None
        live = record.expires_at is not None and record.expires_at > datetime.now(tz=UTC)
        return replace(record, is_live=live)

    def expire_all_states(self) -> None:
        for key, record in list(self.states.items()):
            self.states[key] = replace(
                record, expires_at=datetime.now(tz=UTC) - timedelta(seconds=1)
            )


BASE_URL = "https://gw.example.com"
ACCOUNT = "acct-dana"


def oauth_server(auth_config: dict | None = None, **kwargs) -> MCPServerRecord:  # noqa: ANN003
    return MCPServerRecord(
        mcp_server_id="srv-1",
        server_key="github",
        name="GitHub",
        base_url="https://mcp.example.com/mcp",
        auth_mode="oauth2",
        auth_config=auth_config or {},
        **kwargs,
    )


class OAuthHarness:
    def __init__(
        self,
        upstream: FakeOAuthUpstream | None = None,
        *,
        key: bytes | None = None,
        base_url: str | None = BASE_URL,
    ) -> None:
        self.upstream = upstream or FakeOAuthUpstream()
        self.repository = InMemoryOAuthRepository()
        self.servers: dict[str, MCPServerRecord] = {}
        self.base_url = base_url
        self.cipher = SecretEnvelopeCipher(key or os.urandom(32)) if key != b"" else None

        async def loader(server_id: str) -> MCPServerRecord | None:
            return self.servers.get(server_id)

        self.service = MCPOAuthService(
            repository=self.repository,
            protocol=MCPOAuthProtocolClient(make_http(self.upstream)),
            cipher=self.cipher,
            public_base_url=lambda: self.base_url,
            server_loader=loader,
        )

    async def configured_server(self, requested: dict | None = None) -> MCPServerRecord:
        auth_config = await self.service.prepare_server_auth_config(
            base_url="https://mcp.example.com/mcp", requested=requested, existing=None
        )
        server = oauth_server(auth_config)
        self.servers[server.mcp_server_id] = server
        return server

    async def connect(self, server: MCPServerRecord, account_id: str = ACCOUNT) -> None:
        url = await self.service.start_authorization(server, account_id=account_id)
        state = parse_qs(urlsplit(url).query)["state"][0]
        await self.service.complete_authorization(
            state=state, code="good-code", error=None, account_id=account_id
        )
