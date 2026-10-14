"""Create or reuse the suite organization, team, test user, and API key.

Every step is safe to repeat. The test user gets a new random password on each
run, and the password lives only in process memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
from typing import Any

import httpx

from scripts.live_smoke.config import SUITE_MARKER, ResourceNames
from scripts.live_smoke.gateway_client import GatewayClient, GatewayError, error_text

KEY_LIFETIME = timedelta(hours=6)
SESSION_COOKIE_NAME = "deltallm_session"


@dataclass(frozen=True)
class TestUser:
    account_id: str
    email: str
    password: str

    def __repr__(self) -> str:
        return f"TestUser(account_id={self.account_id!r}, email={self.email!r})"


@dataclass(frozen=True)
class TestKey:
    token: str
    raw_key: str

    def __repr__(self) -> str:
        return f"TestKey(token={self.token!r})"


@dataclass(frozen=True)
class SuiteScope:
    organization_id: str
    team_id: str


def _get_or_none(admin: GatewayClient, path: str, action: str) -> dict[str, Any] | None:
    response = admin.call("GET", path, action, expect=(200, 404))
    return None if response.status_code == 404 else response.json()


def find_suite_organizations(admin: GatewayClient, names: ResourceNames) -> list[dict[str, Any]]:
    page = admin.json(
        "GET",
        "/ui/api/organizations",
        "find suite organization",
        params={"search": names.organization_id_base, "limit": 500},
    )
    return [
        org
        for org in page.get("data") or []
        if names.is_suite_organization(str(org.get("organization_id") or ""))
    ]


def ensure_organization(
    admin: GatewayClient, names: ResourceNames, tier_id: str
) -> tuple[SuiteScope, bool]:
    """Reuse the active suite organization, or create one. True means it is new."""

    active = [
        org
        for org in find_suite_organizations(admin, names)
        if org.get("lifecycle_state") in (None, "active")
    ]
    if active:
        organization_id = str(
            sorted(active, key=lambda org: org["organization_id"])[0]["organization_id"]
        )
        _ensure_primary_tier(admin, organization_id, tier_id)
        return SuiteScope(organization_id, names.team_id_for(organization_id)), False
    organization_id = names.new_organization_id()
    admin.json(
        "POST",
        "/ui/api/organizations",
        "create suite organization",
        json={
            "organization_id": organization_id,
            "organization_name": names.organization_name,
            "primary_tier": {"tier_id": tier_id},
            "metadata": {"managed_by": SUITE_MARKER},
        },
    )
    return SuiteScope(organization_id, names.team_id_for(organization_id)), True


def _ensure_primary_tier(admin: GatewayClient, organization_id: str, tier_id: str) -> None:
    base = f"/ui/api/organizations/{organization_id}/tier-assignments"
    assignments = admin.json("GET", base, "list organization tier assignments")["data"]
    primary = [
        item
        for item in assignments
        if item.get("assignment_type") == "primary" and item.get("enabled")
    ]
    for item in primary:
        if item.get("tier_id") == tier_id and not item.get("tier_version_id"):
            return
    if primary:
        admin.json(
            "PATCH",
            f"{base}/{primary[0]['assignment_id']}",
            "point organization at suite tier",
            json={"tier_id": tier_id, "tier_version_id": None},
        )
        return
    admin.json(
        "POST",
        base,
        "assign suite tier to organization",
        json={"tier_id": tier_id, "assignment_type": "primary"},
    )


def grant_organization_access(
    admin: GatewayClient, organization_id: str, callable_keys: list[str]
) -> str:
    """Grant every model where organization grants still apply.

    When the tier policy is authoritative (enforce mode) the gateway refuses the
    change with 409, and the tier alone decides access.
    """

    response = admin.call(
        "PUT",
        f"/ui/api/organizations/{organization_id}/asset-access",
        "grant organization access",
        expect=(200, 409),
        json={
            "mode": "grant",
            "selected_callable_keys": callable_keys,
            "select_all_selectable": True,
        },
    )
    if response.status_code == 409:
        return "tier decides access"
    return f"granted {len(callable_keys)} models"


def ensure_team(admin: GatewayClient, names: ResourceNames, scope: SuiteScope) -> bool:
    existing = _get_or_none(admin, f"/ui/api/teams/{scope.team_id}", "read suite team")
    if existing is not None:
        if existing.get("organization_id") != scope.organization_id:
            raise GatewayError(
                "reuse suite team",
                409,
                f"team {scope.team_id} belongs to another organization",
            )
        return False
    admin.json(
        "POST",
        "/ui/api/teams",
        "create suite team",
        json={
            "team_id": scope.team_id,
            "team_alias": names.team_alias,
            "organization_id": scope.organization_id,
        },
    )
    return True


def ensure_user(admin: GatewayClient, names: ResourceNames, scope: SuiteScope) -> TestUser:
    password = secrets.token_urlsafe(24)
    account = admin.json(
        "POST",
        "/ui/api/rbac/accounts",
        "create or update test user",
        json={
            "email": names.user_email,
            "password": password,
            "role": "org_user",
            "is_active": True,
        },
    )
    account_id = str(account["account_id"])
    admin.json(
        "POST",
        "/ui/api/rbac/organization-memberships",
        "add test user to organization",
        json={
            "account_id": account_id,
            "organization_id": scope.organization_id,
            "role": "org_member",
        },
    )
    admin.json(
        "POST",
        f"/ui/api/teams/{scope.team_id}/members",
        "add test user to team",
        # A team developer can only use self-service keys, which need a runtime user
        # that only sign-up or external sign-in creates. A team admin creates keys directly.
        json={"user_id": account_id, "user_role": "team_admin"},
    )
    return TestUser(account_id=account_id, email=names.user_email, password=password)


def delete_suite_keys(admin: GatewayClient, names: ResourceNames, team_id: str) -> int:
    """Delete keys left by an interrupted run, so the per-user key limit is never hit."""

    page = admin.json(
        "GET",
        "/ui/api/keys",
        "list suite keys",
        params={"team_id": team_id, "limit": 500},
    )
    deleted = 0
    for key in page.get("data") or []:
        if key.get("key_name") == names.key_name and key.get("token"):
            delete_key(admin, str(key["token"]))
            deleted += 1
    return deleted


def create_key_as_user(
    base_url: str,
    user: TestUser,
    names: ResourceNames,
    scope: SuiteScope,
    *,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> TestKey:
    """Sign in as the test user and create a key the way a member does in the UI."""

    with GatewayClient(base_url, timeout=timeout, transport=transport) as session:
        login = session.request(
            "POST",
            "/auth/internal/login",
            json={"email": user.email, "password": user.password},
        )
        if login.status_code != 200:
            raise GatewayError("sign in as test user", login.status_code, error_text(login))
        body = login.json()
        if body.get("mfa_required") or body.get("force_password_change"):
            raise GatewayError(
                "sign in as test user",
                login.status_code,
                "the test user must not need MFA or a password change",
            )
        # Send the session cookie explicitly: the jar drops Secure cookies on plain HTTP.
        session_token = login.cookies.get(SESSION_COOKIE_NAME)
        if not session_token:
            raise GatewayError("sign in as test user", 200, "no session cookie in the response")
        session.clear_cookies()
        expires = (datetime.now(tz=UTC) + KEY_LIFETIME).strftime("%Y-%m-%dT%H:%M:%SZ")
        created = session.json(
            "POST",
            "/ui/api/keys",
            "create API key as test user",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={session_token}"},
            json={
                "key_name": names.key_name,
                "team_id": scope.team_id,
                "owner_account_id": user.account_id,
                "expires": expires,
            },
        )
    raw_key = created.get("raw_key")
    if not raw_key:
        raise GatewayError("create API key as test user", 200, "response has no key")
    return TestKey(token=str(created["token"]), raw_key=str(raw_key))


def delete_key(admin: GatewayClient, token: str) -> None:
    admin.call("DELETE", f"/ui/api/keys/{token}", "delete test key", expect=(200, 404))
