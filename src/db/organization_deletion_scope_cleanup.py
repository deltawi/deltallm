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
        processed = await self._delete_asset_grants(
            organization_id,
            limit=page_size,
        )
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

    async def _delete_asset_grants(self, organization_id: str, *, limit: int) -> int:
        rows = await self.prisma.query_raw(
            f"""
            WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL},
            candidates AS MATERIALIZED (
                SELECT asset_grant.grant_id, asset_grant.managed_asset_id
                FROM deltallm_assetgrant AS asset_grant
                WHERE asset_grant.organization_id = $1
                   OR asset_grant.team_id IN (SELECT team_id FROM target_teams)
                ORDER BY asset_grant.grant_id
                LIMIT $2
            ), removed AS (
                DELETE FROM deltallm_assetgrant AS asset_grant
                USING candidates
                WHERE asset_grant.grant_id = candidates.grant_id
                RETURNING asset_grant.managed_asset_id
            ), bumped AS (
                UPDATE deltallm_managedasset AS asset
                SET policy_version = policy_version + 1,
                    updated_at = NOW()
                WHERE asset.asset_id IN (SELECT managed_asset_id FROM removed)
                RETURNING asset.asset_id, asset.asset_kind
            ), detached_invalid_credentials AS (
                UPDATE deltallm_modeldeployment AS deployment
                SET named_credential_id = NULL,
                    updated_at = NOW()
                WHERE deployment.named_credential_id IS NOT NULL
                  AND deployment.credential_binding_mode = 'audience_scoped'
                  AND EXISTS (
                    SELECT 1
                    FROM deltallm_model AS model
                    JOIN deltallm_managedasset AS model_asset
                      ON model_asset.asset_id = model.managed_asset_id
                    JOIN deltallm_namedcredential AS credential
                      ON credential.credential_id = deployment.named_credential_id
                    JOIN deltallm_managedasset AS credential_asset
                      ON credential_asset.asset_id = credential.managed_asset_id
                    WHERE model.model_id = deployment.model_id
                      AND model_asset.governance_source = 'creator'
                      AND (
                        NOT (
                          credential_asset.owner_account_id = model_asset.owner_account_id
                          OR EXISTS (
                            SELECT 1
                            FROM deltallm_assetgrant AS owner_grant
                            WHERE owner_grant.managed_asset_id = credential_asset.asset_id
                              AND (
                                owner_grant.subject_type = 'public'
                                OR (
                                  owner_grant.subject_type = 'team'
                                  AND EXISTS (
                                    SELECT 1 FROM deltallm_teammembership AS owner_team
                                    WHERE owner_team.account_id = model_asset.owner_account_id
                                      AND owner_team.team_id = owner_grant.team_id
                                  )
                                )
                                OR (
                                  owner_grant.subject_type = 'organization'
                                  AND EXISTS (
                                    SELECT 1 FROM deltallm_organizationmembership AS owner_org
                                    WHERE owner_org.account_id = model_asset.owner_account_id
                                      AND owner_org.organization_id = owner_grant.organization_id
                                  )
                                )
                              )
                          )
                        )
                        OR EXISTS (
                          SELECT 1
                          FROM deltallm_assetgrant AS model_grant
                          LEFT JOIN deltallm_teamtable AS model_team
                            ON model_team.team_id = model_grant.team_id
                          WHERE model_grant.managed_asset_id = model_asset.asset_id
                            AND NOT EXISTS (
                              SELECT 1
                              FROM deltallm_assetgrant AS credential_grant
                              WHERE credential_grant.managed_asset_id = credential_asset.asset_id
                                AND (
                                  credential_grant.subject_type = 'public'
                                  OR (
                                    model_grant.subject_type = 'team'
                                    AND credential_grant.subject_type = 'team'
                                    AND credential_grant.team_id = model_grant.team_id
                                  )
                                  OR (
                                    model_grant.subject_type = 'team'
                                    AND credential_grant.subject_type = 'organization'
                                    AND credential_grant.organization_id = model_team.organization_id
                                  )
                                  OR (
                                    model_grant.subject_type = 'organization'
                                    AND credential_grant.subject_type = 'organization'
                                    AND credential_grant.organization_id = model_grant.organization_id
                                  )
                                )
                            )
                          )
                        )
                      )
                RETURNING deployment.deployment_id
            ), revision_bump AS (
                UPDATE deltallm_routeruntimestate
                SET revision = revision + 1,
                    updated_at = NOW()
                WHERE state_key = 'routing_runtime'
                  AND (
                    EXISTS (SELECT 1 FROM detached_invalid_credentials)
                    OR EXISTS (
                      SELECT 1 FROM bumped
                      WHERE asset_kind IN (
                          'model', 'route_group', 'mcp_server', 'prompt_template'
                      )
                    )
                  )
                RETURNING revision
            )
            SELECT asset_id FROM bumped
            """,
            organization_id,
            limit,
        )
        return len(rows)

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
            ) OR EXISTS (
                SELECT 1
                FROM deltallm_assetgrant AS asset_grant
                WHERE asset_grant.organization_id = $1
                   OR asset_grant.team_id IN (SELECT team_id FROM target_teams)
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
