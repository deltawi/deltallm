from __future__ import annotations

from src.db.organization_deletion_scope_inventory import (
    ORGANIZATION_SCOPE_INVENTORY_CTE_SQL,
    approval_attribution_predicate,
    conflicting_approval_predicate,
    conflicting_prompt_log_predicate,
    prompt_log_attribution_predicate,
    scope_predicate,
    unattributed_approval_predicate,
    unattributed_prompt_log_predicate,
)


_ORGANIZATION_DELETION_PLAN_SQL_TEMPLATE = """
            WITH __SCOPE_INVENTORY_CTE__,
            target_prompt_render_logs AS MATERIALIZED (
                SELECT l.prompt_render_log_id
                FROM deltallm_promptrenderlog l
                WHERE (__PROMPT_LOG_ATTRIBUTION_PREDICATE__)
            ),
            target_mcp_approvals AS MATERIALIZED (
                SELECT a.mcp_approval_request_id, a.status
                FROM deltallm_mcpapprovalrequest a
                WHERE (__APPROVAL_ATTRIBUTION_PREDICATE__)
            ),
            sensitive_ownership_blockers AS MATERIALIZED (
                SELECT l.prompt_render_log_id AS sensitive_record_id,
                       'conflicting'::text AS classification
                FROM deltallm_promptrenderlog l
                WHERE (__CONFLICTING_PROMPT_LOG_PREDICATE__)
                UNION ALL
                SELECT a.mcp_approval_request_id, 'conflicting'::text
                FROM deltallm_mcpapprovalrequest a
                WHERE (__CONFLICTING_APPROVAL_PREDICATE__)
                UNION ALL
                SELECT l.prompt_render_log_id, 'unattributed'::text
                FROM deltallm_promptrenderlog l
                WHERE (__UNATTRIBUTED_PROMPT_LOG_PREDICATE__)
                UNION ALL
                SELECT a.mcp_approval_request_id, 'unattributed'::text
                FROM deltallm_mcpapprovalrequest a
                WHERE (__UNATTRIBUTED_APPROVAL_PREDICATE__)
            )
            SELECT
                o.organization_id,
                o.organization_name,
                o.lifecycle_state,
                o.lifecycle_version,
                o.deletion_requested_at,
                o.deletion_not_before_at,
                o.deletion_job_id,
                (SELECT COUNT(*)::int
                   FROM target_teams) AS teams,
                (SELECT COUNT(*)::int FROM target_keys) AS api_keys,
                (SELECT COUNT(*)::int
                   FROM deltallm_serviceaccount s
                   JOIN deltallm_teamtable t ON t.team_id = s.team_id
                  WHERE t.organization_id = o.organization_id) AS service_accounts,
                (SELECT COUNT(*)::int
                   FROM deltallm_organizationmembership m
                  WHERE m.organization_id = o.organization_id) AS organization_memberships,
                (SELECT COUNT(*)::int
                   FROM deltallm_teammembership m
                   JOIN deltallm_teamtable t ON t.team_id = m.team_id
                  WHERE t.organization_id = o.organization_id) AS team_memberships,
                (SELECT COUNT(*)::int
                   FROM deltallm_platforminvitation i
                  WHERE i.status IN ('pending', 'sent')
                    AND EXISTS (
                        SELECT 1 FROM (
                            SELECT item
                              FROM jsonb_array_elements(
                                  COALESCE(i.metadata->'organization_invites', '[]'::jsonb)
                              ) item
                            UNION ALL
                            SELECT item
                              FROM jsonb_array_elements(
                                  COALESCE(i.metadata->'team_invites', '[]'::jsonb)
                              ) item
                        ) invite
                        WHERE invite.item->>'organization_id' = o.organization_id
                    )) AS pending_invitations,
                (SELECT COUNT(*)::int
                   FROM target_mcp_approvals a
                  WHERE a.status = 'pending') AS pending_mcp_approvals,
                (SELECT COUNT(*)::int FROM (
                    SELECT scope_type, scope_id FROM deltallm_routegroupbinding
                    UNION ALL SELECT scope_type, scope_id FROM deltallm_callabletargetbinding
                    UNION ALL SELECT scope_type, scope_id FROM deltallm_callabletargetaccessgroupbinding
                    UNION ALL SELECT scope_type, scope_id FROM deltallm_callabletargetscopepolicy
                    UNION ALL SELECT scope_type, scope_id FROM deltallm_mcpbinding
                    UNION ALL SELECT scope_type, scope_id FROM deltallm_mcpscopepolicy
                    UNION ALL SELECT scope_type, scope_id FROM deltallm_mcptoolpolicy
                    UNION ALL SELECT scope_type, scope_id FROM deltallm_promptbinding
                ) scoped
                WHERE (__SCOPED_ATTRIBUTION_PREDICATE__)) AS scope_bindings,
                (SELECT COUNT(*)::int
                   FROM deltallm_mcpserver s
                  WHERE s.owner_scope_type = 'organization'
                    AND s.owner_scope_id = o.organization_id) AS owned_mcp_servers,
                (SELECT COUNT(*)::int
                   FROM deltallm_prompttemplate p
                  WHERE p.owner_scope = 'organization'
                    AND p.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                ) AS owned_prompt_templates,
                (SELECT COUNT(*)::int
                   FROM deltallm_routegroup g
                  WHERE g.metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
                    AND g.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                ) AS owned_route_groups,
                (SELECT COUNT(*)::int FROM (
                    SELECT b.mcp_binding_id AS dependency_id
                      FROM deltallm_mcpbinding b
                      JOIN deltallm_mcpserver s ON s.mcp_server_id = b.mcp_server_id
                     WHERE s.owner_scope_type = 'organization'
                       AND s.owner_scope_id = o.organization_id
                       AND NOT (__BINDING_ATTRIBUTION_PREDICATE__)
                    UNION ALL
                    SELECT p.mcp_tool_policy_id
                      FROM deltallm_mcptoolpolicy p
                      JOIN deltallm_mcpserver s ON s.mcp_server_id = p.mcp_server_id
                     WHERE s.owner_scope_type = 'organization'
                       AND s.owner_scope_id = o.organization_id
                       AND NOT (__POLICY_ATTRIBUTION_PREDICATE__)
                    UNION ALL
                    SELECT a.mcp_approval_request_id
                      FROM deltallm_mcpapprovalrequest a
                      JOIN deltallm_mcpserver s ON s.mcp_server_id = a.mcp_server_id
                     WHERE s.owner_scope_type = 'organization'
                       AND s.owner_scope_id = o.organization_id
                       AND NOT (__APPROVAL_SCOPE_ATTRIBUTION_PREDICATE__)
                ) dependencies) AS external_mcp_dependencies,
                (SELECT COUNT(*)::int FROM (
                    SELECT b.prompt_binding_id AS dependency_id
                      FROM deltallm_promptbinding b
                      JOIN deltallm_prompttemplate p
                        ON p.prompt_template_id = b.prompt_template_id
                     WHERE p.owner_scope = 'organization'
                       AND p.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                       AND NOT (__BINDING_ATTRIBUTION_PREDICATE__)
                    UNION ALL
                    SELECT g.route_group_id
                      FROM deltallm_routegroup g
                      JOIN deltallm_prompttemplate p
                        ON p.template_key = g.metadata #>> '{default_prompt,template_key}'
                     WHERE p.owner_scope = 'organization'
                       AND p.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                       AND NOT COALESCE((
                           g.metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
                           AND g.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                       ), FALSE)
                ) dependencies) AS external_prompt_dependencies,
                (SELECT COUNT(*)::int FROM (
                    SELECT b.route_group_binding_id AS dependency_id
                      FROM deltallm_routegroupbinding b
                      JOIN deltallm_routegroup g ON g.route_group_id = b.route_group_id
                     WHERE g.metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
                       AND g.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                       AND NOT (__BINDING_ATTRIBUTION_PREDICATE__)
                    UNION ALL
                    SELECT b.callable_target_binding_id
                      FROM deltallm_callabletargetbinding b
                      JOIN deltallm_routegroup g ON g.group_key = b.callable_key
                     WHERE g.metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
                       AND g.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                       AND NOT (__BINDING_ATTRIBUTION_PREDICATE__)
                    UNION ALL
                    SELECT v.prompt_version_id
                      FROM deltallm_promptversion v
                      JOIN deltallm_prompttemplate p
                        ON p.prompt_template_id = v.prompt_template_id
                      JOIN deltallm_routegroup g
                        ON g.group_key = v.route_preferences->>'route_group'
                     WHERE g.metadata #>> '{_asset_governance,owner_scope_type}' = 'organization'
                       AND g.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                       AND NOT COALESCE((
                           p.owner_scope = 'organization'
                           AND p.metadata #>> '{_asset_governance,owner_scope_id}' = o.organization_id
                       ), FALSE)
                ) dependencies) AS external_route_group_dependencies,
                (SELECT COUNT(*)::int FROM target_prompt_render_logs) AS prompt_render_logs,
                (SELECT COUNT(*)::int FROM sensitive_ownership_blockers)
                  AS ambiguous_sensitive_records,
                (SELECT COUNT(*)::int FROM sensitive_ownership_blockers
                  WHERE classification = 'conflicting') AS conflicting_sensitive_records,
                (SELECT COUNT(*)::int FROM sensitive_ownership_blockers
                  WHERE classification = 'unattributed') AS unattributed_sensitive_records,
                (SELECT COUNT(*)::int
                   FROM deltallm_batch_job j
                   LEFT JOIN deltallm_teamtable t ON t.team_id = j.created_by_team_id
                  WHERE (
                      COALESCE(j.created_by_organization_id, t.organization_id)
                          = o.organization_id
                      OR (
                          j.created_by_organization_id IS NULL
                          AND j.created_by_team_id IS NULL
                          AND (
                              j.created_by_api_key IN (SELECT token FROM target_keys)
                              OR j.created_by_user_id IN (
                                  SELECT user_id FROM target_candidate_users
                              )
                          )
                      )
                  )
                    AND j.status IN ('queued', 'in_progress', 'finalizing')) AS active_batches,
                (SELECT COUNT(*)::int
                   FROM deltallm_batch_create_session s
                   LEFT JOIN deltallm_teamtable t ON t.team_id = s.created_by_team_id
                  WHERE (
                      COALESCE(s.created_by_organization_id, t.organization_id)
                          = o.organization_id
                      OR (
                          s.created_by_organization_id IS NULL
                          AND s.created_by_team_id IS NULL
                          AND (
                              s.created_by_api_key IN (SELECT token FROM target_keys)
                              OR s.created_by_user_id IN (
                                  SELECT user_id FROM target_candidate_users
                              )
                          )
                      )
                  )
                    AND s.status IN ('staged', 'failed_retryable', 'failed_permanent')) AS staged_batch_sessions,
                (SELECT COUNT(*)::int FROM (
                    SELECT j.batch_id AS record_id
                    FROM deltallm_batch_job j
                    WHERE j.created_by_organization_id IS NULL
                      AND (
                          j.created_by_team_id IN (SELECT team_id FROM target_teams)
                          OR j.created_by_api_key IN (SELECT token FROM target_keys)
                          OR j.created_by_user_id IN (
                              SELECT user_id FROM target_candidate_users
                          )
                      )
                    UNION ALL
                    SELECT s.session_id
                    FROM deltallm_batch_create_session s
                    WHERE s.created_by_organization_id IS NULL
                      AND (
                          s.created_by_team_id IN (SELECT team_id FROM target_teams)
                          OR s.created_by_api_key IN (SELECT token FROM target_keys)
                          OR s.created_by_user_id IN (
                              SELECT user_id FROM target_candidate_users
                          )
                      )
                    UNION ALL
                    SELECT w.event_id
                    FROM deltallm_batch_webhook_outbox w
                    WHERE w.created_by_organization_id IS NULL
                      AND (
                          w.created_by_team_id IN (SELECT team_id FROM target_teams)
                          OR EXISTS (
                              SELECT 1
                              FROM deltallm_batch_job source
                              WHERE source.batch_id = w.batch_id
                                AND (
                                    source.created_by_organization_id = o.organization_id
                                    OR source.created_by_team_id IN (
                                        SELECT team_id FROM target_teams
                                    )
                                    OR source.created_by_api_key IN (
                                        SELECT token FROM target_keys
                                    )
                                    OR source.created_by_user_id IN (
                                        SELECT user_id FROM target_candidate_users
                                    )
                                )
                          )
                      )
                ) unresolved) AS unresolved_batch_ownership_records,
                (SELECT COUNT(*)::int FROM deltallm_spendlog_events e
                  WHERE e.organization_id = o.organization_id) AS retained_spend_events,
                (SELECT COUNT(*)::int FROM deltallm_auditevent e
                  WHERE e.organization_id = o.organization_id) AS retained_audit_events,
                (SELECT COUNT(*)::int
                   FROM deltallm_batch_job j
                   LEFT JOIN deltallm_teamtable t ON t.team_id = j.created_by_team_id
                  WHERE COALESCE(j.created_by_organization_id, t.organization_id) = o.organization_id
                ) AS retained_batch_jobs,
                (SELECT COUNT(*)::int
                   FROM deltallm_batch_file f
                   LEFT JOIN deltallm_teamtable t ON t.team_id = f.created_by_team_id
                  WHERE COALESCE(f.created_by_organization_id, t.organization_id) = o.organization_id
                ) AS retained_batch_files
            FROM deltallm_organizationtable o
            WHERE o.organization_id = $1
            LIMIT 1
            """


