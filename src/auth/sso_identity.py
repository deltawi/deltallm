from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from src.auth.roles import PlatformRole


class SSOSubjectSource(str, Enum):
    PROVIDER = "provider"
    EMAIL = "email"


class SSOAccountMatch(str, Enum):
    SUBJECT = "subject"
    EMAIL = "email"
    CREATED = "created"


class SSOIdentityOwnershipError(ValueError):
    def __init__(self) -> None:
        super().__init__("SSO email is not verified")


class AccountInactiveError(RuntimeError):
    pass


class LoginSessionCreationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SSOIdentityAssertion:
    provider: str
    subject: str
    email: str
    email_verified: bool | None
    subject_source: SSOSubjectSource

    def __post_init__(self) -> None:
        for field in ("provider", "subject", "email"):
            value = getattr(self, field).strip()
            if field == "email":
                value = value.lower()
            if not value:
                raise ValueError(f"{field} is required")
            object.__setattr__(self, field, value)
        object.__setattr__(self, "subject_source", SSOSubjectSource(self.subject_source))

    @classmethod
    def from_callback(cls, payload: Mapping[str, object], *, provider: str) -> SSOIdentityAssertion:
        email = str(payload.get("email") or "").strip().lower()
        source = payload.get("provider_subject_source")
        subject = str(payload.get("provider_subject") or "").strip()
        # A runtime user ID or an unclassified fallback is not provider ownership proof.
        if source != SSOSubjectSource.PROVIDER or not subject:
            source, subject = SSOSubjectSource.EMAIL, email
        verified = payload.get("email_verified")
        return cls(
            provider=provider,
            subject=subject,
            email=email,
            email_verified=verified if isinstance(verified, bool) else None,
            subject_source=SSOSubjectSource(source),
        )

    def require_verified_email(self) -> None:
        if self.email_verified is not True:
            raise SSOIdentityOwnershipError()

    def require_ownership(self, match: SSOAccountMatch, *, role: str) -> None:
        if (
            self.subject_source is SSOSubjectSource.EMAIL
            or match is SSOAccountMatch.EMAIL
            or (match is SSOAccountMatch.CREATED and role == PlatformRole.ADMIN)
        ):
            self.require_verified_email()
