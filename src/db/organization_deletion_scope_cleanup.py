from __future__ import annotations

from typing import Any

from src.db.organization_deletion_cleanup_types import CleanupPageResult
from src.db.organization_deletion_scope_inventory import (
    ORGANIZATION_SCOPE_INVENTORY_CTE_SQL,
    ambiguous_approval_predicate,
    ambiguous_prompt_log_predicate,
    approval_attribution_predicate,
    prompt_log_attribution_predicate,
    scope_predicate,
)


_SCOPED_TABLES = (
    "deltallm_routegroupbinding",
    "deltallm_callabletargetbinding",
    "deltallm_callabletargetaccessgroupbinding",
    "deltallm_callabletargetscopepolicy",
    "deltallm_mcpbinding",
    "deltallm_mcpscopepolicy",
    "deltallm_mcptoolpolicy",
    "deltallm_promptbinding",
)


class OrganizationDeletionScopeCleanup:
    """Bounded cleanup using the shared organization attribution inventory."""

    def __init__(self, prisma_client: Any) -> None:
        self.prisma = prisma_client

    async def reject_pending_approvals(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> int:
        rows = await self.prisma.query_raw(
            f"""
            WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL},
            candidates AS (
                SELECT a.mcp_approval_request_id
                FROM deltallm_mcpapprovalrequest a
                WHERE ({approval_attribution_predicate()})
                  AND a.status = 'pending'
                ORDER BY a.created_at ASC, a.mcp_approval_request_id ASC
                LIMIT $2
            )
            UPDATE deltallm_mcpapprovalrequest a
            SET status = 'rejected', decision_comment = 'Organization deletion requested',
                decided_at = NOW(), updated_at = NOW()
            FROM candidates c
            WHERE a.mcp_approval_request_id = c.mcp_approval_request_id
            RETURNING a.mcp_approval_request_id
            """,
            organization_id,
            page_size,
        )
        return len(rows)

    async def delete_sensitive_history_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        prompt_logs = await self._delete_prompt_logs(
            organization_id,
            page_size=page_size,
        )
        if prompt_logs >= page_size:
            return CleanupPageResult(processed=prompt_logs, remaining=True)
        approvals = await self._delete_approvals(
            organization_id,
            page_size=page_size - prompt_logs,
        )
        processed = prompt_logs + approvals
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def delete_scoped_access_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        processed = 0
        for table in _SCOPED_TABLES:
            remaining_budget = page_size - processed
            if remaining_budget <= 0:
                break
            rows = await self.prisma.query_raw(
                f"""
                WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL},
                candidates AS (
                    SELECT target.ctid
                    FROM {table} target
                    WHERE ({scope_predicate("target")})
                    LIMIT $2
                )
                DELETE FROM {table} target
                USING candidates c
                WHERE target.ctid = c.ctid
                RETURNING 1
                """,
                organization_id,
                remaining_budget,
            )
            processed += len(rows)
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def has_sensitive_history(self, organization_id: str) -> bool:
        rows = await self.prisma.query_raw(
            f"""
            WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL}
            SELECT (
                EXISTS (
                    SELECT 1
                    FROM deltallm_promptrenderlog l
                    WHERE ({prompt_log_attribution_predicate()})
                ) OR EXISTS (
                    SELECT 1
                    FROM deltallm_mcpapprovalrequest a
                    WHERE ({approval_attribution_predicate()})
                )
            ) AS has_sensitive_history
            """,
            organization_id,
        )
        return bool(rows and rows[0].get("has_sensitive_history"))

    async def has_ambiguous_sensitive_records(self, organization_id: str) -> bool:
        rows = await self.prisma.query_raw(
            f"""
            WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL}
            SELECT (
                EXISTS (
                    SELECT 1
                    FROM deltallm_promptrenderlog l
                    WHERE ({ambiguous_prompt_log_predicate()})
                ) OR EXISTS (
                    SELECT 1
                    FROM deltallm_mcpapprovalrequest a
                    WHERE ({ambiguous_approval_predicate()})
                )
            ) AS has_ambiguous_sensitive_records
            """,
            organization_id,
        )
        return bool(rows and rows[0].get("has_ambiguous_sensitive_records"))

    async def has_scoped_access(self, organization_id: str) -> bool:
        union_sql = " UNION ALL ".join(
            f"SELECT scope_type, scope_id FROM {table}" for table in _SCOPED_TABLES
        )
        rows = await self.prisma.query_raw(
            f"""
            WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL}
            SELECT EXISTS (
                SELECT 1
                FROM ({union_sql}) target
                WHERE ({scope_predicate("target")})
            ) AS has_scoped_access
            """,
            organization_id,
        )
        return bool(rows and rows[0].get("has_scoped_access"))

    async def _delete_prompt_logs(self, organization_id: str, *, page_size: int) -> int:
        rows = await self.prisma.query_raw(
            f"""
            WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL},
            candidates AS (
                SELECT l.prompt_render_log_id
                FROM deltallm_promptrenderlog l
                WHERE ({prompt_log_attribution_predicate()})
                ORDER BY l.created_at ASC, l.prompt_render_log_id ASC
                LIMIT $2
            )
            DELETE FROM deltallm_promptrenderlog l
            USING candidates c
            WHERE l.prompt_render_log_id = c.prompt_render_log_id
            RETURNING l.prompt_render_log_id
            """,
            organization_id,
            page_size,
        )
        return len(rows)

    async def _delete_approvals(self, organization_id: str, *, page_size: int) -> int:
        rows = await self.prisma.query_raw(
            f"""
            WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL},
            candidates AS (
                SELECT a.mcp_approval_request_id
                FROM deltallm_mcpapprovalrequest a
                WHERE ({approval_attribution_predicate()})
                ORDER BY a.created_at ASC, a.mcp_approval_request_id ASC
                LIMIT $2
            )
            DELETE FROM deltallm_mcpapprovalrequest a
            USING candidates c
            WHERE a.mcp_approval_request_id = c.mcp_approval_request_id
            RETURNING a.mcp_approval_request_id
            """,
            organization_id,
            page_size,
        )
        return len(rows)


__all__ = ["OrganizationDeletionScopeCleanup"]
