from __future__ import annotations

from dataclasses import dataclass

from src.models.platform_auth import PlatformAuthContext
from src.services.identity.sso_group_sync import SSOGroupSyncResult


@dataclass
class LoginResult:
    context: PlatformAuthContext
    session_token: str
    mfa_required: bool
    mfa_prompt: bool
    group_sync: SSOGroupSyncResult | None = None


@dataclass(frozen=True)
class AccountAuthState:
    account_id: str
    email: str
    has_local_password: bool
    has_sso_identity: bool
