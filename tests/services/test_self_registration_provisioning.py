from __future__ import annotations

import json
from typing import Any

import pytest

from src.config import AppConfig, SelfRegistrationSettings
from src.services.platform_identity_service import PlatformIdentityService
from src.services.self_registration_provisioning import SelfRegistrationProvisioningService


def _enabled_settings() -> SelfRegistrationSettings:
    cfg = AppConfig.model_validate(
        {
            "general_settings": {
                "self_registration": {
                    "enabled": True,
                    "mode": "sso_allowed_domain",
                    "allowed_domains": ["example.com"],
                    "default_org": {
                        "id": "org-sandbox",
                        "name": "Developer Sandbox",
                        "max_budget": 100,
                        "soft_budget": 80,
                        "rpm_limit": 300,
                        "tpm_limit": 500_000,
                        "rph_limit": 2_000,
                        "rpd_limit": 10_000,
                        "tpd_limit": 5_000_000,
                    },
                    "default_team": {
                        "id": "team-self-serve",
                        "alias": "Self Serve",
                        "role": "team_developer",
                        "max_budget": 50,
                        "rpm_limit": 100,
                        "tpm_limit": 200_000,
                        "self_service_keys_enabled": True,
                        "self_service_max_keys_per_user": 2,
                        "self_service_budget_ceiling": 5,
                        "self_service_require_expiry": True,
                        "self_service_max_expiry_days": 14,
                    },
                    "default_user": {
                        "max_budget": 10,
                        "soft_budget": 8,
                        "rpm_limit": 30,
                        "tpm_limit": 50_000,
                        "rph_limit": 200,
                        "rpd_limit": 1_000,
                        "tpd_limit": 500_000,
                    },
                }
            }
        }
    )
    return cfg.general_settings.self_registration


