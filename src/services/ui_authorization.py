from __future__ import annotations

from typing import Any, Iterable

from src.auth.roles import ORG_ROLE_PERMISSIONS, PLATFORM_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS, Permission


def effective_permissions_for_context(context: Any | None) -> list[str]:
    if context is None:
        return []

    permissions: set[str] = set()
    role = str(getattr(context, "role", "") or "")
    permissions.update(PLATFORM_ROLE_PERMISSIONS.get(role, set()))

    for membership in list(getattr(context, "organization_memberships", []) or []):
        org_role = str(membership.get("role") or "")
        permissions.update(ORG_ROLE_PERMISSIONS.get(org_role, set()))

    for membership in list(getattr(context, "team_memberships", []) or []):
        team_role = str(membership.get("role") or "")
        permissions.update(TEAM_ROLE_PERMISSIONS.get(team_role, set()))

    return sorted(permissions)


def build_ui_access(
    *,
    authenticated: bool,
    effective_permissions: Iterable[str],
    organization_memberships: Iterable[dict[str, Any]] | None = None,
) -> dict[str, bool]:
    permissions = set(effective_permissions)
    is_platform_admin = Permission.PLATFORM_ADMIN in permissions
    can_read_keys = (
        Permission.KEY_READ in permissions
        or Permission.KEY_UPDATE in permissions
        or Permission.KEY_CREATE_SELF in permissions
    )
    can_view_dashboard = authenticated and (is_platform_admin or Permission.SPEND_READ in permissions)
    can_create_team = is_platform_admin
    if not can_create_team:
        for membership in list(organization_memberships or []):
            org_role = str(membership.get("role") or "")
            if Permission.TEAM_UPDATE in ORG_ROLE_PERMISSIONS.get(org_role, set()):
                can_create_team = True
                break

    return {
        "dashboard": can_view_dashboard,
        "models": authenticated,
        "model_admin": is_platform_admin,
        "route_groups": is_platform_admin,
        "prompts": is_platform_admin,
        "mcp_servers": authenticated and (is_platform_admin or Permission.KEY_READ in permissions),
        "mcp_approvals": authenticated and (is_platform_admin or Permission.KEY_UPDATE in permissions),
        "keys": authenticated and (is_platform_admin or can_read_keys),
        "organizations": authenticated and (is_platform_admin or Permission.ORG_READ in permissions),
        "organization_create": is_platform_admin,
        "teams": authenticated and (is_platform_admin or Permission.TEAM_READ in permissions),
        "team_create": authenticated and can_create_team,
        "people_access": is_platform_admin,
        "usage": authenticated and (is_platform_admin or Permission.SPEND_READ in permissions),
        "audit": authenticated and (is_platform_admin or Permission.AUDIT_READ in permissions),
        "batches": authenticated and (is_platform_admin or Permission.KEY_READ in permissions),
        "guardrails": is_platform_admin,
        "playground": authenticated,
        "settings": is_platform_admin,
    }


def _scope_has_org_permission(scope: Any, organization_id: str | None, permission: str) -> bool:
    if getattr(scope, "is_platform_admin", False):
        return True
    normalized_org_id = str(organization_id or "").strip()
    if not normalized_org_id:
        return False
    permissions_by_org = getattr(scope, "org_permissions_by_id", {}) or {}
    return permission in permissions_by_org.get(normalized_org_id, set())


def _scope_has_team_permission(scope: Any, team_id: str | None, permission: str) -> bool:
    if getattr(scope, "is_platform_admin", False):
        return True
    normalized_team_id = str(team_id or "").strip()
    if not normalized_team_id:
        return False
    permissions_by_team = getattr(scope, "team_permissions_by_id", {}) or {}
    return permission in permissions_by_team.get(normalized_team_id, set())


def build_organization_capabilities(scope: Any, organization: dict[str, Any]) -> dict[str, bool]:
    organization_id = str(organization.get("organization_id") or "").strip()
    can_edit = _scope_has_org_permission(scope, organization_id, Permission.ORG_UPDATE)
    can_add_team = _scope_has_org_permission(scope, organization_id, Permission.TEAM_UPDATE)
    can_manage_assets = bool(getattr(scope, "is_platform_admin", False))

    return {
        "view": True,
        "edit": can_edit,
        "add_team": can_add_team,
        "manage_members": can_edit,
        "manage_assets": can_manage_assets,
        "view_usage": _scope_has_org_permission(scope, organization_id, Permission.SPEND_READ),
    }


def build_team_capabilities(scope: Any, team: dict[str, Any]) -> dict[str, bool]:
    team_id = str(team.get("team_id") or "").strip()
    organization_id = str(team.get("organization_id") or "").strip()
    can_edit = (
        _scope_has_team_permission(scope, team_id, Permission.TEAM_UPDATE)
        or _scope_has_org_permission(scope, organization_id, Permission.TEAM_UPDATE)
    )

    return {
        "view": True,
        "edit": can_edit,
        "delete": can_edit,
        "manage_members": can_edit,
        "manage_assets": can_edit,
        "manage_self_service_policy": can_edit,
        "create_self_key": bool(team.get("self_service_keys_enabled")) and _scope_has_team_permission(
            scope,
            team_id,
            Permission.KEY_CREATE_SELF,
        ),
    }


def build_batch_capabilities(scope: Any, batch: dict[str, Any]) -> dict[str, bool]:
    team_id = str(batch.get("created_by_team_id") or "").strip()
    organization_id = str(batch.get("organization_id") or "").strip()
    can_cancel = (
        _scope_has_team_permission(scope, team_id, Permission.KEY_UPDATE)
        or _scope_has_org_permission(scope, organization_id, Permission.KEY_UPDATE)
    )

    return {
        "view": True,
        "cancel": can_cancel,
    }
