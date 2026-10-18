"""OAuth 2.0 protocol calls for MCP servers.

- RFC 9728: protected resource metadata (WWW-Authenticate ``resource_metadata``
  first, then the well-known URIs).
- RFC 8414: authorization server metadata, with OpenID Connect discovery as a
  fallback.
- RFC 7591: dynamic client registration.
- RFC 6749 + RFC 7636: authorization code with PKCE, refresh.
- RFC 8707: the ``resource`` parameter on authorization and token requests.
- RFC 7009: token revocation.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from src.outbound.urls import normalize_outbound_url

from .errors import MCPOAuthDiscoveryError, MCPOAuthError, MCPOAuthTokenError
from .http import MCPOAuthHTTP, OAuthHTTPResponse
from .models import OAuthClient, OAuthDiscoveryResult, OAuthTokenResponse

PROTECTED_RESOURCE_WELL_KNOWN = "/.well-known/oauth-protected-resource"
AUTHORIZATION_SERVER_WELL_KNOWN = "/.well-known/oauth-authorization-server"
OPENID_CONFIGURATION_WELL_KNOWN = "/.well-known/openid-configuration"
CLIENT_NAME = "DeltaLLM"
_RESOURCE_METADATA_PARAM = re.compile(r'resource_metadata\s*=\s*"([^"]{1,2048})"', re.IGNORECASE)
_PROBE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "deltallm", "version": "0.1.0"},
    },
}
_MAX_TOKEN_LIFETIME_SECONDS = 366 * 24 * 3600
_MAX_LABEL_LENGTH = 200


def _strip_slash(value: str) -> str:
    return value.rstrip("/") if len(value) > 1 else value


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _path(url: str) -> str:
    path = urlsplit(url).path or ""
    return "" if path in {"", "/"} else path.rstrip("/")


def _same_url(left: str, right: str) -> bool:
    return _strip_slash(left) == _strip_slash(right)


class MCPOAuthProtocolClient:
    def __init__(self, http: MCPOAuthHTTP) -> None:
        self.http = http

    # URL checks -------------------------------------------------------------

    def checked_endpoint(self, value: Any, *, field_name: str) -> str:
        """Validate an endpoint URL from metadata. Policy checks happen again on each call."""
        try:
            normalized = normalize_outbound_url(str(value or ""))
        except ValueError:
            raise MCPOAuthDiscoveryError(
                f"The metadata field {field_name} is not a valid URL"
            ) from None
        scheme = urlsplit(normalized).scheme
        if scheme != "https" and not (scheme == "http" and self.http.allow_http):
            raise MCPOAuthDiscoveryError(f"The metadata field {field_name} must use https")
        return normalized

    # Discovery --------------------------------------------------------------

    async def discover(self, server_url: str) -> OAuthDiscoveryResult:
        base_url = self.checked_endpoint(server_url, field_name="base_url").rstrip("/")
        resource_metadata = await self._find_protected_resource_metadata(base_url)
        if resource_metadata is not None:
            metadata, found_from, metadata_url = resource_metadata
            issuer = self.checked_endpoint(
                metadata["authorization_servers"][0], field_name="authorization_servers"
            )
            resource = str(metadata["resource"])
        else:
            # Older MCP servers have no resource metadata: the server origin is
            # the authorization server.
            issuer = _origin(base_url)
            resource = base_url
            found_from = ""
            metadata_url = ""
        (
            auth_metadata,
            auth_found_from,
            auth_metadata_url,
        ) = await self._authorization_server_metadata(issuer)
        if not found_from:
            found_from, metadata_url = auth_found_from, auth_metadata_url
        return OAuthDiscoveryResult(
            authorization_server=_strip_slash(str(auth_metadata.get("issuer") or issuer)),
            found_from=found_from,
            metadata_url=metadata_url,
            authorization_endpoint=self.checked_endpoint(
                auth_metadata.get("authorization_endpoint"), field_name="authorization_endpoint"
            ),
            token_endpoint=self.checked_endpoint(
                auth_metadata.get("token_endpoint"), field_name="token_endpoint"
            ),
            resource=resource,
            registration_endpoint=self._optional_endpoint(
                auth_metadata.get("registration_endpoint"), field_name="registration_endpoint"
            ),
            revocation_endpoint=self._optional_endpoint(
                auth_metadata.get("revocation_endpoint"), field_name="revocation_endpoint"
            ),
            scopes_supported=_string_list(
                (resource_metadata[0] if resource_metadata else {}).get("scopes_supported")
            )
            or _string_list(auth_metadata.get("scopes_supported")),
            code_challenge_methods_supported=_string_list(
                auth_metadata.get("code_challenge_methods_supported")
            ),
            token_endpoint_auth_methods_supported=_string_list(
                auth_metadata.get("token_endpoint_auth_methods_supported")
            ),
        )

    def _optional_endpoint(self, value: Any, *, field_name: str) -> str | None:
        if value is None or value == "":
            return None
        return self.checked_endpoint(value, field_name=field_name)

    async def _find_protected_resource_metadata(
        self, base_url: str
    ) -> tuple[dict[str, Any], str, str] | None:
        candidates: list[tuple[str, str]] = []
        header_url = await self._resource_metadata_from_challenge(base_url)
        if header_url is not None:
            candidates.append((header_url, "WWW-Authenticate header"))
        origin = _origin(base_url)
        path = _path(base_url)
        if path:
            candidates.append(
                (
                    f"{origin}{PROTECTED_RESOURCE_WELL_KNOWN}{path}",
                    f"{PROTECTED_RESOURCE_WELL_KNOWN}{path}",
                )
            )
        candidates.append(
            (f"{origin}{PROTECTED_RESOURCE_WELL_KNOWN}", PROTECTED_RESOURCE_WELL_KNOWN)
        )
        for url, found_from in candidates:
            metadata = await self._json_or_none(url)
            if metadata is None:
                continue
            resource = metadata.get("resource")
            servers = metadata.get("authorization_servers")
            if not isinstance(resource, str) or not isinstance(servers, list) or not servers:
                continue
            if not isinstance(servers[0], str):
                continue
            # RFC 9728 section 3.3: the metadata must name this resource. A
            # document for another resource is not used.
            if not (
                _same_url(resource, base_url)
                or _same_url(resource, origin)
                or base_url.startswith(_strip_slash(resource) + "/")
            ):
                continue
            return metadata, found_from, url
        return None

    async def _resource_metadata_from_challenge(self, base_url: str) -> str | None:
        try:
            response = await self.http.post_json(
                base_url,
                _PROBE_BODY,
                headers={"Accept": "application/json, text/event-stream"},
            )
        except MCPOAuthError:
            return None
        if response.status_code != 401:
            return None
        challenge = response.headers.get("www-authenticate") or ""
        match = _RESOURCE_METADATA_PARAM.search(challenge)
        if match is None:
            return None
        try:
            return self.checked_endpoint(match.group(1), field_name="resource_metadata")
        except MCPOAuthDiscoveryError:
            return None

    async def _authorization_server_metadata(self, issuer: str) -> tuple[dict[str, Any], str, str]:
        origin = _origin(issuer)
        path = _path(issuer)
        if path:
            candidates = [
                (
                    f"{origin}{AUTHORIZATION_SERVER_WELL_KNOWN}{path}",
                    AUTHORIZATION_SERVER_WELL_KNOWN,
                ),
                (
                    f"{origin}{OPENID_CONFIGURATION_WELL_KNOWN}{path}",
                    OPENID_CONFIGURATION_WELL_KNOWN,
                ),
                (
                    f"{origin}{path}{OPENID_CONFIGURATION_WELL_KNOWN}",
                    OPENID_CONFIGURATION_WELL_KNOWN,
                ),
            ]
        else:
            candidates = [
                (f"{origin}{AUTHORIZATION_SERVER_WELL_KNOWN}", AUTHORIZATION_SERVER_WELL_KNOWN),
                (f"{origin}{OPENID_CONFIGURATION_WELL_KNOWN}", OPENID_CONFIGURATION_WELL_KNOWN),
            ]
        for url, found_from in candidates:
            metadata = await self._json_or_none(url)
            if metadata is None:
                continue
            declared_issuer = metadata.get("issuer")
            # RFC 8414 section 3.3: the issuer in the document must match.
            if not isinstance(declared_issuer, str) or not _same_url(declared_issuer, issuer):
                continue
            if not metadata.get("authorization_endpoint") or not metadata.get("token_endpoint"):
                continue
            return metadata, found_from, url
        raise MCPOAuthDiscoveryError(
            "No OAuth authorization server metadata was found for this MCP server"
        )

    async def _json_or_none(self, url: str) -> dict[str, Any] | None:
        try:
            response = await self.http.get(url)
        except MCPOAuthError:
            return None
        if response.status_code != 200:
            return None
        return response.json_object()

    # Registration -----------------------------------------------------------

    async def register_client(
        self,
        discovery: OAuthDiscoveryResult,
        *,
        redirect_uri: str,
        scopes: list[str],
    ) -> OAuthClient:
        if not discovery.registration_endpoint:
            raise MCPOAuthDiscoveryError(
                "The authorization server does not support automatic client registration. "
                "Enter a client ID."
            )
        request: dict[str, Any] = {
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        if scopes:
            request["scope"] = " ".join(scopes)
        response = await self.http.post_json(discovery.registration_endpoint, request)
        payload = response.json_object()
        if response.status_code not in {200, 201} or payload is None:
            raise MCPOAuthDiscoveryError(
                f"Automatic client registration failed with status {response.status_code}"
            )
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise MCPOAuthDiscoveryError("Automatic client registration returned no client_id")
        secret = payload.get("client_secret")
        return OAuthClient(
            client_id=client_id.strip(),
            client_secret=secret if isinstance(secret, str) and secret else None,
        )

    # Tokens -----------------------------------------------------------------

    def _client_auth(
        self,
        discovery: OAuthDiscoveryResult,
        client: OAuthClient,
        form: dict[str, str],
    ) -> dict[str, str]:
        if client.client_secret is None:
            form["client_id"] = client.client_id
            return {}
        methods = discovery.token_endpoint_auth_methods_supported
        if "client_secret_post" in methods and "client_secret_basic" not in methods:
            form["client_id"] = client.client_id
            form["client_secret"] = client.client_secret
            return {}
        credentials = f"{_form_quote(client.client_id)}:{_form_quote(client.client_secret)}"
        return {"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode("ascii")}

    async def exchange_code(
        self,
        discovery: OAuthDiscoveryResult,
        client: OAuthClient,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> OAuthTokenResponse:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "resource": discovery.resource,
        }
        headers = self._client_auth(discovery, client, form)
        response = await self.http.post_form(discovery.token_endpoint, form, headers=headers)
        return _token_response(response)

    async def refresh(
        self,
        discovery: OAuthDiscoveryResult,
        client: OAuthClient,
        *,
        refresh_token: str,
    ) -> OAuthTokenResponse:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "resource": discovery.resource,
        }
        headers = self._client_auth(discovery, client, form)
        response = await self.http.post_form(discovery.token_endpoint, form, headers=headers)
        return _token_response(response)

    async def revoke(
        self,
        discovery: OAuthDiscoveryResult,
        client: OAuthClient,
        *,
        token: str,
        token_type_hint: str,
    ) -> bool:
        if not discovery.revocation_endpoint:
            return False
        form = {"token": token, "token_type_hint": token_type_hint}
        headers = self._client_auth(discovery, client, form)
        try:
            response = await self.http.post_form(
                discovery.revocation_endpoint, form, headers=headers
            )
        except MCPOAuthError:
            return False
        return response.status_code == 200


def _form_quote(value: str) -> str:
    # RFC 6749 section 2.3.1: encode the client credentials before Basic auth.
    return quote(value, safe="")


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _token_response(response: OAuthHTTPResponse) -> OAuthTokenResponse:
    payload = response.json_object()
    if response.status_code != 200 or payload is None:
        error_code = payload.get("error") if isinstance(payload, dict) else None
        safe_code = error_code if isinstance(error_code, str) and error_code.isascii() else None
        safe_code = safe_code[:64] if safe_code else None
        raise MCPOAuthTokenError(
            f"The token endpoint returned status {response.status_code}"
            + (f" ({safe_code})" if safe_code else ""),
            error_code=safe_code,
        )
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise MCPOAuthTokenError("The token endpoint returned no access_token")
    token_type = payload.get("token_type")
    if isinstance(token_type, str) and token_type.lower() not in {"bearer"}:
        raise MCPOAuthTokenError("The token endpoint returned a token type that is not Bearer")
    expires_in = payload.get("expires_in")
    lifetime: int | None = None
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        lifetime = max(0, min(int(expires_in), _MAX_TOKEN_LIFETIME_SECONDS))
    elif isinstance(expires_in, str) and expires_in.isdigit():
        lifetime = min(int(expires_in), _MAX_TOKEN_LIFETIME_SECONDS)
    refresh_token = payload.get("refresh_token")
    scope = payload.get("scope")
    return OAuthTokenResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=lifetime,
        refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else None,
        scopes=tuple(scope.split()) if isinstance(scope, str) else None,
        account_label=_label_from_id_token(payload.get("id_token")),
    )


def _label_from_id_token(value: Any) -> str | None:
    """Read a display name from an ID token.

    The token comes straight from the token endpoint over TLS. The gateway uses
    it only as a label in the UI, never for an access decision, so it does not
    check the signature.
    """
    if not isinstance(value, str) or value.count(".") != 2:
        return None
    try:
        segment = value.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(claims, dict):
        return None
    for claim in ("preferred_username", "email", "name"):
        label = claims.get(claim)
        if isinstance(label, str) and label.strip():
            return label.strip()[:_MAX_LABEL_LENGTH]
    return None
