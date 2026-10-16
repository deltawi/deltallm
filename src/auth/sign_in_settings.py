"""Settings for the sign-in methods that an operator allows."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.auth.email_domains import MAX_EMAIL_DOMAINS, email_domain, normalize_email_domains


class SSOSignInSettings(BaseModel):
    """Who can sign in with SSO. The defaults keep SSO open to every identity provider user."""

    model_config = ConfigDict(extra="forbid")

    allow_new_accounts: bool = True
    allowed_email_domains: list[str] = Field(default_factory=list, max_length=MAX_EMAIL_DOMAINS)

    @field_validator("allowed_email_domains", mode="before")
    @classmethod
    def normalize_domains_input(cls, value: object) -> list[object]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return value
        raise ValueError("allowed_email_domains must be a list of domains")

    @field_validator("allowed_email_domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        return normalize_email_domains(value, field="allowed_email_domains")

    def admits_email(self, email: str, *, email_verified: bool | None) -> bool:
        """Apply the domain gate. An empty list admits every email.

        With a list, the identity provider must also assert a verified email, because an
        unverified email claim (for example, on a shared IdP) proves nothing about the domain.
        """

        if not self.allowed_email_domains:
            return True
        domain = email_domain(email)
        return email_verified is True and domain in self.allowed_email_domains


def password_login_enabled(general_settings: Any) -> bool:
    """`general_settings.password_login_enabled`; password sign-in stays on when it is not set."""

    return getattr(general_settings, "password_login_enabled", True) is not False


def sso_sign_in_settings(general_settings: Any) -> SSOSignInSettings:
    settings = getattr(general_settings, "sso_sign_in", None)
    return settings if isinstance(settings, SSOSignInSettings) else SSOSignInSettings()
