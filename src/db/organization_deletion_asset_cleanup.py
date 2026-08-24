from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.db.organization_deletion_cleanup_types import CleanupPageResult


DeleteOperation = Callable[[str, int], Awaitable[int]]


class OrganizationDeletionAssetCleanup:
    """Deletes asset children before parents so each page has a strict row bound."""

    def __init__(self, prisma_client: Any) -> None:
        self.prisma = prisma_client

    async def delete_owned_assets_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        operations: tuple[DeleteOperation, ...] = (
            self._delete_mcp_approvals,
            self._delete_mcp_bindings,
            self._delete_mcp_tool_policies,
            self._delete_mcp_servers,
            self._delete_prompt_bindings,
            self._delete_prompt_labels,
            self._delete_prompt_versions,
            self._delete_prompt_templates,
            self._delete_route_group_bindings,
            self._delete_route_callable_bindings,
            self._delete_route_policies,
            self._delete_route_members,
            self._delete_route_groups,
        )
        processed = 0
        for operation in operations:
            remaining_budget = page_size - processed
            if remaining_budget <= 0:
                break
            processed += await operation(organization_id, remaining_budget)
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def has_owned_assets(self, organization_id: str) -> bool:
        rows = await self.prisma.query_raw(
            """
            SELECT (
                EXISTS (
                    SELECT 1 FROM deltallm_mcpserver
                    WHERE owner_scope_type = 'organization' AND owner_scope_id = $1
                ) OR EXISTS (
                    SELECT 1 FROM deltallm_prompttemplate
                    WHERE owner_scope = 'organization'
                      AND metadata #>> '{_asset_governance,owner_scope_id}' = $1
                ) OR EXISTS (
                    SELECT 1 FROM deltallm_routegroup
                    WHERE metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
                      AND metadata #>> '{_asset_governance,owner_scope_id}' = $1
                )
            ) AS has_owned_assets
            """,
            organization_id,
        )
        return bool(rows and rows[0].get("has_owned_assets"))

    async def _delete_mcp_approvals(self, organization_id: str, limit: int) -> int:
        return await self._delete_ids(
            "deltallm_mcpapprovalrequest",
            "mcp_approval_request_id",
            """
            mcp_server_id IN (
                SELECT mcp_server_id FROM deltallm_mcpserver
                WHERE owner_scope_type = 'organization' AND owner_scope_id = $1
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_mcp_bindings(self, organization_id: str, limit: int) -> int:
        return await self._delete_ids(
            "deltallm_mcpbinding",
            "mcp_binding_id",
            """
            mcp_server_id IN (
                SELECT mcp_server_id FROM deltallm_mcpserver
                WHERE owner_scope_type = 'organization' AND owner_scope_id = $1
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_mcp_tool_policies(self, organization_id: str, limit: int) -> int:
        return await self._delete_ids(
            "deltallm_mcptoolpolicy",
            "mcp_tool_policy_id",
            """
            mcp_server_id IN (
                SELECT mcp_server_id FROM deltallm_mcpserver
                WHERE owner_scope_type = 'organization' AND owner_scope_id = $1
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_mcp_servers(self, organization_id: str, limit: int) -> int:
        return await self._delete_ids(
            "deltallm_mcpserver",
            "mcp_server_id",
            """
            owner_scope_type = 'organization' AND owner_scope_id = $1
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_mcpapprovalrequest child
                WHERE child.mcp_server_id = deltallm_mcpserver.mcp_server_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_mcpbinding child
                WHERE child.mcp_server_id = deltallm_mcpserver.mcp_server_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_mcptoolpolicy child
                WHERE child.mcp_server_id = deltallm_mcpserver.mcp_server_id
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_prompt_bindings(self, organization_id: str, limit: int) -> int:
        return await self._delete_prompt_children(
            "deltallm_promptbinding",
            "prompt_binding_id",
            organization_id,
            limit,
        )

    async def _delete_prompt_labels(self, organization_id: str, limit: int) -> int:
        return await self._delete_prompt_children(
            "deltallm_promptlabel",
            "prompt_label_id",
            organization_id,
            limit,
        )

    async def _delete_prompt_versions(self, organization_id: str, limit: int) -> int:
        return await self._delete_prompt_children(
            "deltallm_promptversion",
            "prompt_version_id",
            organization_id,
            limit,
        )

    async def _delete_prompt_templates(self, organization_id: str, limit: int) -> int:
        return await self._delete_ids(
            "deltallm_prompttemplate",
            "prompt_template_id",
            """
            owner_scope = 'organization'
            AND metadata #>> '{_asset_governance,owner_scope_id}' = $1
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_promptbinding child
                WHERE child.prompt_template_id = deltallm_prompttemplate.prompt_template_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_promptlabel child
                WHERE child.prompt_template_id = deltallm_prompttemplate.prompt_template_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_promptversion child
                WHERE child.prompt_template_id = deltallm_prompttemplate.prompt_template_id
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_route_group_bindings(self, organization_id: str, limit: int) -> int:
        return await self._delete_route_children(
            "deltallm_routegroupbinding",
            "route_group_binding_id",
            "route_group_id",
            organization_id,
            limit,
        )

    async def _delete_route_callable_bindings(
        self,
        organization_id: str,
        limit: int,
    ) -> int:
        return await self._delete_ids(
            "deltallm_callabletargetbinding",
            "callable_target_binding_id",
            """
            callable_key IN (
                SELECT group_key FROM deltallm_routegroup
                WHERE metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
                  AND metadata #>> '{_asset_governance,owner_scope_id}' = $1
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_route_policies(self, organization_id: str, limit: int) -> int:
        return await self._delete_route_children(
            "deltallm_routepolicy",
            "route_policy_id",
            "route_group_id",
            organization_id,
            limit,
        )

    async def _delete_route_members(self, organization_id: str, limit: int) -> int:
        return await self._delete_route_children(
            "deltallm_routegroupmember",
            "membership_id",
            "route_group_id",
            organization_id,
            limit,
        )

    async def _delete_route_groups(self, organization_id: str, limit: int) -> int:
        return await self._delete_ids(
            "deltallm_routegroup",
            "route_group_id",
            """
            metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
            AND metadata #>> '{_asset_governance,owner_scope_id}' = $1
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_routegroupbinding child
                WHERE child.route_group_id = deltallm_routegroup.route_group_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_routepolicy child
                WHERE child.route_group_id = deltallm_routegroup.route_group_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_routegroupmember child
                WHERE child.route_group_id = deltallm_routegroup.route_group_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM deltallm_callabletargetbinding child
                WHERE child.callable_key = deltallm_routegroup.group_key
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_prompt_children(
        self,
        table: str,
        id_column: str,
        organization_id: str,
        limit: int,
    ) -> int:
        return await self._delete_ids(
            table,
            id_column,
            """
            prompt_template_id IN (
                SELECT prompt_template_id FROM deltallm_prompttemplate
                WHERE owner_scope = 'organization'
                  AND metadata #>> '{_asset_governance,owner_scope_id}' = $1
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_route_children(
        self,
        table: str,
        id_column: str,
        group_column: str,
        organization_id: str,
        limit: int,
    ) -> int:
        return await self._delete_ids(
            table,
            id_column,
            f"""
            {group_column} IN (
                SELECT route_group_id FROM deltallm_routegroup
                WHERE metadata #>> '{{_asset_governance,owner_scope_type}}' = 'organization'
                  AND metadata #>> '{{_asset_governance,owner_scope_id}}' = $1
            )
            """,
            organization_id,
            limit,
        )

    async def _delete_ids(
        self,
        table: str,
        id_column: str,
        predicate: str,
        organization_id: str,
        limit: int,
    ) -> int:
        allowed_targets = {
            ("deltallm_mcpapprovalrequest", "mcp_approval_request_id"),
            ("deltallm_mcpbinding", "mcp_binding_id"),
            ("deltallm_mcptoolpolicy", "mcp_tool_policy_id"),
            ("deltallm_mcpserver", "mcp_server_id"),
            ("deltallm_promptbinding", "prompt_binding_id"),
            ("deltallm_promptlabel", "prompt_label_id"),
            ("deltallm_promptversion", "prompt_version_id"),
            ("deltallm_prompttemplate", "prompt_template_id"),
            ("deltallm_routegroupbinding", "route_group_binding_id"),
            ("deltallm_callabletargetbinding", "callable_target_binding_id"),
            ("deltallm_routepolicy", "route_policy_id"),
            ("deltallm_routegroupmember", "membership_id"),
            ("deltallm_routegroup", "route_group_id"),
        }
        if (table, id_column) not in allowed_targets:
            raise ValueError("unsupported organization asset cleanup target")
        rows = await self.prisma.query_raw(
            f"""
            WITH candidates AS MATERIALIZED (
                SELECT {id_column}
                FROM {table}
                WHERE {predicate}
                ORDER BY {id_column} ASC
                LIMIT $2
            )
            DELETE FROM {table} target
            USING candidates c
            WHERE target.{id_column} = c.{id_column}
            RETURNING target.{id_column}
            """,
            organization_id,
            limit,
        )
        return len(rows)


__all__ = ["OrganizationDeletionAssetCleanup"]
