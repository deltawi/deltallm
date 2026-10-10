from __future__ import annotations

import re
from ipaddress import ip_network
from urllib.parse import urlsplit
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExternalVerificationKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    kid: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    public_key: str = Field(min_length=1, max_length=8192, repr=False)

    @field_validator("public_key")
    @classmethod
    def require_rsa_public_key(cls, value: str) -> str:
        try:
            key = load_pem_public_key(value.encode("ascii"))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("External auth requires an RSA public key") from exc
        if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
            raise ValueError("External auth requires RSA with at least 2048 bits")
        return value


class ExternalAuthIntegrationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    integration_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    issuer: str = Field(min_length=1, max_length=512)
    audience: str = Field(min_length=1, max_length=200)
    identity_issuer: str = Field(min_length=1, max_length=512)
    keys: tuple[ExternalVerificationKey, ...] = Field(min_length=1, max_length=4)

    @field_validator("keys", mode="before")
    @classmethod
    def freeze_keys(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("issuer", "identity_issuer")
    @classmethod
    def require_exact_https_issuer(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or re.search(r"[\s\x00-\x1f]", value)
        ):
            raise ValueError("External auth issuer must be an exact HTTPS URL")
        return value

    @model_validator(mode="after")
    def require_unique_keys(self) -> ExternalAuthIntegrationSettings:
        if len({key.kid for key in self.keys}) != len(self.keys):
            raise ValueError("External auth key IDs must be unique")
        return self


class ExternalAuthSettings(BaseModel):
    """Startup trust and capacity. PostgreSQL owns live enablement."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    enabled: bool = False
    deployment_protocol: Literal["external_customer_v1"] | None = None
    integrations: tuple[ExternalAuthIntegrationSettings, ...] = Field(default=(), max_length=8)
    assertion_lifetime_seconds: int = Field(default=60, ge=1, le=60)
    clock_allowance_seconds: int = Field(default=10, ge=0, le=10)
    child_lifetime_seconds: int = Field(default=300, ge=60, le=300)
    parent_lifetime_seconds: int = Field(default=43200, ge=300, le=43200)
    previous_overlap_seconds: int = Field(default=30, ge=0, le=30)
    subject_exchanges_per_minute: int = Field(default=12, ge=1, le=60)
    binding_exchanges_per_minute: int = Field(default=60, ge=1, le=600)
    integration_exchanges_per_minute: int = Field(default=1200, ge=1, le=1200)
    cleanup_batch_size: int = Field(default=1000, ge=1, le=1000)
    cleanup_interval_seconds: int = Field(default=5, ge=1, le=60)
    allowed_origins: tuple[str, ...] = Field(default=(), max_length=16)
    trusted_proxy_cidrs: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("integrations", "allowed_origins", "trusted_proxy_cidrs", mode="before")
    @classmethod
    def freeze_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("allowed_origins")
    @classmethod
    def require_exact_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            parts = urlsplit(value)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.path
                or parts.query
                or parts.fragment
                or parts.username
                or parts.password
                or re.search(r"[\s\x00-\x1f]", value)
            ):
                raise ValueError("External browser origin must be an exact HTTPS origin")
        if len(set(values)) != len(values):
            raise ValueError("External browser origins must be unique")
        return values

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def require_proxy_networks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            network = ip_network(value, strict=True)
            if network.prefixlen == 0:
                raise ValueError("External auth cannot trust all proxy addresses")
        if len(set(values)) != len(values):
            raise ValueError("External proxy networks must be unique")
        return values

    @model_validator(mode="after")
    def require_consistent_trust(self) -> ExternalAuthSettings:
        if self.enabled and not self.integrations:
            raise ValueError("Enabled external auth requires configured integrations")
        for attribute in ("integration_id", "issuer"):
            values = {getattr(item, attribute) for item in self.integrations}
            if len(values) != len(self.integrations):
                raise ValueError("External auth integration IDs and issuers must be unique")
        if self.parent_lifetime_seconds < self.child_lifetime_seconds:
            raise ValueError("External child lifetime exceeds parent lifetime")
        rate = len(self.integrations) * self.integration_exchanges_per_minute / 60
        if self.cleanup_batch_size / self.cleanup_interval_seconds < rate * 1.25:
            raise ValueError(
                "External replay cleanup capacity must exceed admitted traffic by 25 percent"
            )
        return self

    def require_deployment(
        self,
        *,
        audit_mode: str,
        audit_worker_enabled: bool,
        cache_worker_enabled: bool,
        cache_ttl_seconds: int,
    ) -> None:
        if not self.enabled:
            return
        if self.deployment_protocol != "external_customer_v1":
            raise ValueError(
                "External auth requires acknowledgement of the external_customer_v1 deployment protocol"
            )
        if not self.allowed_origins:
            raise ValueError("External auth requires an explicit Console browser origin")
        if audit_mode != "outbox" or not audit_worker_enabled:
            raise ValueError("External auth requires durable audit outbox mode and its worker")
        if not cache_worker_enabled or cache_ttl_seconds > 60:
            raise ValueError(
                "External auth requires its revocation worker and API key cache lifetime at most 60 seconds"
            )
