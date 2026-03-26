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


class ChangePasswordRequest(BaseModel):
    current_password: str | None = None
    new_password: str


class MFAStartResponse(BaseModel):
    secret: str
    otpauth_url: str


class InvitationTokenResponse(BaseModel):
    valid: bool
    invitation_id: str | None = None
    email: str | None = None
    status: str | None = None
    invite_scope_type: str | None = None
    inviter_email: str | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    password_required: bool = False


class InvitationAcceptRequest(BaseModel):
    token: str
    password: str | None = None


class InvitationAcceptResponse(BaseModel):
    accepted: bool
    session_established: bool
    next_step: str
    account_id: str
    email: str
    role: str
    mfa_enabled: bool = False
    mfa_required: bool = False
    mfa_prompt: bool = False
    force_password_change: bool = False


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class ResetPasswordTokenResponse(BaseModel):
    valid: bool
    email: str | None = None
    expires_at: datetime | None = None


class UIAccessResponse(BaseModel):
    dashboard: bool = False
    models: bool = False
    model_admin: bool = False
    route_groups: bool = False
    prompts: bool = False
    mcp_servers: bool = False
    mcp_approvals: bool = False
    keys: bool = False
    organizations: bool = False
    organization_create: bool = False
    teams: bool = False
    team_create: bool = False
    people_access: bool = False
    usage: bool = False
    audit: bool = False
    batches: bool = False
    guardrails: bool = False
    playground: bool = False
    settings: bool = False


class CurrentSessionResponse(BaseModel):
    authenticated: bool
    account_id: str | None = None
    email: str | None = None
    role: str | None = None
    effective_permissions: list[str] = Field(default_factory=list)
    ui_access: UIAccessResponse = Field(default_factory=UIAccessResponse)
    organization_memberships: list[dict[str, Any]] = Field(default_factory=list)
    team_memberships: list[dict[str, Any]] = Field(default_factory=list)
    mfa_enabled: bool = False
    mfa_verified: bool = False
    mfa_prompt: bool = False
    force_password_change: bool = False
