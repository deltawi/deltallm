"""Per-user OAuth connections for MCP servers with ``auth_mode == "oauth2"``.

Source of truth: PostgreSQL (``deltallm_mcpoauthconnection`` and
``deltallm_mcpoauthstate``). Tokens and code verifiers are stored as AES-GCM
envelopes bound to their server and account. The data plane reads a connection
only when a request calls a tool of an ``oauth2`` server.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from src.db.mcp.mcp import MCPServerRecord
from src.db.mcp.mcp_oauth import MCPOAuthConnectionRecord, MCPOAuthRepository
from src.secret_envelope import SecretEnvelopeCipher

from .client import MCPOAuthProtocolClient
from .errors import (
    MCPConnectionRequiredError,
    MCPOAuthCallbackError,
    MCPOAuthConfigurationError,
    MCPOAuthError,
    MCPOAuthTokenError,
)
from .models import (
    OAuth2ServerSettings,
    OAuthAccessToken,
    OAuthClient,
    OAuthDiscoveryResult,
    OAuthTokenResponse,
    normalize_scopes,
)

logger = logging.getLogger(__name__)

OAUTH2_AUTH_MODE = "oauth2"
CALLBACK_PATH = "/mcp/oauth/callback"
CONNECT_PATH_PREFIX = "/ui/connect/"
STATE_TTL_SECONDS = 600
REFRESH_SKEW_SECONDS = 60
REFRESH_LEASE_SECONDS = 30
_REFRESH_WAIT_DELAYS_SECONDS = (0.2, 0.4, 0.8, 1.6)
_LOCK_STRIPES = 64
_NEEDS_RECONNECT_REVOKED = "The server revoked the token."
_NEEDS_RECONNECT_EXPIRED = "The token expired and the server gave no refresh token."
_NEEDS_RECONNECT_UNREADABLE = "The stored token cannot be read. The encryption key changed."
_NEEDS_RECONNECT_REJECTED = "The server rejected the token."

ServerLoader = Callable[[str], Awaitable[MCPServerRecord | None]]
BaseUrlProvider = Callable[[], str | None]


def _aad(kind: str, *parts: str) -> bytes:
    return ":".join(("deltallm", "mcp-oauth", kind, "v1", *parts)).encode("utf-8")


_CLIENT_SECRET_AAD = _aad("client-secret")


def normalize_public_base_url(value: str | None) -> str | None:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return None
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment:
        return None
    return text


class MCPOAuthService:
    def __init__(
        self,
        *,
        repository: MCPOAuthRepository,
        protocol: MCPOAuthProtocolClient,
        cipher: SecretEnvelopeCipher | None,
        public_base_url: BaseUrlProvider,
        server_loader: ServerLoader,
    ) -> None:
        self.repository = repository
        self.protocol = protocol
        self.cipher = cipher
        self._public_base_url = public_base_url
        self._server_loader = server_loader
        self._locks = tuple(asyncio.Lock() for _ in range(_LOCK_STRIPES))

    # Settings ----------------------------------------------------------------

    @property
    def encryption_configured(self) -> bool:
        return self.cipher is not None

    def public_base_url(self) -> str | None:
        return normalize_public_base_url(self._public_base_url())

    def redirect_uri(self) -> str | None:
        base = self.public_base_url()
        return f"{base}{CALLBACK_PATH}" if base else None

    def connect_url(self, server_key: str) -> str | None:
        base = self.public_base_url()
        return f"{base}{CONNECT_PATH_PREFIX}{server_key}" if base else None

    def _require_cipher(self) -> SecretEnvelopeCipher:
        if self.cipher is None:
            raise MCPOAuthConfigurationError(
                "Set general_settings.mcp_oauth_encryption_key to use OAuth 2.0 (per user) "
                "MCP servers"
            )
        return self.cipher

    def _require_redirect_uri(self) -> str:
        redirect_uri = self.redirect_uri()
        if redirect_uri is None:
            raise MCPOAuthConfigurationError(
                "Set general_settings.email_base_url to the public gateway URL to use "
                "OAuth 2.0 (per user) MCP servers"
            )
        return redirect_uri

    # Admin: server configuration ----------------------------------------------

    async def prepare_server_auth_config(
        self,
        *,
        base_url: str,
        requested: Any,
        existing: dict[str, Any] | None,
        existing_base_url: str | None = None,
    ) -> dict[str, Any]:
        """Build the stored ``auth_config`` for an oauth2 server.

        Missing fields keep their stored value. ``client_secret`` is write-only;
        ``clear_client_secret: true`` removes it. Discovery runs again when the
        base URL changes or no result is stored. Discovery or registration
        failures do not block the save; the result shows the error.
        """
        cipher = self._require_cipher()
        self._require_redirect_uri()
        if requested is not None and not isinstance(requested, dict):
            raise ValueError("auth_config must be an object")
        request = dict(requested or {})
        current = OAuth2ServerSettings.from_auth_config(existing)

        client_id = current.client_id
        client_id_source = current.client_id_source
        if "client_id" in request:
            requested_id = str(request.get("client_id") or "").strip()
            if len(requested_id) > 512:
                raise ValueError("auth_config.client_id is too long")
            if requested_id:
                client_id, client_id_source = requested_id, "manual"
            elif client_id_source != "dynamic":
                client_id, client_id_source = None, None

        secret_ciphertext = current.client_secret_ciphertext
        if bool(request.get("clear_client_secret")):
            secret_ciphertext = None
        new_secret = request.get("client_secret")
        if isinstance(new_secret, str) and new_secret.strip():
            if len(new_secret) > 4096:
                raise ValueError("auth_config.client_secret is too long")
            secret_ciphertext = cipher.encrypt(
                new_secret.strip().encode("utf-8"), aad=_CLIENT_SECRET_AAD
            )
        elif new_secret is not None and not isinstance(new_secret, str):
            raise ValueError("auth_config.client_secret must be a string")

        scopes = (
            tuple(normalize_scopes(request.get("scopes")))
            if "scopes" in request
            else current.scopes
        )
        settings = OAuth2ServerSettings(
            client_id=client_id,
            client_id_source=client_id_source,
            client_secret_ciphertext=secret_ciphertext,
            scopes=scopes,
            discovery=current.discovery,
            discovery_error=current.discovery_error,
        )
        base_changed = existing_base_url is not None and existing_base_url.rstrip(
            "/"
        ) != base_url.rstrip("/")
        if base_changed and settings.client_id_source == "dynamic":
            # A client registered for another server is not valid here.
            settings = replace(
                settings, client_id=None, client_id_source=None, client_secret_ciphertext=None
            )
        if settings.discovery is None or base_changed or settings.client_id is None:
            settings = await self.run_discovery(base_url=base_url, settings=settings)
        return settings.to_auth_config()

    async def run_discovery(
        self, *, base_url: str, settings: OAuth2ServerSettings
    ) -> OAuth2ServerSettings:
        """Discover metadata, and register a client when none is set."""
        cipher = self._require_cipher()
        redirect_uri = self._require_redirect_uri()
        try:
            discovery = await self.protocol.discover(base_url)
        except MCPOAuthError as exc:
            return replace(settings, discovery=None, discovery_error=str(exc))
        settings = replace(settings, discovery=discovery, discovery_error=None)
        if settings.client_id is None:
            try:
                client = await self.protocol.register_client(
                    discovery, redirect_uri=redirect_uri, scopes=list(settings.scopes)
                )
            except MCPOAuthError as exc:
                return replace(settings, discovery_error=str(exc))
            settings = replace(
                settings,
                client_id=client.client_id,
                client_id_source="dynamic",
                client_secret_ciphertext=(
                    cipher.encrypt(client.client_secret.encode("utf-8"), aad=_CLIENT_SECRET_AAD)
                    if client.client_secret
                    else None
                ),
            )
        return settings

    async def preview_discovery(self, base_url: str) -> OAuthDiscoveryResult:
        return await self.protocol.discover(base_url)

    def _client(
        self, settings: OAuth2ServerSettings, *, client_id: str | None = None
    ) -> OAuthClient:
        resolved_id = client_id or settings.client_id
        if not resolved_id:
            raise MCPOAuthConfigurationError(
                "This MCP server has no OAuth client. Ask an administrator to run discovery again "
                "or to enter a client ID."
            )
        secret: str | None = None
        if settings.client_secret_ciphertext:
            plaintext = self._require_cipher().try_decrypt(
                settings.client_secret_ciphertext, aad=_CLIENT_SECRET_AAD
            )
            if plaintext is None:
                raise MCPOAuthConfigurationError(
                    "The stored client secret cannot be read. Enter the client secret again."
                )
            secret = plaintext.decode("utf-8")
        return OAuthClient(client_id=resolved_id, client_secret=secret)

    # User: connect flow -------------------------------------------------------

    async def start_authorization(self, server: MCPServerRecord, *, account_id: str) -> str:
        if server.auth_mode != OAUTH2_AUTH_MODE or not server.enabled:
            raise MCPOAuthConfigurationError("This MCP server does not use per-user OAuth")
        cipher = self._require_cipher()
        redirect_uri = self._require_redirect_uri()
        settings = OAuth2ServerSettings.from_auth_config(server.auth_config)
        discovery = settings.discovery
        if discovery is None:
            raise MCPOAuthConfigurationError(
                "OAuth discovery has not succeeded for this server. Ask an administrator to run it again."
            )
        if not discovery.pkce_s256_supported:
            raise MCPOAuthConfigurationError(
                "The authorization server does not declare PKCE (S256) support, so the gateway "
                "cannot connect to it"
            )
        client = self._client(settings)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        state = secrets.token_urlsafe(32)
        state_hash = _state_hash(state)
        await self.repository.create_state(
            state_hash=state_hash,
            server_id=server.mcp_server_id,
            account_id=account_id,
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            code_verifier_ciphertext=cipher.encrypt(
                verifier.encode("ascii"), aad=_aad("verifier", state_hash)
            ),
            ttl_seconds=STATE_TTL_SECONDS,
        )
        params = {
            "response_type": "code",
            "client_id": client.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": discovery.resource,
        }
        if settings.scopes:
            params["scope"] = " ".join(settings.scopes)
        return _with_query(discovery.authorization_endpoint, params)

    async def complete_authorization(
        self,
        *,
        state: str,
        code: str | None,
        error: str | None,
        account_id: str,
    ) -> MCPServerRecord:
        if not state or len(state) > 512:
            raise MCPOAuthCallbackError("invalid_state")
        record = await self.repository.consume_state(_state_hash(state))
        if record is None:
            raise MCPOAuthCallbackError("invalid_state")
        if record.account_id != account_id:
            # The state belongs to another user: do not reveal the server.
            raise MCPOAuthCallbackError("wrong_account")
        server = await self._server_loader(record.server_id)
        server_key = server.server_key if server is not None else None
        if not record.is_live:
            raise MCPOAuthCallbackError("expired_state", server_key=server_key)
        if server is None or server.auth_mode != OAUTH2_AUTH_MODE or not server.enabled:
            raise MCPOAuthCallbackError("server_unavailable", server_key=server_key)
        if error or not code:
            raise MCPOAuthCallbackError("denied", server_key=server_key)
        settings = OAuth2ServerSettings.from_auth_config(server.auth_config)
        if settings.discovery is None or settings.client_id != record.client_id:
            raise MCPOAuthCallbackError("client_changed", server_key=server_key)
        try:
            cipher = self._require_cipher()
            client = self._client(settings, client_id=record.client_id)
        except MCPOAuthConfigurationError:
            raise MCPOAuthCallbackError("configuration", server_key=server_key) from None
        verifier = cipher.try_decrypt(
            record.code_verifier_ciphertext, aad=_aad("verifier", record.state_hash)
        )
        if verifier is None:
            raise MCPOAuthCallbackError("configuration", server_key=server_key)
        try:
            tokens = await self.protocol.exchange_code(
                settings.discovery,
                client,
                code=code,
                code_verifier=verifier.decode("ascii"),
                redirect_uri=record.redirect_uri,
            )
        except MCPOAuthError as exc:
            logger.warning(
                "mcp oauth code exchange failed",
                extra={"server_key": server.server_key, "error": str(exc)},
            )
            raise MCPOAuthCallbackError("token_exchange_failed", server_key=server_key) from None
        await self._store_new_connection(
            server=server,
            account_id=account_id,
            client_id=record.client_id,
            tokens=tokens,
            default_scopes=settings.scopes,
        )
        return server

    async def _store_new_connection(
        self,
        *,
        server: MCPServerRecord,
        account_id: str,
        client_id: str,
        tokens: OAuthTokenResponse,
        default_scopes: tuple[str, ...],
    ) -> MCPOAuthConnectionRecord | None:
        cipher = self._require_cipher()
        server_id = server.mcp_server_id
        return await self.repository.upsert_connection(
            server_id=server_id,
            account_id=account_id,
            client_id=client_id,
            access_token_ciphertext=cipher.encrypt(
                tokens.access_token.encode("utf-8"), aad=_aad("access", server_id, account_id)
            ),
            refresh_token_ciphertext=(
                cipher.encrypt(
                    tokens.refresh_token.encode("utf-8"),
                    aad=_aad("refresh", server_id, account_id),
                )
                if tokens.refresh_token
                else None
            ),
            token_type=tokens.token_type,
            scopes=list(tokens.scopes if tokens.scopes is not None else default_scopes),
            account_label=tokens.account_label,
            expires_in_seconds=tokens.expires_in,
        )

    async def disconnect(self, server: MCPServerRecord, *, account_id: str) -> tuple[bool, bool]:
        """Delete the connection. Returns (deleted, revoked at the server)."""
        connection = await self.repository.delete_connection(
            server_id=server.mcp_server_id, account_id=account_id
        )
        if connection is None:
            return False, False
        settings = OAuth2ServerSettings.from_auth_config(server.auth_config)
        if settings.discovery is None or not settings.discovery.revocation_endpoint:
            return True, False
        try:
            client = self._client(settings, client_id=connection.client_id)
        except MCPOAuthConfigurationError:
            return True, False
        token, hint = self._revocable_token(connection)
        if token is None:
            return True, False
        revoked = await self.protocol.revoke(
            settings.discovery, client, token=token, token_type_hint=hint
        )
        return True, revoked

    def _revocable_token(self, connection: MCPOAuthConnectionRecord) -> tuple[str | None, str]:
        if self.cipher is None:
            return None, ""
        if connection.refresh_token_ciphertext:
            refresh = self.cipher.try_decrypt(
                connection.refresh_token_ciphertext,
                aad=_aad("refresh", connection.server_id, connection.account_id),
            )
            if refresh is not None:
                # Revoking the refresh token also ends the grant (RFC 7009 section 2.1).
                return refresh.decode("utf-8"), "refresh_token"
        access = self.cipher.try_decrypt(
            connection.access_token_ciphertext,
            aad=_aad("access", connection.server_id, connection.account_id),
        )
        return (access.decode("utf-8"), "access_token") if access is not None else (None, "")

    async def list_connections(self, account_id: str) -> list[MCPOAuthConnectionRecord]:
        return await self.repository.list_connections_for_account(account_id)

    # Data plane: tokens for tool calls ------------------------------------------

    async def access_token_for(
        self,
        *,
        server_id: str,
        server_key: str,
        auth_config: dict[str, Any] | None,
        account_id: str,
        rejected_token_version: int | None = None,
    ) -> OAuthAccessToken:
        """Return a usable access token, refreshing it when it expires soon.

        ``rejected_token_version`` forces a refresh after the MCP server
        rejected that token version with HTTP 401.
        """
        connection = await self.repository.get_connection(
            server_id=server_id, account_id=account_id
        )
        self._require_usable(connection, server_key)
        assert connection is not None
        if rejected_token_version is None and not _expires_soon(connection):
            return self._decrypt_access(connection, server_key)
        if (
            rejected_token_version is not None
            and connection.token_version != rejected_token_version
        ):
            # Another call already replaced the rejected token.
            return self._decrypt_access(connection, server_key)
        return await self._refresh(
            connection,
            server_key=server_key,
            settings=OAuth2ServerSettings.from_auth_config(auth_config),
            forced=rejected_token_version is not None,
        )

    def _require_usable(self, connection: MCPOAuthConnectionRecord | None, server_key: str) -> None:
        if connection is None:
            raise MCPConnectionRequiredError(server_key=server_key, reason="not_connected")
        if connection.status != "connected":
            raise MCPConnectionRequiredError(server_key=server_key, reason="needs_reconnect")

    def _decrypt_access(
        self, connection: MCPOAuthConnectionRecord, server_key: str
    ) -> OAuthAccessToken:
        cipher = self._require_cipher()
        plaintext = cipher.try_decrypt(
            connection.access_token_ciphertext,
            aad=_aad("access", connection.server_id, connection.account_id),
        )
        if plaintext is None:
            raise MCPConnectionRequiredError(server_key=server_key, reason="needs_reconnect")
        return OAuthAccessToken(
            value=plaintext.decode("utf-8"), token_version=connection.token_version
        )

    async def _refresh(
        self,
        connection: MCPOAuthConnectionRecord,
        *,
        server_key: str,
        settings: OAuth2ServerSettings,
        forced: bool,
    ) -> OAuthAccessToken:
        cipher = self._require_cipher()
        lock = self._locks[hash(connection.connection_id) % _LOCK_STRIPES]
        async with lock:
            current = await self.repository.get_connection(
                server_id=connection.server_id, account_id=connection.account_id
            )
            self._require_usable(current, server_key)
            assert current is not None
            if current.token_version != connection.token_version and not _expires_soon(current):
                return self._decrypt_access(current, server_key)
            refresh_plaintext = (
                cipher.try_decrypt(
                    current.refresh_token_ciphertext,
                    aad=_aad("refresh", current.server_id, current.account_id),
                )
                if current.refresh_token_ciphertext
                else None
            )
            if refresh_plaintext is None:
                reason = (
                    _NEEDS_RECONNECT_UNREADABLE
                    if current.refresh_token_ciphertext
                    else (_NEEDS_RECONNECT_REJECTED if forced else _NEEDS_RECONNECT_EXPIRED)
                )
                await self.repository.mark_needs_reconnect(
                    connection_id=current.connection_id,
                    token_version=current.token_version,
                    reason=reason,
                )
                raise MCPConnectionRequiredError(server_key=server_key, reason="needs_reconnect")
            owner = str(uuid4())
            claimed = await self.repository.claim_refresh_lease(
                connection_id=current.connection_id,
                token_version=current.token_version,
                owner=owner,
                lease_seconds=REFRESH_LEASE_SECONDS,
            )
            if not claimed:
                return await self._wait_for_other_refresh(current, server_key)
            try:
                if settings.discovery is None:
                    raise MCPOAuthConfigurationError(
                        "OAuth discovery has no result for this server"
                    )
                tokens = await self.protocol.refresh(
                    settings.discovery,
                    self._client(settings, client_id=current.client_id),
                    refresh_token=refresh_plaintext.decode("utf-8"),
                )
            except MCPOAuthTokenError as exc:
                if exc.error_code in {"invalid_grant", "invalid_client", "unauthorized_client"}:
                    await self.repository.mark_needs_reconnect(
                        connection_id=current.connection_id,
                        token_version=current.token_version,
                        reason=_NEEDS_RECONNECT_REVOKED,
                    )
                    raise MCPConnectionRequiredError(
                        server_key=server_key, reason="needs_reconnect"
                    ) from None
                await self.repository.release_refresh_lease(
                    connection_id=current.connection_id, owner=owner
                )
                raise
            except BaseException:
                await self.repository.release_refresh_lease(
                    connection_id=current.connection_id, owner=owner
                )
                raise
            updated = await self.repository.complete_refresh(
                connection_id=current.connection_id,
                owner=owner,
                access_token_ciphertext=cipher.encrypt(
                    tokens.access_token.encode("utf-8"),
                    aad=_aad("access", current.server_id, current.account_id),
                ),
                refresh_token_ciphertext=(
                    cipher.encrypt(
                        tokens.refresh_token.encode("utf-8"),
                        aad=_aad("refresh", current.server_id, current.account_id),
                    )
                    if tokens.refresh_token
                    else None
                ),
                scopes=list(tokens.scopes) if tokens.scopes is not None else None,
                expires_in_seconds=tokens.expires_in,
            )
            if updated is None:
                raise MCPOAuthError("The OAuth token refresh lost its lease. Try again.")
            return OAuthAccessToken(value=tokens.access_token, token_version=updated.token_version)

    async def _wait_for_other_refresh(
        self, connection: MCPOAuthConnectionRecord, server_key: str
    ) -> OAuthAccessToken:
        """Another replica holds the refresh lease: wait a short time for its result."""
        for delay in _REFRESH_WAIT_DELAYS_SECONDS:
            await asyncio.sleep(delay)
            current = await self.repository.get_connection(
                server_id=connection.server_id, account_id=connection.account_id
            )
            self._require_usable(current, server_key)
            assert current is not None
            if current.token_version != connection.token_version:
                return self._decrypt_access(current, server_key)
        raise MCPOAuthError("An OAuth token refresh is in progress. Try again.")

    async def load_server(self, server_id: str) -> MCPServerRecord | None:
        return await self._server_loader(server_id)


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _expires_soon(connection: MCPOAuthConnectionRecord) -> bool:
    if connection.expires_at is None:
        return False
    return connection.expires_at <= datetime.now(tz=UTC) + timedelta(seconds=REFRESH_SKEW_SECONDS)


def _with_query(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    reserved = set(params)
    merged = [(key, value) for key, value in query if key not in reserved] + list(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), ""))
