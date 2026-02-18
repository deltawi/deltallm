from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PlatformAuthContext(BaseModel):
    account_id: str
    email: str
    role: str
    mfa_enabled: bool = False
    mfa_verified: bool = False
    force_password_change: bool = False
    permissions: list[str] = Field(default_factory=list)
    organization_memberships: list[dict[str, Any]] = Field(default_factory=list)
    team_memberships: list[dict[str, Any]] = Field(default_factory=list)
    session_expires_at: datetime | None = None


class InternalLoginRequest(BaseModel):
    email: str
    password: str
    mfa_code: str | None = None


class InternalLoginResponse(BaseModel):
    account_id: str
    email: str
    role: str
    mfa_enabled: bool
    mfa_required: bool
    mfa_prompt: bool
    force_password_change: bool


class MFAVerifyRequest(BaseModel):
    code: str


class MFAStartResponse(BaseModel):
    secret: str
    otpauth_url: str


class CurrentSessionResponse(BaseModel):
    authenticated: bool
    account_id: str | None = None
    email: str | None = None
    role: str | None = None
    mfa_enabled: bool = False
    mfa_verified: bool = False
    mfa_prompt: bool = False
    force_password_change: bool = False