class FakeSelfRegistrationDB:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, Any]] = {}
        self.organizations: dict[str, dict[str, Any]] = {}
        self.teams: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.organization_memberships: dict[tuple[str, str], dict[str, Any]] = {}
        self.team_memberships: dict[tuple[str, str], dict[str, Any]] = {}

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = _normalize_sql(query)
        if "from deltallm_teamtable" in normalized and "where team_id = $1" in normalized:
            team = self.teams.get(str(params[0]))
            return [dict(team)] if team else []
        if "from deltallm_platformaccount" in normalized and "lower(email) = lower($1)" in normalized:
            email = str(params[0]).strip().lower()
            account = self._account_by_email(email)
            return [dict(account)] if account else []
        if "from deltallm_platformaccount" in normalized and "where account_id = $1" in normalized:
            account = self.accounts.get(str(params[0]))
            return [dict(account)] if account else []
        if "from deltallm_usertable" in normalized and "where user_id = $1" in normalized:
            account_id = str(params[0])
            email = str(params[1]).strip().lower()
            by_id = self.users.get(account_id)
            if by_id is not None:
                return [dict(by_id)]
            by_email = next(
                (
                    user
                    for user in self.users.values()
                    if str(user.get("user_email") or "").lower() == email
                ),
                None,
            )
            return [dict(by_email)] if by_email else []
        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        normalized = _normalize_sql(query)
        if "insert into deltallm_platformaccount" in normalized:
            return self._insert_platform_account(*params)
        if "insert into deltallm_organizationtable" in normalized:
            return self._insert_organization(*params)
        if "insert into deltallm_teamtable" in normalized:
            return self._insert_team(*params)
        if "insert into deltallm_organizationmembership" in normalized:
            return self._insert_organization_membership(*params)
        if "insert into deltallm_teammembership" in normalized:
            return self._insert_team_membership(*params)
        if "insert into deltallm_usertable" in normalized:
            return self._insert_runtime_user(*params)
        return 0

    def add_account(
        self,
        *,
        account_id: str,
        email: str,
        role: str = "org_user",
        is_active: bool = True,
    ) -> None:
        self.accounts[account_id] = {
            "account_id": account_id,
            "email": email.strip().lower(),
            "password_hash": None,
            "role": role,
            "is_active": is_active,
            "force_password_change": False,
            "mfa_enabled": False,
            "created_at": None,
            "updated_at": None,
            "last_login_at": None,
        }

    def _account_by_email(self, email: str) -> dict[str, Any] | None:
        return next(
            (
                account
                for account in self.accounts.values()
                if str(account.get("email") or "").lower() == email
            ),
            None,
        )

    def _insert_platform_account(self, email: str, role: str, is_active: bool) -> int:
        normalized_email = email.strip().lower()
        if self._account_by_email(normalized_email) is not None:
            return 1
        account_id = f"acct-{len(self.accounts) + 1}"
        self.add_account(
            account_id=account_id,
            email=normalized_email,
            role=role,
            is_active=is_active,
        )
        return 1

    def _insert_organization(
        self,
        organization_id: str,
        organization_name: str | None,
        max_budget: float | None,
        soft_budget: float | None,
        rpm_limit: int | None,
        tpm_limit: int | None,
        rph_limit: int | None,
        rpd_limit: int | None,
        tpd_limit: int | None,
        metadata: str,
    ) -> int:
        if organization_id in self.organizations:
            return 0
        self.organizations[organization_id] = {
            "organization_id": organization_id,
            "organization_name": organization_name,
            "max_budget": max_budget,
            "soft_budget": soft_budget,
            "spend": 0,
            "rpm_limit": rpm_limit,
            "tpm_limit": tpm_limit,
            "rph_limit": rph_limit,
            "rpd_limit": rpd_limit,
            "tpd_limit": tpd_limit,
            "metadata": json.loads(metadata),
        }
        return 1

    def _insert_team(
        self,
        team_id: str,
        team_alias: str | None,
        organization_id: str,
        max_budget: float | None,
        soft_budget: float | None,
        rpm_limit: int | None,
        tpm_limit: int | None,
        rph_limit: int | None,
        rpd_limit: int | None,
        tpd_limit: int | None,
        metadata: str,
        self_service_keys_enabled: bool,
        self_service_max_keys_per_user: int | None,
        self_service_budget_ceiling: float | None,
        self_service_require_expiry: bool,
        self_service_max_expiry_days: int | None,
    ) -> int:
        if team_id in self.teams:
            return 0
        self.teams[team_id] = {
            "team_id": team_id,
            "team_alias": team_alias,
            "organization_id": organization_id,
            "max_budget": max_budget,
            "soft_budget": soft_budget,
            "spend": 0,
            "rpm_limit": rpm_limit,
            "tpm_limit": tpm_limit,
            "rph_limit": rph_limit,
            "rpd_limit": rpd_limit,
            "tpd_limit": tpd_limit,
            "blocked": False,
            "metadata": json.loads(metadata),
            "self_service_keys_enabled": self_service_keys_enabled,
            "self_service_max_keys_per_user": self_service_max_keys_per_user,
            "self_service_budget_ceiling": self_service_budget_ceiling,
            "self_service_require_expiry": self_service_require_expiry,
            "self_service_max_expiry_days": self_service_max_expiry_days,
        }
        return 1

    def _insert_organization_membership(
        self,
        account_id: str,
        organization_id: str,
        role: str,
    ) -> int:
        key = (account_id, organization_id)
        self.organization_memberships.setdefault(
            key,
            {"account_id": account_id, "organization_id": organization_id, "role": role},
        )
        return 1

    def _insert_team_membership(self, account_id: str, team_id: str, role: str) -> int:
        key = (account_id, team_id)
        self.team_memberships.setdefault(
            key,
            {"account_id": account_id, "team_id": team_id, "role": role},
        )
        return 1

    def _insert_runtime_user(
        self,
        user_id: str,
        user_email: str,
        user_role: str,
        max_budget: float | None,
        soft_budget: float | None,
        rpm_limit: int | None,
        tpm_limit: int | None,
        rph_limit: int | None,
        rpd_limit: int | None,
        tpd_limit: int | None,
        team_id: str,
        metadata: str,
    ) -> int:
        normalized_email = user_email.strip().lower()
        if user_id in self.users:
            return 0
        if any(str(user.get("user_email") or "").lower() == normalized_email for user in self.users.values()):
            return 0
        self.users[user_id] = {
            "user_id": user_id,
            "user_email": normalized_email,
            "user_role": user_role,
            "max_budget": max_budget,
            "soft_budget": soft_budget,
            "spend": 0,
            "rpm_limit": rpm_limit,
            "tpm_limit": tpm_limit,
            "rph_limit": rph_limit,
            "rpd_limit": rpd_limit,
            "tpd_limit": tpd_limit,
            "team_id": team_id,
            "metadata": json.loads(metadata),
        }
        return 1


def _normalize_sql(query: str) -> str:
    return " ".join(query.lower().split())


def _service(db: FakeSelfRegistrationDB) -> SelfRegistrationProvisioningService:
    return SelfRegistrationProvisioningService(
        db_client=db,
        platform_identity_service=PlatformIdentityService(db_client=db, salt="salt-key"),
    )


