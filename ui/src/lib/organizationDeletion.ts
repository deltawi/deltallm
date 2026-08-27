import { apiFetch } from './api/transport';
import type { KnownOrganizationLifecycleState } from './organizationLifecycle';

export type { OrganizationLifecycleState } from './organizationLifecycle';

export type OrganizationDeletionCounts = {
  teams: number;
  api_keys: number;
  service_accounts: number;
  organization_memberships: number;
  team_memberships: number;
  pending_invitations: number;
  pending_mcp_approvals: number;
  scope_bindings: number;
  owned_mcp_servers: number;
  owned_prompt_templates: number;
  owned_route_groups: number;
  external_mcp_dependencies: number;
  external_prompt_dependencies: number;
  external_route_group_dependencies: number;
  prompt_render_logs: number;
  ambiguous_sensitive_records: number;
  conflicting_sensitive_records: number;
  unattributed_sensitive_records: number;
  active_batches: number;
  staged_batch_sessions: number;
  unresolved_batch_ownership_records: number;
  retained_spend_events: number;
  retained_audit_events: number;
  retained_batch_jobs: number;
  retained_batch_files: number;
};

export type OrganizationDeletionPhase =
  | 'cancel_pending'
  | 'cancel_batches'
  | 'wait_for_batches'
  | 'resolve_owned_assets'
  | 'purge_sensitive_history'
  | 'remove_scoped_access'
  | 'revoke_credentials'
  | 'remove_tenant_state'
  | 'finalize'
  | 'completed'
  | 'restored';

export type OrganizationDeletionPlan = {
  organization_id: string;
  organization_name: string | null;
  lifecycle_state: KnownOrganizationLifecycleState;
  lifecycle_version: number;
  deletion_job_id: string | null;
  deletion_requested_at: string | null;
  deletion_not_before_at: string | null;
  counts: OrganizationDeletionCounts;
  automatic_cleanup: string[];
  retained_history: string[];
  cancellation_effects: string[];
  blocking_dependencies: string[];
  recovery_window_hours: number;
  lifecycle_protocol_version: number;
  requests_enabled: boolean;
  can_request: boolean;
  plan_token: string;
};

export type OrganizationDeletionJob = {
  deletion_job_id: string;
  organization_id: string;
  status: 'pending' | 'processing' | 'waiting' | 'completed' | 'failed' | 'restored';
  phase: OrganizationDeletionPhase;
  progress: Record<string, unknown>;
  not_before_at: string | null;
  attempt_count: number;
  max_attempts: number;
  last_error_code: string | null;
  last_error_detail: string | null;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  restored_at: string | null;
  restore_allowed: boolean;
  immediate_invalidation_succeeded?: boolean | null;
};

export type OrganizationDeletionRequest = {
  confirmation_name: string;
  plan_token: string;
  acknowledge_running_work_cancellation: boolean;
  options: {
    owned_mcp_servers: 'delete';
    owned_prompt_templates: 'delete';
    owned_route_groups: 'delete';
  };
};

function basePath(organizationId: string): string {
  return `/ui/api/organizations/${encodeURIComponent(organizationId)}/deletion-requests`;
}

export const organizationDeletion = {
  plan: (organizationId: string, signal: AbortSignal) =>
    apiFetch<OrganizationDeletionPlan>(
      `/ui/api/organizations/${encodeURIComponent(organizationId)}/deletion-plan`,
      { signal },
    ),
  request: (
    organizationId: string,
    payload: OrganizationDeletionRequest,
    idempotencyKey: string,
    signal: AbortSignal,
  ) => apiFetch<OrganizationDeletionJob>(basePath(organizationId), {
    method: 'POST',
    signal,
    headers: { 'Idempotency-Key': idempotencyKey },
    json: payload,
  }),
  job: (organizationId: string, deletionJobId: string, signal: AbortSignal) =>
    apiFetch<OrganizationDeletionJob>(
      `${basePath(organizationId)}/${encodeURIComponent(deletionJobId)}`,
      { signal },
    ),
  restore: (organizationId: string, deletionJobId: string, signal: AbortSignal) =>
    apiFetch<OrganizationDeletionJob>(
      `${basePath(organizationId)}/${encodeURIComponent(deletionJobId)}/restore`,
      { method: 'POST', signal },
    ),
  retry: (organizationId: string, deletionJobId: string, signal: AbortSignal) =>
    apiFetch<OrganizationDeletionJob>(
      `${basePath(organizationId)}/${encodeURIComponent(deletionJobId)}/retry`,
      { method: 'POST', signal },
    ),
};