ORGANIZATION_DELETION_PLAN_SQL = (
    _ORGANIZATION_DELETION_PLAN_SQL_TEMPLATE.replace(
        "__SCOPE_INVENTORY_CTE__", ORGANIZATION_SCOPE_INVENTORY_CTE_SQL
    )
    .replace("__APPROVAL_ATTRIBUTION_PREDICATE__", approval_attribution_predicate())
    .replace("__SCOPED_ATTRIBUTION_PREDICATE__", scope_predicate("scoped"))
    .replace("__BINDING_ATTRIBUTION_PREDICATE__", scope_predicate("b"))
    .replace("__POLICY_ATTRIBUTION_PREDICATE__", scope_predicate("p"))
    .replace("__APPROVAL_SCOPE_ATTRIBUTION_PREDICATE__", scope_predicate("a"))
    .replace("__PROMPT_LOG_ATTRIBUTION_PREDICATE__", prompt_log_attribution_predicate())
    .replace("__CONFLICTING_PROMPT_LOG_PREDICATE__", conflicting_prompt_log_predicate())
    .replace("__CONFLICTING_APPROVAL_PREDICATE__", conflicting_approval_predicate())
    .replace("__UNATTRIBUTED_PROMPT_LOG_PREDICATE__", unattributed_prompt_log_predicate())
    .replace("__UNATTRIBUTED_APPROVAL_PREDICATE__", unattributed_approval_predicate())
)


__all__ = ["ORGANIZATION_DELETION_PLAN_SQL"]
