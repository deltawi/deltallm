from __future__ import annotations

from src.auth.sso_group_settings import SSOGroupSyncSettings
from src.db.identity.sso_group_sync import AccountAccess, OrganizationTarget, TeamTarget
from src.services.identity.sso_group_access import diff_group_access, plan_group_access


def _settings(*, remove: bool = False, **extra: object) -> SSOGroupSyncSettings:
    return SSOGroupSyncSettings.model_validate(
        {
            "enabled": True,
            "claim": "deltallm_role",
            "admin_values": ["admin"],
            "remove_unlisted": remove,
            "mappings": [
                {"value": "team-ml", "team_id": "ml", "team_role": "team_developer"},
                {"value": "ml-leads", "team_id": "ml", "team_role": "team_admin"},
                {"value": "support", "team_id": "support", "team_role": "team_viewer"},
                {"value": "finance", "organization_id": "acme", "org_role": "org_billing"},
            ],
            **extra,
        }
    )


TEAMS = {
    "ml": TeamTarget("ml", "ML", "acme", True, False),
    "support": TeamTarget("support", "Support", "acme", True, False),
    "manual": TeamTarget("manual", "Manual", "acme", True, False),
}
ORGS = {"acme": OrganizationTarget("acme", "Acme", True)}


def _actions(diff) -> set[tuple[str, str, str | None, str | None]]:  # noqa: ANN001
    return {(c.kind, c.action, c.target_id, c.role_after) for c in diff.changes}


def test_plan_keeps_highest_role_and_matched_values() -> None:
    plan = plan_group_access(_settings(), ["team-ml", "ml-leads", "unknown", "admin"])
    assert plan.grants_platform_admin is True
    assert plan.team_roles == {"ml": "team_admin"}
    assert plan.team_reasons == {"ml": "ml-leads"}
    assert plan.matched_values == ("admin", "team-ml", "ml-leads")
    assert plan_group_access(_settings(), ["unknown"]).has_grants is False


def test_add_only_grants_and_never_lowers_or_removes() -> None:
    plan = plan_group_access(_settings(), ["team-ml", "admin"])
    current = AccountAccess(
        "acct", "org_user", org_roles={}, team_roles={"ml": "team_admin", "support": "team_viewer"}
    )
    diff = diff_group_access(plan, current, TEAMS, ORGS, last_active_admin=False)
    assert _actions(diff) == {
        ("platform_role", "update", None, "platform_admin"),
        ("organization", "add", "acme", "org_member"),
    }


def test_remove_unlisted_makes_mapped_scopes_exact() -> None:
    plan = plan_group_access(_settings(remove=True), ["team-ml"])
    current = AccountAccess(
        "acct",
        "platform_admin",
        org_roles={"acme": "org_admin"},
        team_roles={"ml": "team_admin", "support": "team_viewer", "manual": "team_admin"},
    )
    diff = diff_group_access(plan, current, TEAMS, ORGS, last_active_admin=False)
    assert _actions(diff) == {
        ("platform_role", "update", None, "org_user"),
        ("team", "update", "ml", "team_developer"),
        ("team", "remove", "support", None),
        # acme is mapped, and team-ml only implies membership.
        ("organization", "update", "acme", "org_member"),
    }
    assert diff.demotes_platform_admin is True


def test_last_active_admin_is_kept() -> None:
    plan = plan_group_access(_settings(remove=True), [])
    diff = diff_group_access(
        plan, AccountAccess("acct", "platform_admin"), TEAMS, ORGS, last_active_admin=True
    )
    assert _actions(diff) == {("platform_role", "kept", None, "platform_admin")}
    assert diff.demotes_platform_admin is False


def test_mapped_org_removal_covers_its_teams_once() -> None:
    plan = plan_group_access(_settings(remove=True), [])
    current = AccountAccess(
        "acct", "org_user", org_roles={"acme": "org_billing"}, team_roles={"support": "team_viewer"}
    )
    diff = diff_group_access(plan, current, TEAMS, ORGS, last_active_admin=False)
    # Removing the org membership also removes its team memberships in SQL.
    assert _actions(diff) == {("organization", "remove", "acme", None)}


def test_unavailable_targets_are_skipped_not_granted_or_removed() -> None:
    teams = {
        "ml": TeamTarget("ml", "ML", "acme", True, True),
        "support": TeamTarget("support", "Support", "acme", False, False),
    }
    plan = plan_group_access(_settings(remove=True), ["team-ml", "support", "finance"])
    current = AccountAccess("acct", "org_user", team_roles={"support": "team_admin"})
    diff = diff_group_access(plan, current, teams, {}, last_active_admin=False)
    assert diff.changes == ()
    assert {(s.target_id, s.reason) for s in diff.skipped} == {
        ("ml", "external_managed"),
        ("support", "inactive_organization"),
        ("acme", "not_found"),
    }
