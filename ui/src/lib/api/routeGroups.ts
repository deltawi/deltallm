import { apiFetch, withQuery } from './transport';

export interface RouteGroup {
  route_group_id: string;
  group_key: string;
  name: string | null;
  mode: string;
  routing_strategy: string | null;
  enabled: boolean;
  member_count: number;
  metadata: Record<string, unknown> | null;
  default_prompt?: { template_key: string; label?: string | null } | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface RouteGroupMember {
  membership_id: string;
  route_group_id: string;
  deployment_id: string;
  enabled: boolean;
  weight: number | null;
  priority: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface RouteGroupMemberDetail extends RouteGroupMember {
  model_name?: string | null;
  provider?: string | null;
  mode?: string | null;
  healthy?: boolean | null;
}

export interface RoutePolicy {
  route_policy_id: string;
  route_group_id: string;
  version: number;
  semantics_version: number;
  status: string;
  policy_json: Record<string, unknown>;
  published_at: string | null;
  published_by: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface RouteGroupBinding {
  route_group_binding_id: string;
  route_group_id: string;
  group_key: string;
  scope_type: string;
  scope_id: string;
  enabled: boolean;
  metadata: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MutationWarnings {
  warnings: string[];
}

export type RouteGroupMutationResponse = RouteGroup & MutationWarnings;
export type RouteGroupMemberMutationResponse = RouteGroupMember & MutationWarnings;
export interface DeleteRouteGroupResponse extends MutationWarnings {
  deleted: boolean;
}

export interface RoutePolicyMutationResponse extends MutationWarnings {
  group_key: string;
  policy: RoutePolicy;
}

export interface RollbackRoutePolicyResponse extends RoutePolicyMutationResponse {
  rolled_back_from_version: number;
}

export interface RouteGroupWritePayload {
  group_key?: string;
  name?: string | null;
  mode?: string;
  strategy?: string | null;
  enabled?: boolean;
  metadata?: Record<string, unknown> | null;
  default_prompt?: { template_key: string; label?: string | null } | null;
  owner_scope_type?: string | null;
  owner_scope_id?: string | null;
}

export interface RouteGroupMemberWritePayload {
  deployment_id: string;
  enabled?: boolean;
  weight?: number | null;
  priority?: number | null;
}

export interface RouteGroupListResponse {
  data: RouteGroup[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
    has_more: boolean;
  };
}

export type RoutePolicySimulationOutcome = 'success' | 'timeout' | 'rate_limit' | 'unavailable';

export interface RoutePolicySimulationRequest {
  iterations?: number;
  policy?: Record<string, unknown> | null;
  metadata?: Record<string, unknown>;
  user_id?: string;
  prompt_ref?: Record<string, unknown> | null;
  outcomes?: Array<{
    deployment_id: string;
    outcome: RoutePolicySimulationOutcome;
  }>;
}

export interface RoutePolicySimulationSelection {
  deployment_id: string;
  count: number;
  ratio: number;
}

export interface RoutePolicySimulationAttempt {
  iteration: number;
  attempt: number;
  deployment_id: string;
  outcome: RoutePolicySimulationOutcome;
  transition: 'primary' | 'retry' | 'fallback';
}

export interface RoutePolicySimulationResponse {
  group_key: string;
  iterations: number;
  basis: 'live_state_dry_run';
  warnings: string[];
  prompt: {
    template_key: string;
    version: number;
    label: string | null;
    route_preferences: Record<string, unknown>;
  } | null;
  effective_metadata: Record<string, unknown>;
  summary: {
    selected_requests: number;
    no_selection_requests: number;
    served_requests: number;
    failed_requests: number;
    fallback_requests: number;
    timed_out_requests: number;
    total_attempts: number;
  };
  reason_counts: Record<string, number>;
  selections: RoutePolicySimulationSelection[];
  served_deployments: RoutePolicySimulationSelection[];
  terminal_outcomes: Record<string, number>;
  sample_decision: Record<string, unknown> | null;
  sample_attempts: RoutePolicySimulationAttempt[];
}

export const routeGroups = {
  list: (
    params?: { search?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => apiFetch<RouteGroupListResponse>(withQuery('/ui/api/route-groups', params), { signal }),
  get: (groupKey: string, signal?: AbortSignal) =>
    apiFetch<{
      group: RouteGroup;
      members: RouteGroupMemberDetail[];
      policy: RoutePolicy | null;
      bindings: RouteGroupBinding[];
    }>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}`, { signal }),
  create: (payload: RouteGroupWritePayload, signal?: AbortSignal) =>
    apiFetch<RouteGroupMutationResponse>('/ui/api/route-groups', {
      method: 'POST',
      json: payload,
      signal,
    }),
  update: (groupKey: string, payload: RouteGroupWritePayload, signal?: AbortSignal) =>
    apiFetch<RouteGroupMutationResponse>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}`, {
      method: 'PUT',
      json: payload,
      signal,
    }),
  delete: (groupKey: string, signal?: AbortSignal) =>
    apiFetch<DeleteRouteGroupResponse>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}`, {
      method: 'DELETE',
      signal,
    }),
  members: (groupKey: string, signal?: AbortSignal) =>
    apiFetch<RouteGroupMember[]>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/members`,
      { signal },
    ),
  upsertMember: (
    groupKey: string,
    payload: RouteGroupMemberWritePayload,
    signal?: AbortSignal,
  ) =>
    apiFetch<RouteGroupMemberMutationResponse>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/members`,
      { method: 'POST', json: payload, signal },
    ),
  removeMember: (groupKey: string, deploymentId: string, signal?: AbortSignal) =>
    apiFetch<DeleteRouteGroupResponse>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/members/${encodeURIComponent(deploymentId)}`,
      { method: 'DELETE', signal },
    ),
  getPolicy: (groupKey: string, signal?: AbortSignal) =>
    apiFetch<{ group_key: string; policy: RoutePolicy | null }>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy`,
      { signal },
    ),
  listPolicies: (groupKey: string, signal?: AbortSignal) =>
    apiFetch<{ group_key: string; policies: RoutePolicy[] }>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policies`,
      { signal },
    ),
  validatePolicy: (
    groupKey: string,
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ) =>
    apiFetch<{
      group_key: string;
      valid: boolean;
      policy: Record<string, unknown>;
      warnings: string[];
    }>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/validate`, {
      method: 'POST',
      json: payload,
      signal,
    }),
  savePolicyDraft: (
    groupKey: string,
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ) =>
    apiFetch<RoutePolicyMutationResponse>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/draft`,
      { method: 'POST', json: payload, signal },
    ),
  publishPolicy: (
    groupKey: string,
    payload?: Record<string, unknown>,
    signal?: AbortSignal,
  ) =>
    apiFetch<RoutePolicyMutationResponse>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/publish`,
      { method: 'POST', json: payload ?? {}, signal },
    ),
  rollbackPolicy: (groupKey: string, version: number, signal?: AbortSignal) =>
    apiFetch<RollbackRoutePolicyResponse>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/rollback`,
      { method: 'POST', json: { version }, signal },
    ),
  simulatePolicy: (
    groupKey: string,
    payload: RoutePolicySimulationRequest,
    signal?: AbortSignal,
  ) => apiFetch<RoutePolicySimulationResponse>(
    `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/simulate`,
    { method: 'POST', json: payload, signal },
  ),
};
