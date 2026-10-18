"""Typed views of the OAuth part of an MCP server's ``auth_config``.

Stored layout for ``auth_mode == "oauth2"``::

    {
      "client_id": "abc" | null,
      "client_id_source": "manual" | "dynamic" | null,
      "client_secret_ciphertext": "v1.<key id>.<payload>" | null,
      "scopes": ["repo", "read:user"],
      "discovery": {...} | null,
      "discovery_error": "..." | null
    }
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ClientIdSource = Literal["manual", "dynamic"]

MAX_SCOPES = 32
MAX_SCOPE_LENGTH = 200


def _str_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item.strip())


def normalize_scopes(value: Any) -> list[str]:
    """Accept a list or a space/comma separated string. Keep order, drop duplicates."""
    if value is None:
        return []
    if isinstance(value, str):
        items = value.replace(",", " ").split()
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        raise ValueError("scopes must be a list of strings")
    scopes: list[str] = []
    for item in items:
        if not item:
            continue
        if len(item) > MAX_SCOPE_LENGTH or any(
            ord(character) <= 0x20 or character in {'"', "\\"} or ord(character) == 0x7F
            for character in item
        ):
            raise ValueError("scopes contain a value that is not allowed")
        if item not in scopes:
            scopes.append(item)
    if len(scopes) > MAX_SCOPES:
        raise ValueError(f"scopes can have at most {MAX_SCOPES} values")
    return scopes


@dataclass(frozen=True)
class OAuthDiscoveryResult:
    authorization_server: str
    found_from: str
    metadata_url: str
    authorization_endpoint: str
    token_endpoint: str
    resource: str
    registration_endpoint: str | None = None
    revocation_endpoint: str | None = None
    scopes_supported: tuple[str, ...] = ()
    code_challenge_methods_supported: tuple[str, ...] = ()
    token_endpoint_auth_methods_supported: tuple[str, ...] = ()
    checked_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    @property
    def pkce_s256_supported(self) -> bool:
        return "S256" in self.code_challenge_methods_supported

    @property
    def dynamic_registration_supported(self) -> bool:
        return bool(self.registration_endpoint)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "scopes_supported",
            "code_challenge_methods_supported",
            "token_endpoint_auth_methods_supported",
        ):
            payload[key] = list(payload[key])
        return payload

    def to_public_json(self) -> dict[str, Any]:
        """Fields the admin UI shows. Endpoints are public metadata, not secrets."""
        return {
            **self.to_json(),
            "pkce_s256_supported": self.pkce_s256_supported,
            "dynamic_registration_supported": self.dynamic_registration_supported,
        }

    @classmethod
    def from_json(cls, value: Any) -> OAuthDiscoveryResult | None:
        if not isinstance(value, dict):
            return None
        required = {
            key: _str_or_none(value.get(key))
            for key in (
                "authorization_server",
                "found_from",
                "metadata_url",
                "authorization_endpoint",
                "token_endpoint",
                "resource",
            )
        }
        if any(item is None for item in required.values()):
            return None
        return cls(
            authorization_server=str(required["authorization_server"]),
            found_from=str(required["found_from"]),
            metadata_url=str(required["metadata_url"]),
            authorization_endpoint=str(required["authorization_endpoint"]),
            token_endpoint=str(required["token_endpoint"]),
            resource=str(required["resource"]),
            registration_endpoint=_str_or_none(value.get("registration_endpoint")),
            revocation_endpoint=_str_or_none(value.get("revocation_endpoint")),
            scopes_supported=_str_tuple(value.get("scopes_supported")),
            code_challenge_methods_supported=_str_tuple(
                value.get("code_challenge_methods_supported")
            ),
            token_endpoint_auth_methods_supported=_str_tuple(
                value.get("token_endpoint_auth_methods_supported")
            ),
            checked_at=str(value.get("checked_at") or ""),
        )


@dataclass(frozen=True)
class OAuth2ServerSettings:
    client_id: str | None = None
    client_id_source: ClientIdSource | None = None
    client_secret_ciphertext: str | None = field(default=None, repr=False)
    scopes: tuple[str, ...] = ()
    discovery: OAuthDiscoveryResult | None = None
    discovery_error: str | None = None

    @classmethod
    def from_auth_config(cls, value: Any) -> OAuth2ServerSettings:
        config = value if isinstance(value, dict) else {}
        source = config.get("client_id_source")
        return cls(
            client_id=_str_or_none(config.get("client_id")),
            client_id_source=source if source in {"manual", "dynamic"} else None,
            client_secret_ciphertext=_str_or_none(config.get("client_secret_ciphertext")),
            scopes=_str_tuple(config.get("scopes")),
            discovery=OAuthDiscoveryResult.from_json(config.get("discovery")),
            discovery_error=_str_or_none(config.get("discovery_error")),
        )

    def to_auth_config(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "client_id_source": self.client_id_source,
            "client_secret_ciphertext": self.client_secret_ciphertext,
            "scopes": list(self.scopes),
            "discovery": self.discovery.to_json() if self.discovery is not None else None,
            "discovery_error": self.discovery_error,
        }

    def to_public_json(self) -> dict[str, Any]:
        """Admin view. The client secret is reported only as set or not set."""
        return {
            "client_id": self.client_id,
            "client_id_source": self.client_id_source,
            "client_secret_set": bool(self.client_secret_ciphertext),
            "scopes": list(self.scopes),
            "discovery": self.discovery.to_public_json() if self.discovery is not None else None,
            "discovery_error": self.discovery_error,
        }


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class OAuthTokenResponse:
    access_token: str = field(repr=False)
    token_type: str | None = None
    expires_in: int | None = None
    refresh_token: str | None = field(default=None, repr=False)
    scopes: tuple[str, ...] | None = None
    account_label: str | None = None


@dataclass(frozen=True)
class OAuthAccessToken:
    value: str = field(repr=False)
    token_version: int = 1
