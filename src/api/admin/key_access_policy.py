from __future__ import annotations

from typing import Literal
from fastapi import HTTPException, status
from src.api.admin.auth_scope import AuthScope
from src.auth.roles import Permission
from src.db.identity.key_list_scope import (
    _append_in_predicate as _append_in_predicate,
    _append_key_list_scope_clause as _append_key_list_scope_clause,
    _ordered_unique_scope_ids as _ordered_unique_scope_ids,
    _scope_ids_with_any_permission as _scope_ids_with_any_permission,
)

_ADMIN_KEY_LIST_PERMISSIONS = frozenset({Permission.KEY_UPDATE, Permission.KEY_REVOKE})


def _is_self_service_only(scope: AuthScope) -> bool:
    if scope.external_workspace is not None:
        return True
    if scope.is_platform_admin:
        return False
    effective_permissions = set(getattr(scope, "effective_permissions", set()) or set())
    return (
        Permission.KEY_CREATE_SELF in effective_permissions
        and Permission.KEY_UPDATE not in effective_permissions
        and Permission.KEY_REVOKE not in effective_permissions
    )


def _scope_has_permission(
    scope: AuthScope,
    *,
    organization_id: str | None,
    team_id: str | None,
    permission: str,
) -> bool:
    if scope.is_platform_admin:
        return True

    team_permissions = getattr(scope, "team_permissions_by_id", {}) or {}
    if team_id and permission in set(team_permissions.get(team_id) or set()):
        return True

    org_permissions = getattr(scope, "org_permissions_by_id", {}) or {}
    if organization_id and permission in set(org_permissions.get(organization_id) or set()):
        return True

    return False


def _target_scope_permission_sets(
    scope: AuthScope,
    *,
    organization_id: str | None,
    team_id: str | None,
) -> list[set[str]]:
    permission_sets: list[set[str]] = []
    team_permissions = getattr(scope, "team_permissions_by_id", {}) or {}
    if team_id:
        permission_sets.append(set(team_permissions.get(team_id) or set()))

    org_permissions = getattr(scope, "org_permissions_by_id", {}) or {}
    if organization_id:
        permission_sets.append(set(org_permissions.get(organization_id) or set()))
    return permission_sets


def _resolve_key_read_access_mode(
    scope: AuthScope,
    *,
    organization_id: str | None,
    team_id: str | None,
) -> Literal["admin", "self_service"]:
    if scope.is_platform_admin:
        return "admin"

    if scope.external_workspace is not None:
        if _scope_has_permission(
            scope, organization_id=organization_id, team_id=team_id, permission=Permission.KEY_READ
        ):
            return "self_service"
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    owner_only_read = False
    for permissions in _target_scope_permission_sets(
        scope, organization_id=organization_id, team_id=team_id
    ):
        if permissions.intersection(_ADMIN_KEY_LIST_PERMISSIONS):
            return "admin"
        if Permission.KEY_READ in permissions and Permission.KEY_CREATE_SELF not in permissions:
            return "admin"
        if Permission.KEY_READ in permissions and Permission.KEY_CREATE_SELF in permissions:
            owner_only_read = True

    if owner_only_read:
        return "self_service"
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


def _resolve_key_access_mode(
    scope: AuthScope,
    *,
    organization_id: str | None,
    team_id: str | None,
    admin_permission: str,
    allow_self_service: bool = False,
) -> Literal["admin", "self_service"]:
    if scope.external_workspace is not None:
        permitted = admin_permission in {
            Permission.KEY_READ,
            Permission.KEY_REVOKE,
        } and _scope_has_permission(
            scope, organization_id=organization_id, team_id=team_id, permission=admin_permission
        )
        permitted = permitted or (
            allow_self_service
            and _scope_has_permission(
                scope,
                organization_id=organization_id,
                team_id=team_id,
                permission=Permission.KEY_CREATE_SELF,
            )
        )
        if permitted:
            return "self_service"
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if _scope_has_permission(
        scope,
        organization_id=organization_id,
        team_id=team_id,
        permission=admin_permission,
    ):
        return "admin"

    if allow_self_service and _scope_has_permission(
        scope,
        organization_id=organization_id,
        team_id=team_id,
        permission=Permission.KEY_CREATE_SELF,
    ):
        return "self_service"

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
