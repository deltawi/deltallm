from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExternalPurpose(StrEnum):
    EXCHANGE = "gateway_session_exchange"
    REVOKE = "gateway_session_revoke"
    SUSPEND = "gateway_subject_suspend"
    LINK = "gateway_account_link"
    RUNTIME_BIND = "gateway_runtime_identity_bind"


class ExternalAssertionClaims(BaseModel):
    """Only these signed claims can establish external authentication."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    iss: str = Field(min_length=1, max_length=512)
    aud: str = Field(min_length=1, max_length=200)
    sub: str = Field(min_length=1, max_length=200)
    identity_issuer: str = Field(min_length=1, max_length=512)
    iat: int = Field(ge=0, le=253402300799)
    nbf: int = Field(ge=0, le=253402300799)
    exp: int = Field(ge=0, le=253402300799)
    jti: str = Field(min_length=22, max_length=200, repr=False)
    purpose: str
    binding_id: str = Field(min_length=1, max_length=200)
    external_customer_id: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320, repr=False)
    email_verified: bool
    external_session_id: str = Field(min_length=1, max_length=200, repr=False)
    auth_time: int = Field(ge=0, le=253402300799)
    external_session_expires_at: int = Field(ge=0, le=253402300799)

    @field_validator("sub", "binding_id", "external_customer_id", "external_session_id")
    @classmethod
    def require_bounded_reference(cls, value: str) -> str:
        if (
            len(value.encode("utf-8")) > 200
            or value != value.strip()
            or re.search(r"[\x00-\x1f\x7f]", value)
        ):
            raise ValueError("Invalid external identity reference")
        return value

    @field_validator("email")
    @classmethod
    def require_email(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid verified email")
        return value.strip().lower()

    @field_validator("jti")
    @classmethod
    def require_nonce(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("Invalid assertion nonce")
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(decoded) < 16:
            raise ValueError("Assertion nonce is too short")
        return value


@dataclass(frozen=True, slots=True)
class VerifiedExternalAssertion:
    integration_id: str
    claims: ExternalAssertionClaims = field(repr=False)

    @property
    def provider(self) -> str:
        return "external:" + hashlib.sha256(self.claims.identity_issuer.encode()).hexdigest()

    def digest(self, purpose: str, value: str) -> str:
        # Length prefixes prevent namespace collisions in attacker-controlled references.
        parts = ("external-auth-v1", self.integration_id, purpose, value)
        encoded = b"".join(len(item.encode()).to_bytes(4, "big") + item.encode() for item in parts)
        return hashlib.sha256(encoded).hexdigest()

    @property
    def nonce_hash(self) -> str:
        return self.digest("nonce", self.claims.jti)

    @property
    def parent_hash(self) -> str:
        return self.digest("parent", self.claims.external_session_id)
