"""Pure SSO group access rules: claim values to a plan, and a plan to changes.

The login transaction (`sso_group_sync.py`) and the admin preview both use these
functions, so a preview shows exactly what the next sign-in does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from src.auth.roles import OrganizationRole, PlatformRole, TeamRole
from src.auth.sso_group_settings import SSOGroupSyncSettings
from src.db.identity.sso_group_sync import AccountAccess, OrganizationTarget, TeamTarget

TEAM_ROLE_RANK: dict[str, int] = {
    TeamRole.VIEWER: 1,
    TeamRole.DEVELOPER: 2,
    TeamRole.ADMIN: 3,
}
ORG_ROLE_RANK: dict[str, int] = {
    OrganizationRole.MEMBER: 1,
    OrganizationRole.BILLING: 2,
    OrganizationRole.AUDITOR: 3,
    OrganizationRole.ADMIN: 4,
    OrganizationRole.OWNER: 5,
}

ChangeKind = Literal["platform_role", "organization", "team"]
ChangeAction = Literal["add", "update", "remove", "kept"]


@dataclass(frozen=True, slots=True)
class SSOGroupAccessPlan:
    claim_values: tuple[str, ...]
    matched_values: tuple[str, ...]
    grants_platform_admin: bool
    team_roles: Mapping[str, str]
    org_roles: Mapping[str, str]
    team_reasons: Mapping[str, str]
    org_reasons: Mapping[str, str]
    remove_unlisted: bool
    managed_team_ids: frozenset[str]
    managed_org_ids: frozenset[str]

    @property
    def has_grants(self) -> bool:
        return self.grants_platform_admin or bool(self.team_roles) or bool(self.org_roles)


def _higher(rank: Mapping[str, int], current: str | None, candidate: str) -> bool:
    return current is None or rank.get(candidate, 0) > rank.get(current, 0)


def plan_group_access(
    settings: SSOGroupSyncSettings, claim_values: Sequence[str]
) -> SSOGroupAccessPlan:
    values = tuple(claim_values)
    present = set(values)
    matched: list[str] = []
    team_roles: dict[str, str] = {}
    org_roles: dict[str, str] = {}
    team_reasons: dict[str, str] = {}
    org_reasons: dict[str, str] = {}
    grants_admin = False
    for value in settings.admin_values:
        if value in present:
            grants_admin = True
            matched.append(value)
    for mapping in settings.mappings:
        if mapping.value not in present:
            continue
        if mapping.value not in matched:
            matched.append(mapping.value)
        if mapping.team_id and mapping.team_role:
            if _higher(TEAM_ROLE_RANK, team_roles.get(mapping.team_id), mapping.team_role):
                team_roles[mapping.team_id] = mapping.team_role
                team_reasons[mapping.team_id] = mapping.value
        elif mapping.organization_id and mapping.org_role:
            current = org_roles.get(mapping.organization_id)
            if _higher(ORG_ROLE_RANK, current, mapping.org_role):
                org_roles[mapping.organization_id] = mapping.org_role
                org_reasons[mapping.organization_id] = mapping.value
    return SSOGroupAccessPlan(
        claim_values=values,
        matched_values=tuple(matched),
        grants_platform_admin=grants_admin,
        team_roles=team_roles,
        org_roles=org_roles,
        team_reasons=team_reasons,
        org_reasons=org_reasons,
        remove_unlisted=settings.remove_unlisted,
        managed_team_ids=settings.mapped_team_ids,
        managed_org_ids=settings.mapped_organization_ids,
    )


@dataclass(frozen=True, slots=True)
class SSOGroupAccessChange:
    kind: ChangeKind
    action: ChangeAction
    target_id: str | None
    role_before: str | None
    role_after: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SSOGroupSkippedTarget:
    target_id: str
    kind: Literal["organization", "team"]
    reason: Literal["not_found", "inactive_organization", "external_managed", "credential_in_use"]


@dataclass(frozen=True, slots=True)
class SSOGroupAccessDiff:
    changes: tuple[SSOGroupAccessChange, ...]
    skipped: tuple[SSOGroupSkippedTarget, ...]

    @property
    def demotes_platform_admin(self) -> bool:
        return any(
            item.kind == "platform_role"
            and item.action == "update"
            and item.role_after != PlatformRole.ADMIN
            for item in self.changes
        )


def _team_skip(target: TeamTarget | None, team_id: str) -> SSOGroupSkippedTarget | None:
    if target is None:
        return SSOGroupSkippedTarget(team_id, "team", "not_found")
    if target.external_managed:
        return SSOGroupSkippedTarget(team_id, "team", "external_managed")
    if not target.organization_active:
        return SSOGroupSkippedTarget(team_id, "team", "inactive_organization")
    return None


def _org_skip(target: OrganizationTarget | None, org_id: str) -> SSOGroupSkippedTarget | None:
    if target is None:
        return SSOGroupSkippedTarget(org_id, "organization", "not_found")
    if not target.active:
        return SSOGroupSkippedTarget(org_id, "organization", "inactive_organization")
    return None


def _role_change(
    kind: ChangeKind,
    target_id: str,
    current: str | None,
    desired: str,
    *,
    exact: bool,
    rank: Mapping[str, int],
    reason: str | None,
) -> SSOGroupAccessChange | None:
    if current is None:
        return SSOGroupAccessChange(kind, "add", target_id, None, desired, reason)
    if current == desired:
        return None
    if exact or rank.get(desired, 0) > rank.get(current, 0):
        return SSOGroupAccessChange(kind, "update", target_id, current, desired, reason)
    return None


def _platform_change(
    plan: SSOGroupAccessPlan, current: AccountAccess, *, last_active_admin: bool
) -> SSOGroupAccessChange | None:
    if plan.grants_platform_admin and current.role != PlatformRole.ADMIN:
        reason = next(iter(plan.matched_values), None)
        return SSOGroupAccessChange(
            "platform_role", "update", None, current.role, PlatformRole.ADMIN, reason
        )
    if (
        plan.remove_unlisted
        and not plan.grants_platform_admin
        and current.role == PlatformRole.ADMIN
    ):
        if last_active_admin:
            return SSOGroupAccessChange(
                "platform_role", "kept", None, current.role, current.role, "last_platform_admin"
            )
        return SSOGroupAccessChange(
            "platform_role", "update", None, current.role, PlatformRole.ORG_USER, None
        )
    return None


def diff_group_access(
    plan: SSOGroupAccessPlan,
    current: AccountAccess,
    teams: Mapping[str, TeamTarget],
    organizations: Mapping[str, OrganizationTarget],
    *,
    last_active_admin: bool,
) -> SSOGroupAccessDiff:
    """Compare current access with the plan. `remove_unlisted` makes mapped scopes exact."""

    changes: list[SSOGroupAccessChange] = []
    skipped: list[SSOGroupSkippedTarget] = []
    exact = plan.remove_unlisted
    platform = _platform_change(plan, current, last_active_admin=last_active_admin)
    if platform is not None:
        changes.append(platform)

    kept_teams: set[str] = set()
    implied_orgs: set[str] = set()
    for team_id, role in plan.team_roles.items():
        target = teams.get(team_id)
        skip = _team_skip(target, team_id)
        if skip is not None:
            skipped.append(skip)
            continue
        assert target is not None
        kept_teams.add(team_id)
        if target.organization_id:
            implied_orgs.add(target.organization_id)
        change = _role_change(
            "team",
            team_id,
            current.team_roles.get(team_id),
            role,
            exact=exact,
            rank=TEAM_ROLE_RANK,
            reason=plan.team_reasons.get(team_id),
        )
        if change is not None:
            changes.append(change)

    kept_orgs: set[str] = set(implied_orgs)
    for org_id, role in plan.org_roles.items():
        skip = _org_skip(organizations.get(org_id), org_id)
        if skip is not None:
            skipped.append(skip)
            continue
        kept_orgs.add(org_id)
        change = _role_change(
            "organization",
            org_id,
            current.org_roles.get(org_id),
            role,
            exact=exact,
            rank=ORG_ROLE_RANK,
            reason=plan.org_reasons.get(org_id),
        )
        if change is not None:
            changes.append(change)
    for org_id in sorted(implied_orgs - set(plan.org_roles)):
        # A team grant needs organization membership; it never raises the org role.
        # In a mapped organization with removal on, a higher role falls back to member.
        role_now = current.org_roles.get(org_id)
        if role_now is None:
            changes.append(
                SSOGroupAccessChange(
                    "organization", "add", org_id, None, OrganizationRole.MEMBER, None
                )
            )
        elif exact and org_id in plan.managed_org_ids and role_now != OrganizationRole.MEMBER:
            changes.append(
                SSOGroupAccessChange(
                    "organization", "update", org_id, role_now, OrganizationRole.MEMBER, None
                )
            )

    if exact:
        changes.extend(_removals(plan, current, teams, organizations, kept_teams, kept_orgs))
    return SSOGroupAccessDiff(changes=tuple(changes), skipped=tuple(skipped))


def _removals(
    plan: SSOGroupAccessPlan,
    current: AccountAccess,
    teams: Mapping[str, TeamTarget],
    organizations: Mapping[str, OrganizationTarget],
    kept_teams: set[str],
    kept_orgs: set[str],
) -> list[SSOGroupAccessChange]:
    removed_orgs = [
        org_id
        for org_id in sorted(plan.managed_org_ids)
        if org_id in current.org_roles
        and org_id not in kept_orgs
        and _org_skip(organizations.get(org_id), org_id) is None
    ]
    changes = [
        SSOGroupAccessChange("organization", "remove", org_id, current.org_roles[org_id], None)
        for org_id in removed_orgs
    ]
    for team_id in sorted(current.team_roles):
        target = teams.get(team_id)
        # Organization removal also removes the account's teams there, mapped or not.
        cascaded = target is not None and target.organization_id in removed_orgs
        mapped = team_id in plan.managed_team_ids and _team_skip(target, team_id) is None
        if team_id not in kept_teams and (cascaded or mapped):
            changes.append(
                SSOGroupAccessChange("team", "remove", team_id, current.team_roles[team_id], None)
            )
    return changes
