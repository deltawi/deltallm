"""Raw-SQL visibility clause builders for the admin MCP endpoints.

These helpers mutate the ``params`` list as they append placeholders so the
caller ends up with the full bind list in the original definition order.
"""
from __future__ import annotations

from typing import Any

from src.api.admin.endpoints.common import AuthScope


def _append_param_list(params: list[Any], values: list[str]) -> str:
    start = len(params) + 1
    params.extend(values)
    return ", ".join(f"${start + index}" for index in range(len(values)))


def _scoped_entity_visibility_clause(alias: str, scope: AuthScope, params: list[Any]) -> str:
    clauses: list[str] = []
    if scope.org_ids:
        org_placeholders = _append_param_list(params, scope.org_ids)
        clauses.append(f"({alias}.scope_type = 'organization' AND {alias}.scope_id IN ({org_placeholders}))")
        clauses.append(
            f"""({alias}.scope_type = 'team' AND EXISTS (
                    SELECT 1 FROM deltallm_teamtable t
                    WHERE t.team_id = {alias}.scope_id
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
        clauses.append(
            f"""({alias}.scope_type = 'api_key' AND EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken vt
                    LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
                    WHERE vt.token = {alias}.scope_id
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
        clauses.append(
            f"""({alias}.scope_type = 'user' AND EXISTS (
                    SELECT 1 FROM deltallm_usertable u
                    LEFT JOIN deltallm_teamtable t ON u.team_id = t.team_id
                    WHERE u.user_id = {alias}.scope_id
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
    if scope.team_ids:
        team_placeholders = _append_param_list(params, scope.team_ids)
        clauses.append(f"({alias}.scope_type = 'team' AND {alias}.scope_id IN ({team_placeholders}))")
        clauses.append(
            f"""({alias}.scope_type = 'api_key' AND EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken vt
                    WHERE vt.token = {alias}.scope_id
                      AND vt.team_id IN ({team_placeholders})
                ))"""
        )
        clauses.append(
            f"""({alias}.scope_type = 'user' AND EXISTS (
                    SELECT 1 FROM deltallm_usertable u
                    WHERE u.user_id = {alias}.scope_id
                      AND u.team_id IN ({team_placeholders})
                ))"""
        )
    return " OR ".join(clauses) if clauses else "FALSE"


def _server_owner_visibility_clause(server_alias: str, scope: AuthScope, params: list[Any]) -> str:
    clauses: list[str] = []
    if scope.org_ids:
        org_placeholders = _append_param_list(params, scope.org_ids)
        clauses.append(
            f"({server_alias}.owner_scope_type = 'organization' AND {server_alias}.owner_scope_id IN ({org_placeholders}))"
        )
    return " OR ".join(clauses) if clauses else "FALSE"


def _server_visibility_exists_clause(server_alias: str, scope: AuthScope, params: list[Any]) -> str:
    clauses: list[str] = []
    owner_clause = _server_owner_visibility_clause(server_alias, scope, params)
    if owner_clause != "FALSE":
        clauses.append(owner_clause)
    clauses.append(
        f"""EXISTS (
                SELECT 1
                FROM deltallm_mcpbinding b
                WHERE b.mcp_server_id = {server_alias}.mcp_server_id
                  AND ({_scoped_entity_visibility_clause('b', scope, params)})
            )"""
    )
    return " OR ".join(f"({clause})" for clause in clauses)


def _audit_scope_visibility_clause(scope: AuthScope, params: list[Any]) -> str:
    clauses: list[str] = []
    if scope.org_ids:
        org_placeholders = _append_param_list(params, scope.org_ids)
        clauses.append(f"(metadata->>'scope_type' = 'organization' AND metadata->>'scope_id' IN ({org_placeholders}))")
        clauses.append(
            f"""(metadata->>'scope_type' = 'team' AND EXISTS (
                    SELECT 1 FROM deltallm_teamtable t
                    WHERE t.team_id = metadata->>'scope_id'
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
        clauses.append(
            f"""(metadata->>'scope_type' = 'api_key' AND EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken vt
                    LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
                    WHERE vt.token = metadata->>'scope_id'
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
        clauses.append(
            f"""(metadata->>'scope_type' = 'user' AND EXISTS (
                    SELECT 1 FROM deltallm_usertable u
                    LEFT JOIN deltallm_teamtable t ON u.team_id = t.team_id
                    WHERE u.user_id = metadata->>'scope_id'
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
    if scope.team_ids:
        team_placeholders = _append_param_list(params, scope.team_ids)
        clauses.append(f"(metadata->>'scope_type' = 'team' AND metadata->>'scope_id' IN ({team_placeholders}))")
        clauses.append(
            f"""(metadata->>'scope_type' = 'api_key' AND EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken vt
                    WHERE vt.token = metadata->>'scope_id'
                      AND vt.team_id IN ({team_placeholders})
                ))"""
        )
        clauses.append(
            f"""(metadata->>'scope_type' = 'user' AND EXISTS (
                    SELECT 1 FROM deltallm_usertable u
                    WHERE u.user_id = metadata->>'scope_id'
                      AND u.team_id IN ({team_placeholders})
                ))"""
        )
    return " OR ".join(clauses) if clauses else "FALSE"


def _approval_visibility_clause(scope: AuthScope, params: list[Any]) -> str:
    return _scoped_entity_visibility_clause("r", scope, params)


__all__ = [
    "_append_param_list",
    "_scoped_entity_visibility_clause",
    "_server_owner_visibility_clause",
    "_server_visibility_exists_clause",
    "_audit_scope_visibility_clause",
    "_approval_visibility_clause",
]
