"""Apply an SSO group access plan to one account inside the login transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.auth.roles import PlatformRole
from src.db.identity.platform_accounts import PlatformAccountDatabase
from src.db.identity.sso_group_sync import (
    AccountAccess,
    OrganizationTarget,
    TeamTarget,
    is_last_active_platform_admin,
    load_account_access,
    load_organization_targets,
    load_team_targets,
    remove_organization_memberships,
    remove_team_memberships,
    set_platform_role,
    upsert_organization_membership,
    upsert_team_membership,
)
from src.services.identity.sso_group_access import (
    SSOGroupAccessDiff,
    SSOGroupAccessPlan,
    diff_group_access,
)


@dataclass(frozen=True, slots=True)
class SSOGroupAccessView:
    current: AccountAccess
    teams: dict[str, TeamTarget]
    organizations: dict[str, OrganizationTarget]
    diff: SSOGroupAccessDiff


@dataclass(frozen=True, slots=True)
class SSOGroupSyncResult:
    diff: SSOGroupAccessDiff
    revoked_key_hashes: tuple[str, ...]

    def audit_summary(self) -> dict[str, Any]:
        return {
            "changes": [
                {
                    "kind": item.kind,
                    "action": item.action,
                    "target_id": item.target_id,
                    "role_before": item.role_before,
                    "role_after": item.role_after,
                    "reason": item.reason,
                }
                for item in self.diff.changes
            ],
            "skipped": [
                {"kind": item.kind, "target_id": item.target_id, "reason": item.reason}
                for item in self.diff.skipped
            ],
            "revoked_keys": len(self.revoked_key_hashes),
        }


class SSOGroupSyncService:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def _view(
        self, plan: SSOGroupAccessPlan, current: AccountAccess, *, lock: bool
    ) -> SSOGroupAccessView:
        teams = await load_team_targets(
            self.db, set(plan.team_roles) | plan.managed_team_ids, lock=lock
        )
        organizations = await load_organization_targets(
            self.db, set(plan.org_roles) | plan.managed_org_ids, lock=lock
        )
        demotion_possible = (
            plan.remove_unlisted
            and not plan.grants_platform_admin
            and current.role == PlatformRole.ADMIN
        )
        last_admin = False
        if demotion_possible and current.account_id:
            last_admin = await is_last_active_platform_admin(self.db, current.account_id, lock=lock)
        diff = diff_group_access(plan, current, teams, organizations, last_active_admin=last_admin)
        return SSOGroupAccessView(current, teams, organizations, diff)

    async def preview(self, plan: SSOGroupAccessPlan, account_id: str | None) -> SSOGroupAccessView:
        """Read-only diff for the admin test panel. `None` previews a new account."""
        current = await load_account_access(self.db, account_id) if account_id else None
        if current is None:
            current = AccountAccess(account_id="", role=PlatformRole.ORG_USER)
        return await self._view(plan, current, lock=False)

    async def apply(self, plan: SSOGroupAccessPlan, account_id: str) -> SSOGroupSyncResult:
        current = await load_account_access(self.db, account_id)
        if current is None:
            raise RuntimeError("SSO account not found")
        view = await self._view(plan, current, lock=True)
        revoked: list[str] = []
        removed_orgs: list[str] = []
        removed_teams: list[str] = []
        for change in view.diff.changes:
            if change.action not in ("add", "update"):
                if change.action == "remove" and change.target_id:
                    target = removed_orgs if change.kind == "organization" else removed_teams
                    target.append(change.target_id)
                continue
            role = str(change.role_after)
            if change.kind == "platform_role":
                await set_platform_role(self.db, account_id, role)
            elif change.kind == "organization" and change.target_id:
                await upsert_organization_membership(
                    self.db, account_id=account_id, organization_id=change.target_id, role=role
                )
            elif change.kind == "team" and change.target_id:
                await upsert_team_membership(
                    self.db, account_id=account_id, team_id=change.target_id, role=role
                )
        revoked += await remove_organization_memberships(
            self.db, account_id=account_id, organization_ids=removed_orgs
        )
        revoked += await remove_team_memberships(
            self.db, account_id=account_id, team_ids=removed_teams
        )
        return SSOGroupSyncResult(diff=view.diff, revoked_key_hashes=tuple(revoked))
