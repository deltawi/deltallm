from __future__ import annotations

from src.db.organization_deletion_scope_inventory import (
    ORGANIZATION_SCOPE_INVENTORY_CTE_SQL,
    ambiguous_approval_predicate,
    ambiguous_prompt_log_predicate,
    approval_attribution_predicate,
    prompt_log_attribution_predicate,
    scope_predicate,
)


_SCOPED_ACCESS_UNION_SQL = """
    SELECT scope_type, scope_id FROM deltallm_routegroupbinding
    UNION ALL SELECT scope_type, scope_id FROM deltallm_callabletargetbinding
    UNION ALL
      SELECT scope_type, scope_id FROM deltallm_callabletargetaccessgroupbinding
    UNION ALL SELECT scope_type, scope_id FROM deltallm_callabletargetscopepolicy
    UNION ALL SELECT scope_type, scope_id FROM deltallm_mcpbinding
    UNION ALL SELECT scope_type, scope_id FROM deltallm_mcpscopepolicy
    UNION ALL SELECT scope_type, scope_id FROM deltallm_mcptoolpolicy
    UNION ALL SELECT scope_type, scope_id FROM deltallm_promptbinding
"""


# This is the authoritative, bounded completion inventory. It runs after the
# finalizer has locked both the deletion job and organization row, so lifecycle-
# aware writers either committed before this snapshot or wait and fail closed.
ORGANIZATION_DELETION_FINAL_INVENTORY_SQL = f"""
WITH {ORGANIZATION_SCOPE_INVENTORY_CTE_SQL},
inventory AS (
    SELECT
        EXISTS (
            SELECT 1
            FROM deltallm_batch_job j
            WHERE (
                j.created_by_organization_id = $1
                OR (
                    j.created_by_organization_id IS NULL
                    AND j.created_by_team_id IN (SELECT team_id FROM target_teams)
                )
            )
              AND j.status IN ('queued', 'in_progress', 'finalizing')
        ) AS active_batches,
        EXISTS (
            SELECT 1
            FROM deltallm_batch_create_session s
            WHERE (
                s.created_by_organization_id = $1
                OR (
                    s.created_by_organization_id IS NULL
                    AND s.created_by_team_id IN (SELECT team_id FROM target_teams)
                )
            )
              AND s.status IN ('staged', 'failed_retryable', 'failed_permanent')
        ) AS staged_batch_sessions,
        EXISTS (
            SELECT 1
            FROM deltallm_platforminvitation i
            WHERE i.status IN ('pending', 'sent')
              AND (
                  EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          COALESCE(i.metadata->'organization_invites', '[]'::jsonb)
                      ) item
                      WHERE item->>'organization_id' = $1
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          COALESCE(i.metadata->'team_invites', '[]'::jsonb)
                      ) item
                      WHERE item->>'organization_id' = $1
                  )
              )
        ) AS pending_invitations,
        EXISTS (
            SELECT 1 FROM deltallm_mcpserver s
            WHERE s.owner_scope_type = 'organization' AND s.owner_scope_id = $1
        ) OR EXISTS (
            SELECT 1 FROM deltallm_prompttemplate p
            WHERE p.owner_scope = 'organization'
              AND p.metadata #>> '{{_asset_governance,owner_scope_id}}' = $1
        ) OR EXISTS (
            SELECT 1 FROM deltallm_routegroup g
            WHERE g.metadata #>> '{{_asset_governance,owner_scope_type}}' = 'organization'
              AND g.metadata #>> '{{_asset_governance,owner_scope_id}}' = $1
        ) AS owned_assets,
        EXISTS (
            SELECT 1 FROM deltallm_promptrenderlog l
            WHERE ({prompt_log_attribution_predicate()})
        ) OR EXISTS (
            SELECT 1 FROM deltallm_mcpapprovalrequest a
            WHERE ({approval_attribution_predicate()})
        ) AS sensitive_history,
        EXISTS (
            SELECT 1 FROM deltallm_promptrenderlog l
            WHERE ({ambiguous_prompt_log_predicate()})
        ) OR EXISTS (
            SELECT 1 FROM deltallm_mcpapprovalrequest a
            WHERE ({ambiguous_approval_predicate()})
        ) OR EXISTS (
            SELECT 1 FROM deltallm_batch_job j
            WHERE j.created_by_organization_id IS NULL
              AND (
                  j.created_by_team_id IN (SELECT team_id FROM target_teams)
                  OR j.created_by_api_key IN (SELECT token FROM target_keys)
                  OR j.created_by_user_id IN (
                      SELECT user_id FROM target_candidate_users
                  )
              )
        ) OR EXISTS (
            SELECT 1 FROM deltallm_batch_create_session s
            WHERE s.created_by_organization_id IS NULL
              AND (
                  s.created_by_team_id IN (SELECT team_id FROM target_teams)
                  OR s.created_by_api_key IN (SELECT token FROM target_keys)
                  OR s.created_by_user_id IN (
                      SELECT user_id FROM target_candidate_users
                  )
              )
        ) AS ownership_blockers,
        EXISTS (
            SELECT 1 FROM ({_SCOPED_ACCESS_UNION_SQL}) scoped
            WHERE ({scope_predicate("scoped")})
        ) AS scoped_access,
        EXISTS (
            SELECT 1 FROM deltallm_verificationtoken v
            WHERE v.token IN (SELECT token FROM target_keys)
        ) OR EXISTS (
            SELECT 1 FROM deltallm_serviceaccount s
            WHERE s.team_id IN (SELECT team_id FROM target_teams)
        ) AS credentials,
        EXISTS (
            SELECT 1 FROM deltallm_batch_webhook_outbox w
            WHERE (
                w.created_by_organization_id = $1
                OR (
                    w.created_by_organization_id IS NULL
                    AND w.created_by_team_id IN (SELECT team_id FROM target_teams)
                )
            )
              AND w.status IN ('queued', 'retrying', 'processing')
        ) AS webhook_deliveries,
        EXISTS (
            SELECT 1 FROM deltallm_batch_scheduler_flow flow
            WHERE (
                flow.tenant_scope_type = 'organization' AND flow.tenant_scope_id = $1
            ) OR (
                flow.tenant_scope_type = 'team'
                AND flow.tenant_scope_id IN (SELECT team_id FROM target_teams)
            ) OR (
                flow.tenant_scope_type = 'api_key'
                AND flow.tenant_scope_id IN (SELECT token FROM target_keys)
            ) OR (
                flow.tenant_scope_type = 'user'
                AND flow.tenant_scope_id IN (SELECT user_id FROM target_candidate_users)
            ) OR EXISTS (
                SELECT 1
                FROM deltallm_batch_job j
                WHERE j.tenant_scope_type = flow.tenant_scope_type
                  AND j.tenant_scope_id = flow.tenant_scope_id
                  AND (
                      j.created_by_organization_id = $1
                      OR (
                          j.created_by_organization_id IS NULL
                          AND j.created_by_team_id IN (SELECT team_id FROM target_teams)
                      )
                  )
            )
        ) AS scheduler_flows,
        EXISTS (
            SELECT 1 FROM deltallm_teammodelspend counter
            WHERE counter.team_id IN (SELECT team_id FROM target_teams)
        ) AS team_model_counters,
        EXISTS (
            SELECT 1 FROM deltallm_teamtable t WHERE t.organization_id = $1
        ) OR EXISTS (
            SELECT 1 FROM deltallm_organizationmembership m
            WHERE m.organization_id = $1
        ) OR EXISTS (
            SELECT 1 FROM deltallm_teammembership m
            WHERE m.team_id IN (SELECT team_id FROM target_teams)
        ) OR EXISTS (
            SELECT 1 FROM deltallm_usertable u
            WHERE u.team_id IN (SELECT team_id FROM target_teams)
        ) AS tenant_rows
)
SELECT
    CASE
        WHEN ownership_blockers THEN 'blocked_ownership_classification'
        WHEN active_batches THEN 'cancel_batches'
        WHEN staged_batch_sessions OR pending_invitations THEN 'cancel_pending'
        WHEN owned_assets THEN 'resolve_owned_assets'
        WHEN sensitive_history THEN 'purge_sensitive_history'
        WHEN scoped_access THEN 'remove_scoped_access'
        WHEN credentials THEN 'revoke_credentials'
        WHEN webhook_deliveries OR scheduler_flows OR team_model_counters OR tenant_rows
          THEN 'remove_tenant_state'
        ELSE NULL
    END AS blocker,
    active_batches,
    staged_batch_sessions,
    pending_invitations,
    owned_assets,
    sensitive_history,
    ownership_blockers,
    scoped_access,
    credentials,
    webhook_deliveries,
    scheduler_flows,
    team_model_counters,
    tenant_rows
FROM inventory
"""


__all__ = ["ORGANIZATION_DELETION_FINAL_INVENTORY_SQL"]
