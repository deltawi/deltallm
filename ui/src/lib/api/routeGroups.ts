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

export interface RoutePolicySelectorLane {
  id: string;
  rank: number;
  description: string;
}

export interface RoutePolicySelector {
  kind: 'llm-tier';
  classifier_deployment_id: string;
  timeout_ms?: number;
  max_input_chars?: number;
  default_lane?: string | null;
  lanes: RoutePolicySelectorLane[];
}

export interface RoutePolicyMemberDocument {
  deployment_id: string;
  enabled?: boolean;
  weight?: number | null;
  priority?: number | null;
  lane?: string | null;
  [opaqueField: string]: unknown;
}

export interface RoutePolicyContextDocument {
  mode?: 'eligible-only' | 'smallest-sufficient';
  unknown_capacity?: 'allow' | 'exclude';
  default_output_tokens?: number;
  safety_margin_tokens?: number;
  [opaqueField: string]: unknown;
}

export interface RoutePolicyDocument {
  mode?: string | null;
  strategy?: string | null;
  members?: RoutePolicyMemberDocument[];
  timeouts?: {
    global_ms?: number;
    global_seconds?: number;
    [opaqueField: string]: unknown;
  };
  retry?: {
    max_attempts?: number;
    retryable_error_classes?: string[];
    [opaqueField: string]: unknown;
  };
  context?: RoutePolicyContextDocument | null;
  selector?: RoutePolicySelector | null;
  [opaqueField: string]: unknown;
}

export type StoredRoutePolicyDocument = Record<string, unknown>;

export interface RoutePolicy {
  route_policy_id: string;
  route_group_id: string;
  version: number;
  semantics_version: number;
  status: string;
  policy_json: StoredRoutePolicyDocument;
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

export interface RoutePolicyCurrentResponse {
  group_key: string;
  policy: RoutePolicy | null;
}

export interface RoutePolicyHistoryResponse {
  group_key: string;
  policies: RoutePolicy[];
}

export interface RoutePolicyValidationResponse {
  group_key: string;
  valid: true;
  policy: RoutePolicyDocument;
  warnings: string[];
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
  input_tokens?: number;
  requested_output_tokens?: number | null;
  policy?: RoutePolicyDocument | null;
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

export interface SelectorDeploymentOption {
  deployment_id: string;
  model_name: string;
  provider: string;
  mode: string;
  eligible: boolean;
  unavailable_reason: string | null;
}

export interface SelectorOptionsQuery {
  search?: string;
  selected_id?: string;
  limit?: number;
  offset?: number;
}

export interface SelectorOptionsPage {
  data: SelectorDeploymentOption[];
  selected: SelectorDeploymentOption | null;
  limit: number;
  offset: number;
  has_more: boolean;
}

export const routeGroups = {
  selectorOptions: (routeGroupId: string, params: SelectorOptionsQuery, signal?: AbortSignal) =>
    apiFetch<SelectorOptionsPage>(withQuery(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/selector-options`, params,
    ), { signal }),
  list: (
    params?: { search?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => apiFetch<RouteGroupListResponse>(withQuery('/ui/api/route-groups', params), { signal }),
  resolveKey: (groupKey: string, signal?: AbortSignal) =>
    apiFetch<{ route_group_id: string; group_key: string }>(
      withQuery('/ui/api/route-groups/resolve/by-key', { group_key: groupKey }), { signal },
    ),
  get: (routeGroupId: string, signal?: AbortSignal) =>
    apiFetch<{
      group: RouteGroup;
      members: RouteGroupMemberDetail[];
      policy: RoutePolicy | null;
      bindings: RouteGroupBinding[];
    }>(`/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}`, { signal }),
  create: (payload: RouteGroupWritePayload, signal?: AbortSignal) =>
    apiFetch<RouteGroupMutationResponse>('/ui/api/route-groups', {
      method: 'POST',
      json: payload,
      signal,
    }),
  update: (routeGroupId: string, payload: RouteGroupWritePayload, signal?: AbortSignal) =>
    apiFetch<RouteGroupMutationResponse>(`/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}`, {
      method: 'PUT',
      json: payload,
      signal,
    }),
  delete: (routeGroupId: string, signal?: AbortSignal) =>
    apiFetch<DeleteRouteGroupResponse>(`/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}`, {
      method: 'DELETE',
      signal,
    }),
  members: (routeGroupId: string, signal?: AbortSignal) =>
    apiFetch<RouteGroupMember[]>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/members`,
      { signal },
    ),
  upsertMember: (
    routeGroupId: string,
    payload: RouteGroupMemberWritePayload,
    signal?: AbortSignal,
  ) =>
    apiFetch<RouteGroupMemberMutationResponse>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/members`,
      { method: 'POST', json: payload, signal },
    ),
  removeMember: (routeGroupId: string, deploymentId: string, signal?: AbortSignal) =>
    apiFetch<DeleteRouteGroupResponse>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/members/${encodeURIComponent(deploymentId)}`,
      { method: 'DELETE', signal },
    ),
  getPolicy: (routeGroupId: string, signal?: AbortSignal) =>
    apiFetch<RoutePolicyCurrentResponse>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/policy`,
      { signal },
    ),
  listPolicies: (routeGroupId: string, signal?: AbortSignal) =>
    apiFetch<RoutePolicyHistoryResponse>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/policies`,
      { signal },
    ),
  validatePolicy: (
    routeGroupId: string,
    payload: RoutePolicyDocument,
    signal?: AbortSignal,
  ) =>
    apiFetch<RoutePolicyValidationResponse>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/policy/validate`,
      {
        method: 'POST',
        json: payload,
        signal,
      },
    ),
  savePolicyDraft: (
    routeGroupId: string,
    payload: RoutePolicyDocument,
    signal?: AbortSignal,
  ) =>
    apiFetch<RoutePolicyMutationResponse>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/policy/draft`,
      { method: 'POST', json: payload, signal },
    ),
  publishPolicy: (
    routeGroupId: string,
    payload?: RoutePolicyDocument,
    signal?: AbortSignal,
  ) =>
    apiFetch<RoutePolicyMutationResponse>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/policy/publish`,
      { method: 'POST', json: payload ?? {}, signal },
    ),
  rollbackPolicy: (routeGroupId: string, version: number, signal?: AbortSignal) =>
    apiFetch<RollbackRoutePolicyResponse>(
      `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/policy/rollback`,
      { method: 'POST', json: { version }, signal },
    ),
  simulatePolicy: (
    routeGroupId: string,
    payload: RoutePolicySimulationRequest,
    signal?: AbortSignal,
  ) => apiFetch<RoutePolicySimulationResponse>(
    `/ui/api/route-groups/by-id/${encodeURIComponent(routeGroupId)}/policy/simulate`,
    { method: 'POST', json: payload, signal },
  ),
};
