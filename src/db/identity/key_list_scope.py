from __future__ import annotations

from typing import Protocol

from src.auth.roles import Permission
from src.models.external_auth import ExternalWorkspaceContext


class KeyListScope(Protocol):
    is_platform_admin: bool
    account_id: str | None
    external_workspace: ExternalWorkspaceContext | None
    org_permissions_by_id: dict[str, set[str]]
    team_permissions_by_id: dict[str, set[str]]
    effective_permissions: set[str]


_ADMIN_KEY_LIST_PERMISSIONS = frozenset({Permission.KEY_UPDATE, Permission.KEY_REVOKE})
_READ_KEY_LIST_PERMISSIONS = frozenset({Permission.KEY_READ})
_OWNER_KEY_LIST_PERMISSIONS = frozenset({Permission.KEY_CREATE_SELF})


def _scope_ids_with_any_permission(
    permissions_by_id: dict[str, set[str]],
    required_permissions: frozenset[str],
) -> list[str]:
    scope_ids: list[str] = []
    for scope_id, permissions in permissions_by_id.items():
        normalized_scope_id = str(scope_id or "").strip()
        if normalized_scope_id and required_permissions.intersection(set(permissions or set())):
            scope_ids.append(normalized_scope_id)
    return scope_ids


def _append_in_predicate(column: str, values: list[str], params: list[object]) -> str | None:
    normalized_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not normalized_values:
        return None
    start = len(params) + 1
    params.extend(normalized_values)
    placeholders = ", ".join(f"${idx}" for idx in range(start, start + len(normalized_values)))
    return f"{column} IN ({placeholders})"


def _ordered_unique_scope_ids(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for scope_id in group:
            if scope_id not in seen:
                seen.add(scope_id)
                ordered.append(scope_id)
    return ordered


def _append_key_list_scope_clause(
    scope: KeyListScope, clauses: list[str], params: list[object], *, my_keys: bool
) -> bool:
    if scope.external_workspace is not None:
        return _append_external_key_list(scope, clauses, params)
    owner_account_id = str(getattr(scope, "account_id", "") or "").strip() or None
    owner_param_index: int | None = None
    if my_keys:
        if owner_account_id is None:
            return False
        params.append(owner_account_id)
        owner_param_index = len(params)
        clauses.append(f"vt.owner_account_id = ${owner_param_index}")

    if scope.is_platform_admin:
        return True

    org_permissions = getattr(scope, "org_permissions_by_id", {}) or {}
    team_permissions = getattr(scope, "team_permissions_by_id", {}) or {}

    admin_org_ids = _scope_ids_with_any_permission(org_permissions, _ADMIN_KEY_LIST_PERMISSIONS)
    admin_team_ids = _scope_ids_with_any_permission(team_permissions, _ADMIN_KEY_LIST_PERMISSIONS)
    admin_org_id_set = set(admin_org_ids)
    admin_team_id_set = set(admin_team_ids)
    owner_org_ids = [
        scope_id
        for scope_id in _scope_ids_with_any_permission(org_permissions, _OWNER_KEY_LIST_PERMISSIONS)
        if scope_id not in admin_org_id_set
    ]
    owner_team_ids = [
        scope_id
        for scope_id in _scope_ids_with_any_permission(
            team_permissions, _OWNER_KEY_LIST_PERMISSIONS
        )
        if scope_id not in admin_team_id_set
    ]
    owner_org_id_set = set(owner_org_ids)
    owner_team_id_set = set(owner_team_ids)
    read_org_ids = [
        scope_id
        for scope_id in _scope_ids_with_any_permission(org_permissions, _READ_KEY_LIST_PERMISSIONS)
        if scope_id not in owner_org_id_set
    ]
    read_team_ids = [
        scope_id
        for scope_id in _scope_ids_with_any_permission(team_permissions, _READ_KEY_LIST_PERMISSIONS)
        if scope_id not in owner_team_id_set
    ]
    full_org_ids = _ordered_unique_scope_ids(admin_org_ids, read_org_ids)
    full_team_ids = _ordered_unique_scope_ids(admin_team_ids, read_team_ids)

    scope_parts: list[str] = []
    full_org_predicate = _append_in_predicate("t.organization_id", full_org_ids, params)
    if full_org_predicate is not None:
        scope_parts.append(full_org_predicate)
    full_team_predicate = _append_in_predicate("vt.team_id", full_team_ids, params)
    if full_team_predicate is not None:
        scope_parts.append(full_team_predicate)

    if owner_account_id is not None and (owner_org_ids or owner_team_ids):
        if owner_param_index is None:
            params.append(owner_account_id)
            owner_param_index = len(params)
        owner_org_predicate = _append_in_predicate("t.organization_id", owner_org_ids, params)
        if owner_org_predicate is not None:
            scope_parts.append(
                f"({owner_org_predicate} AND vt.owner_account_id = ${owner_param_index})"
            )
        owner_team_predicate = _append_in_predicate("vt.team_id", owner_team_ids, params)
        if owner_team_predicate is not None:
            scope_parts.append(
                f"({owner_team_predicate} AND vt.owner_account_id = ${owner_param_index})"
            )

    if not scope_parts:
        return False
    clauses.append(f"({' OR '.join(scope_parts)})")
    return True


def _append_external_key_list(
    scope: KeyListScope, clauses: list[str], params: list[object]
) -> bool:
    workspace = scope.external_workspace
    if (
        workspace is None
        or scope.account_id is None
        or not scope.effective_permissions & {Permission.KEY_READ, Permission.KEY_CREATE_SELF}
    ):
        return False
    for column, value in (
        ("vt.owner_account_id", scope.account_id),
        ("vt.user_id", workspace.inference_user_id),
        ("vt.team_id", workspace.team_id),
        ("t.organization_id", workspace.organization_id),
    ):
        params.append(value)
        clauses.append(f"{column} = ${len(params)}")
    clauses.append("vt.owner_service_account_id IS NULL")
    return True