@pytest.mark.asyncio
async def test_provision_from_defaults_seeds_sandbox_records() -> None:
    db = FakeSelfRegistrationDB()
    service = _service(db)

    result = await service.provision_from_defaults(
        email=" Developer@Example.COM ",
        settings=_enabled_settings(),
    )

    assert result.as_dict() == {
        "account_id": "acct-1",
        "email": "developer@example.com",
        "organization_id": "org-sandbox",
        "team_id": "team-self-serve",
        "user_id": "acct-1",
        "team_role": "team_developer",
        "account_is_active": True,
    }
    assert db.organizations["org-sandbox"]["max_budget"] == 100
    assert db.organizations["org-sandbox"]["soft_budget"] == 80
    assert db.organizations["org-sandbox"]["rpm_limit"] == 300
    assert db.organizations["org-sandbox"]["metadata"]["source"] == "self_registration"
    assert db.teams["team-self-serve"]["organization_id"] == "org-sandbox"
    assert db.teams["team-self-serve"]["max_budget"] == 50
    assert db.teams["team-self-serve"]["self_service_keys_enabled"] is True
    assert db.teams["team-self-serve"]["self_service_budget_ceiling"] == 5
    assert db.organization_memberships[("acct-1", "org-sandbox")]["role"] == "org_member"
    assert db.team_memberships[("acct-1", "team-self-serve")]["role"] == "team_developer"
    assert db.users["acct-1"]["user_email"] == "developer@example.com"
    assert db.users["acct-1"]["max_budget"] == 10
    assert db.users["acct-1"]["team_id"] == "team-self-serve"


@pytest.mark.asyncio
async def test_provision_from_defaults_preserves_existing_admin_changes() -> None:
    db = FakeSelfRegistrationDB()
    db.add_account(
        account_id="acct-existing",
        email="existing@example.com",
        role="platform_admin",
        is_active=False,
    )
    db.organizations["org-sandbox"] = {
        "organization_id": "org-sandbox",
        "organization_name": "Admin Edited Org",
        "max_budget": 999,
        "soft_budget": 900,
        "rpm_limit": 999,
    }
    db.teams["team-self-serve"] = {
        "team_id": "team-self-serve",
        "team_alias": "Admin Edited Team",
        "organization_id": "org-sandbox",
        "max_budget": 777,
        "self_service_keys_enabled": False,
        "self_service_budget_ceiling": 123,
    }
    db.users["acct-existing"] = {
        "user_id": "acct-existing",
        "user_email": "existing@example.com",
        "max_budget": 500,
        "team_id": "custom-team",
    }
    db.organization_memberships[("acct-existing", "org-sandbox")] = {
        "account_id": "acct-existing",
        "organization_id": "org-sandbox",
        "role": "org_admin",
    }
    db.team_memberships[("acct-existing", "team-self-serve")] = {
        "account_id": "acct-existing",
        "team_id": "team-self-serve",
        "role": "team_admin",
    }

    result = await _service(db).provision_from_defaults(
        email="existing@example.com",
        settings=_enabled_settings(),
    )

    assert result.account_id == "acct-existing"
    assert result.account_is_active is False
    assert db.accounts["acct-existing"]["role"] == "platform_admin"
    assert db.organizations["org-sandbox"]["organization_name"] == "Admin Edited Org"
    assert db.organizations["org-sandbox"]["max_budget"] == 999
    assert db.teams["team-self-serve"]["team_alias"] == "Admin Edited Team"
    assert db.teams["team-self-serve"]["self_service_keys_enabled"] is False
    assert db.users["acct-existing"]["max_budget"] == 500
    assert db.users["acct-existing"]["team_id"] == "custom-team"
    assert db.organization_memberships[("acct-existing", "org-sandbox")]["role"] == "org_admin"
    assert db.team_memberships[("acct-existing", "team-self-serve")]["role"] == "team_admin"


@pytest.mark.asyncio
async def test_provision_from_defaults_returns_existing_runtime_user_for_same_email() -> None:
    db = FakeSelfRegistrationDB()
    db.users["legacy-user"] = {
        "user_id": "legacy-user",
        "user_email": "developer@example.com",
        "max_budget": 500,
        "team_id": "legacy-team",
    }

    result = await _service(db).provision_from_defaults(
        email="developer@example.com",
        settings=_enabled_settings(),
    )

    assert result.account_id == "acct-1"
    assert result.user_id == "legacy-user"
    assert "acct-1" not in db.users
    assert db.users["legacy-user"]["max_budget"] == 500
    assert db.users["legacy-user"]["team_id"] == "legacy-team"


@pytest.mark.asyncio
async def test_provision_from_defaults_rejects_existing_team_in_wrong_org() -> None:
    db = FakeSelfRegistrationDB()
    db.teams["team-self-serve"] = {
        "team_id": "team-self-serve",
        "organization_id": "org-other",
    }

    with pytest.raises(ValueError, match="different organization"):
        await _service(db).provision_from_defaults(
            email="developer@example.com",
            settings=_enabled_settings(),
        )

    assert db.organizations == {}
    assert db.accounts == {}


@pytest.mark.asyncio
async def test_provision_from_defaults_rejects_disabled_settings() -> None:
    db = FakeSelfRegistrationDB()
    settings = AppConfig.model_validate({}).general_settings.self_registration

    with pytest.raises(ValueError, match="disabled"):
        await _service(db).provision_from_defaults(
            email="developer@example.com",
            settings=settings,
        )

    assert db.organizations == {}
    assert db.accounts == {}
