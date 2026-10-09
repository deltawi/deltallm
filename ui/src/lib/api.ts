import type { TeamRecord, TeamMemberRecord, TeamMemberCandidate } from './api/teamContracts';
export type { TeamRecord, TeamMemberRecord, TeamMemberCandidate } from './api/teamContracts';
export type * from './api/tierContracts';
import type { OrganizationTierPolicyPreview, TierPolicySimulation, TierPolicySimulationPayload } from './api/tierContracts';
import type { KeyRemovalResult } from './api/keyRevocations';
import type { Paginated, Pagination } from './api/pagination';
import type { BatchItemError } from './api/batchContracts';
import type { ManagedAssetAccess, ManagedAssetAccessInput } from './api/managedAssets';
export type { Paginated, Pagination } from './api/pagination';
import { apiFetch, withQuery } from './api/transport';
import { updateRuntimeOutputTpm } from './api/runtimeUsers';
import {
  organizationRecordsApi,
  type OrganizationTierAssignment,
  type OrganizationTierAssignmentPayload,
} from './api/organizations';

export { ApiError, structuredApiErrorDetail } from './api/transport';
export type { StructuredApiErrorDetail } from './api/transport';
export { managedAssetAccessInput, managedAssets } from './api/managedAssets';
export type {
  AssetAccessRole,
  AssetSubjectType,
  AssetVisibility,
  ManagedAssetAccess,
  ManagedAssetGrantInput,
  ManagedAssetAccessInput,
} from './api/managedAssets';
export type {
  OrganizationCapabilities,
  OrganizationCreatePayload,
  OrganizationListItem,
  OrganizationPrimaryTierSummary,
  OrganizationRecord,
  OrganizationServicePolicy,
  OrganizationTierAssignment,
  OrganizationTierAssignmentPayload,
} from './api/organizations';

export function reportingRequestInit(signal: AbortSignal, forceRefresh = false): RequestInit {
  return forceRefresh
    ? { signal, headers: { 'Cache-Control': 'no-cache' } }
    : { signal };
}

export interface SpendLogsPagination {
  total?: number;
  limit: number;
  offset: number;
  count?: number;
  has_more: boolean;
  next_cursor?: string | null;
  mode?: 'offset' | 'cursor';
}

export interface HealthResponse {
  liveliness?: string;
  readiness?: { status?: string };
  [key: string]: unknown;
}

export interface InvitationAcceptResult {
  accepted: boolean;
  session_established: boolean;
  next_step: string;
  account_id: string;
  email: string;
  role: string;
  mfa_enabled: boolean;
  mfa_required: boolean;
  mfa_prompt: boolean;
  force_password_change: boolean;
}

export interface SpendLog {
  id: string;
  request_id: string;
  call_type: string;
  model: string;
  api_base?: string | null;
  api_key: string;
  spend: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  prompt_tokens_cached?: number;
  completion_tokens_cached?: number;
  start_time?: string | null;
  end_time?: string | null;
  user?: string | null;
  team_id?: string | null;
  end_user?: string | null;
  metadata?: Record<string, unknown> | null;
  cache_hit: boolean;
  cache_key?: string | null;
  request_tags?: string[];
  status?: string | null;
  http_status_code?: number | null;
  error_type?: string | null;
}

export type SpendUsageDimension = 'organization' | 'team' | 'user';
export type SpendUsageMetric = 'spend' | 'tokens';
export type SpendGroupBy = 'model' | SpendUsageDimension | 'api_key';
export type SpendView = 'platform' | 'organization' | 'team' | 'self';

export interface SpendReportingContext {
  api_version: number;
  active_view: SpendView;
}

export interface SpendCapabilities {
  visibility_level: SpendView;
  active_view: SpendView;
  default_view: SpendView;
  available_views: SpendView[];
  self_scoped: boolean;
  allowed_dimensions: SpendUsageDimension[];
  request_logs: boolean;
  user_identity_labels: boolean;
}

export interface SpendSummary {
  total_spend: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_requests: number;
  unique_models: number;
  successful_requests?: number;
  failed_requests?: number;
  capabilities?: SpendCapabilities;
  reporting_context?: SpendReportingContext;
}

export type SpendBucket = 'day' | 'week' | 'month';

export interface SpendTimeSeriesRow {
  group_key: string;
  total_spend: number;
  request_count: number;
  total_tokens: number;
  successful_requests: number;
  failed_requests: number;
}

export interface SpendTimeSeriesReport {
  group_by: 'day';
  interval: SpendBucket;
  breakdown: SpendTimeSeriesRow[];
  reporting_context?: SpendReportingContext;
}

export interface SpendGroupRow {
  group_key: string | null;
  is_unassigned: boolean;
  display_name?: string | null;
  total_spend: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  request_count: number;
}

export interface SpendGroupReport {
  group_by: SpendGroupBy | 'provider' | 'user';
  data: SpendGroupRow[];
  capabilities?: {
    user_identity_labels?: boolean;
  };
  pagination: Pagination;
  reporting_context?: SpendReportingContext;
}

export interface SpendLogsResponse {
  logs: SpendLog[];
  pagination: SpendLogsPagination;
  reporting_context?: SpendReportingContext;
}

export interface SpendFeatureStatus {
  cache_enabled: boolean;
  reporting_api_version?: number;
  capabilities?: SpendCapabilities;
}

export interface ServiceAccount {
  service_account_id: string;
  team_id: string;
  team_alias?: string | null;
  name: string;
  description?: string | null;
  is_active: boolean;
  created_by_account_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MCPServer {
  mcp_server_id: string;
  managed_asset_id?: string | null;
  server_key: string;
  name: string;
  description?: string | null;
  owner_scope_type: 'global' | 'organization';
  owner_scope_id?: string | null;
  transport: 'streamable_http';
  base_url: string;
  enabled: boolean;
  auth_mode: 'none' | 'bearer' | 'basic' | 'header_map';
  auth_credentials_present: boolean;
  forwarded_headers_allowlist?: string[] | null;
  request_timeout_ms: number;
  capabilities_json?: Record<string, unknown> | null;
  capabilities_etag?: string | null;
  capabilities_fetched_at?: string | null;
  last_health_status?: string | null;
  last_health_error?: string | null;
  last_health_at?: string | null;
  last_health_latency_ms?: number | null;
  metadata?: Record<string, unknown> | null;
  created_by_account_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  tool_count: number;
  capabilities?: {
    can_mutate: boolean;
    can_operate: boolean;
    can_manage_scope_config: boolean;
  };
  access?: ManagedAssetAccess;
}

export interface MCPNamespacedTool {
  server_key: string;
  original_name: string;
  namespaced_name: string;
  description?: string | null;
  input_schema: Record<string, unknown>;
}

export interface MCPBinding {
  mcp_binding_id: string;
  mcp_server_id: string;
  scope_type: 'organization' | 'team' | 'api_key';
  scope_id: string;
  enabled: boolean;
  tool_allowlist?: string[] | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MCPToolPolicy {
  mcp_tool_policy_id: string;
  mcp_server_id: string;
  tool_name: string;
  scope_type: 'organization' | 'team' | 'api_key';
  scope_id: string;
  enabled: boolean;
  require_approval?: 'never' | 'manual' | null;
  max_rpm?: number | null;
  max_concurrency?: number | null;
  result_cache_ttl_seconds?: number | null;
  max_total_execution_time_ms?: number | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MCPApprovalRequest {
  mcp_approval_request_id: string;
  mcp_server_id: string;
  tool_name: string;
  scope_type: 'organization' | 'team' | 'api_key';
  scope_id: string;
  status: 'pending' | 'approved' | 'rejected' | 'expired';
  request_fingerprint: string;
  requested_by_api_key?: string | null;
  requested_by_user?: string | null;
  organization_id?: string | null;
  request_id?: string | null;
  correlation_id?: string | null;
  arguments_json?: Record<string, unknown> | null;
  decision_comment?: string | null;
  decided_by_account_id?: string | null;
  decided_at?: string | null;
  expires_at?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
  server?: {
    mcp_server_id: string | null;
    server_key?: string | null;
    name?: string | null;
    owner_scope_type?: 'global' | 'organization' | null;
    owner_scope_id?: string | null;
  } | null;
  capabilities?: {
    can_decide: boolean;
  };
}

export interface MCPServerDetail {
  server: MCPServer;
  tools: MCPNamespacedTool[];
  bindings: MCPBinding[];
  tool_policies: MCPToolPolicy[];
}

export interface MCPOperationsToolRow {
  tool_name: string;
  total_calls: number;
  failed_calls: number;
  avg_latency_ms: number;
}

export interface MCPOperationsFailureRow {
  event_id: string;
  occurred_at: string;
  tool_name: string;
  error_type?: string | null;
  error_code?: string | null;
  latency_ms?: number | null;
  request_id?: string | null;
}

export interface MCPServerOperations {
  window_hours: number;
  summary: {
    total_calls: number;
    failed_calls: number;
    success_calls: number;
    failure_rate: number;
    avg_latency_ms: number;
    approval_requests: number;
    pending_approvals: number;
    approved_approvals: number;
    rejected_approvals: number;
  };
  top_tools: MCPOperationsToolRow[];
  recent_failures: MCPOperationsFailureRow[];
}

export interface ApiKey {
  token: string;
  key_name: string | null;
  user_id: string | null;
  team_id: string;
  team_alias?: string | null;
  owner_account_id?: string | null;
  owner_account_email?: string | null;
  owner_service_account_id?: string | null;
  owner_service_account_name?: string | null;
  spend: number;
  max_budget: number | null;
  rpm_limit: number | null;
  tpm_limit: number | null;
  output_tpm_limit?: number | null;
  rph_limit: number | null;
  rpd_limit: number | null;
  tpd_limit: number | null;
  expires: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  model_output_tpm_limit?: Record<string, number> | null;
}

export interface BatchCapabilities {
  view: boolean;
  cancel: boolean;
  replay_webhook?: boolean;
}

export interface BatchWebhookDelivery {
  event_id: string;
  event_type: string;
  status: string;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at?: string | null;
  last_status_class?: string | null;
  last_error?: string | null;
  lease_expires_at?: string | null;
  created_at: string;
  updated_at: string;
  delivered_at?: string | null;
}

export interface BatchWebhookDeliveryList {
  batch_id: string;
  capabilities: BatchCapabilities;
  data: BatchWebhookDelivery[];
}

export interface BatchWebhookReplayResponse {
  batch_id: string;
  replayed: boolean;
  delivery: BatchWebhookDelivery;
}

export interface BatchFeatureStatus {
  embeddings_batch_enabled: boolean;
}

export interface BatchJobListItem {
  batch_id: string;
  endpoint: string;
  status: string;
  model: string;
  total_items: number;
  completed_items: number;
  failed_items: number;
  cancelled_items: number;
  in_progress_items: number;
  total_cost: number;
  created_by_api_key?: string | null;
  created_by_team_id?: string | null;
  team_alias?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  capabilities: BatchCapabilities;
}

export interface BatchJobSummary {
  total: number;
  queued: number;
  in_progress: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export interface BatchJobCosts {
  batch_id: string;
  total_provider_cost: number;
  total_billed_cost: number;
}

export interface BatchJobItem {
  item_id: string;
  line_number: number;
  custom_id?: string | null;
  status: string;
  attempts: number;
  provider_cost?: number | null;
  billed_cost?: number | null;
  last_error?: string | null;
  has_request_body?: boolean;
  has_response_body?: boolean;
  has_error_body?: boolean;
  has_usage?: boolean;
  request_body?: Record<string, unknown> | null;
  response_body?: Record<string, unknown> | null;
  error_body?: BatchItemError | null;
  usage?: Record<string, unknown> | null;
}

export interface BatchJobItemDetail extends BatchJobItem {
  batch_id: string;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface BatchJobDetail {
  batch_id: string;
  endpoint: string;
  status: string;
  model: string;
  execution_mode?: string | null;
  metadata?: Record<string, unknown> | null;
  total_items: number;
  completed_items: number;
  failed_items: number;
  cancelled_items: number;
  in_progress_items: number;
  total_provider_cost?: number | null;
  total_billed_cost?: number | null;
  created_by_api_key?: string | null;
  created_by_team_id?: string | null;
  team_alias?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  cancel_requested_at?: string | null;
  expires_at?: string | null;
  capabilities: BatchCapabilities;
  webhook_deliveries?: BatchWebhookDelivery[];
  items: Paginated<BatchJobItem>;
}

export interface CallableTargetListItem {
  callable_key: string;
  target_type: 'model' | 'route_group';
  binding_count: number;
  mode?: string | null;
  mode_conflict?: boolean;
}

export interface CallableTargetAccessGroupListItem {
  group_key: string;
  member_count: number;
  binding_count: number;
  members?: Array<{
    callable_key: string;
    target_type: 'model' | 'route_group';
  }>;
}

export interface AssetAccessTarget {
  callable_key: string;
  target_type: 'model' | 'route_group';
  selectable: boolean;
  selected: boolean;
  effective_visible: boolean;
  inherited_only: boolean;
  via_access_groups?: string[];
}

export interface AssetAccessGroup {
  group_key: string;
  selectable: boolean;
  selected: boolean;
  member_count: number;
  effective_visible: boolean;
  callable_keys?: string[];
}

export interface AssetVisibilityTarget {
  callable_key: string;
  target_type: 'model' | 'route_group';
  effective_visible: boolean;
  effective_enabled?: boolean;
  visibility_source?: string;
}

export interface AssetVisibilityResponse {
  organization_id?: string | null;
  team_id?: string | null;
  api_key_id?: string | null;
  user_id?: string | null;
  scope_policies?: {
    team?: 'inherit' | 'restrict';
    api_key?: 'inherit' | 'restrict';
    user?: 'inherit' | 'restrict';
  };
  callable_targets: {
    total: number;
    items: AssetVisibilityTarget[];
  };
  access_groups?: {
    total: number;
    items: AssetAccessGroup[];
    pagination: Pagination;
  };
}

export interface ScopedAssetAccess {
  scope_type: 'organization' | 'team' | 'api_key' | 'user';
  scope_id: string;
  organization_id?: string | null;
  team_id?: string | null;
  api_key_id?: string | null;
  user_id?: string | null;
  mode: 'grant' | 'inherit' | 'restrict';
  auto_follow_catalog?: boolean;
  selected_callable_keys: string[];
  selected_access_group_keys: string[];
  selectable_targets: AssetAccessTarget[];
  selectable_access_groups: AssetAccessGroup[];
  access_group_pagination?: Pagination;
  effective_targets: AssetAccessTarget[];
  summary: {
    selected_total: number;
    selectable_total: number;
    effective_total: number;
    selected_access_group_total?: number;
    selectable_access_group_total?: number;
  };
}

type AssetAccessGroupPageParams = {
  access_group_search?: string;
  access_group_limit?: number;
  access_group_offset?: number;
};

type AssetVisibilityParams = AssetAccessGroupPageParams & {
  user_id?: string;
  include_access_groups?: boolean;
};

type ScopedAssetAccessParams = AssetAccessGroupPageParams & {
  include_targets?: boolean;
};

export interface NamedCredential {
  credential_id: string;
  name: string;
  provider: string;
  connection_config: Record<string, unknown> | null;
  credentials_present: boolean;
  metadata?: Record<string, unknown> | null;
  created_by_account_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  usage_count?: number;
  linked_deployments?: Array<{ deployment_id: string; model_name: string }>;
  warnings?: string[];
  access?: ManagedAssetAccess;
}

export interface InlineCredentialGroup {
  fingerprint: string;
  provider: string;
  connection_config: Record<string, unknown> | null;
  credentials_present: boolean;
  deployment_count: number;
  deployments: Array<{ deployment_id: string; model_name: string }>;
}

export const callableTargets = {
  list: (params?: { search?: string; target_type?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<CallableTargetListItem>>(withQuery('/ui/api/callable-targets', params)),
  listAccessGroups: (params?: { search?: string; include_members?: boolean; limit?: number; offset?: number }) =>
    apiFetch<Paginated<CallableTargetAccessGroupListItem>>(withQuery('/ui/api/callable-target-access-groups', params)),
  listAll: async (params?: { search?: string; target_type?: string }) => {
    const limit = 500;
    let offset = 0;
    let items: CallableTargetListItem[] = [];
    while (true) {
      const page = await apiFetch<Paginated<CallableTargetListItem>>(
        withQuery('/ui/api/callable-targets', { ...(params || {}), limit, offset }),
      );
      items = items.concat(page.data || []);
      if (!page.pagination?.has_more) {
        break;
      }
      offset += limit;
    }
    return items;
  },
};

export interface AuditPayload {
  payload_id: string;
  event_id: string;
  kind: string;
  storage_mode: string;
  content_json: Record<string, unknown> | string | null;
  storage_uri: string | null;
  content_sha256: string | null;
  size_bytes: number | null;
  redacted: boolean;
  created_at: string | null;
}

export interface AuditEvent {
  event_id: string;
  occurred_at: string;
  organization_id: string | null;
  actor_type: string | null;
  actor_id: string | null;
  api_key: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  request_id: string | null;
  correlation_id: string | null;
  ip: string | null;
  user_agent: string | null;
  status: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  error_type: string | null;
  error_code: string | null;
  metadata: Record<string, unknown> | null;
  content_stored: boolean;
  prev_hash?: string | null;
  event_hash?: string | null;
  payloads?: AuditPayload[];
}

export interface AuditListResponse {
  events: AuditEvent[];
  pagination: Pagination;
}

export const health = {
  check: () => apiFetch<HealthResponse>('/health'),
};

export const spend = {
  featureStatus: (opts?: RequestInit) => apiFetch<SpendFeatureStatus>('/ui/api/spend/feature-status', opts),
  summary: (start_date?: string, end_date?: string, view?: SpendView, opts?: RequestInit) => {
    const qs = new URLSearchParams();
    if (start_date) qs.set('start_date', start_date);
    if (end_date) qs.set('end_date', end_date);
    if (view && view !== 'platform') qs.set('view', view);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return apiFetch<SpendSummary>(`/ui/api/spend/summary${suffix}`, opts);
  },
  timeSeries: (
    params: { start_date?: string; end_date?: string; interval: SpendBucket; view?: SpendView },
    opts?: RequestInit,
  ) => {
    const qs = new URLSearchParams({ group_by: 'day', interval: params.interval });
    if (params.start_date) qs.set('start_date', params.start_date);
    if (params.end_date) qs.set('end_date', params.end_date);
    if (params.view && params.view !== 'platform') qs.set('view', params.view);
    return apiFetch<SpendTimeSeriesReport>(`/ui/api/spend/report?${qs.toString()}`, opts);
  },
  providerReport: (
    params?: { start_date?: string; end_date?: string; limit?: number; view?: SpendView },
    opts?: RequestInit,
  ) => {
    const qs = new URLSearchParams({ group_by: 'provider', limit: String(params?.limit ?? 5) });
    if (params?.start_date) qs.set('start_date', params.start_date);
    if (params?.end_date) qs.set('end_date', params.end_date);
    if (params?.view && params.view !== 'platform') qs.set('view', params.view);
    return apiFetch<SpendGroupReport>(`/ui/api/spend/report?${qs.toString()}`, opts);
  },
  report: (
    group_by: 'model' | 'provider' | 'day' | 'user' | 'team',
    start_date?: string,
    end_date?: string,
    opts?: RequestInit,
  ) => {
    const qs = new URLSearchParams({ group_by });
    if (start_date) qs.set('start_date', start_date);
    if (end_date) qs.set('end_date', end_date);
    return apiFetch<unknown>(`/ui/api/spend/report?${qs.toString()}`, opts);
  },
  groupedReport: (
    group_by: SpendGroupBy,
    params?: {
      start_date?: string;
      end_date?: string;
      search?: string;
      sort_by?: SpendUsageMetric;
      scope_type?: SpendUsageDimension;
      scope_id?: string;
      scope_unassigned?: boolean;
      limit?: number;
      offset?: number;
      view?: SpendView;
    },
    opts?: RequestInit,
  ) => {
    const qs = new URLSearchParams({ group_by });
    if (params?.start_date) qs.set('start_date', params.start_date);
    if (params?.end_date) qs.set('end_date', params.end_date);
    if (params?.search) qs.set('search', params.search);
    if (params?.sort_by) qs.set('sort_by', params.sort_by);
    if (params?.scope_type) qs.set('scope_type', params.scope_type);
    if (params?.scope_id) qs.set('scope_id', params.scope_id);
    if (params?.scope_unassigned) qs.set('scope_unassigned', 'true');
    if (params?.limit != null) qs.set('limit', String(params.limit));
    if (params?.offset != null) qs.set('offset', String(params.offset));
    if (params?.view && params.view !== 'platform') qs.set('view', params.view);
    return apiFetch<SpendGroupReport>(`/ui/api/spend/report?${qs.toString()}`, opts);
  },
  logs: (params?: Record<string, string>, opts?: RequestInit) => {
    const qs = new URLSearchParams(params || {});
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return apiFetch<SpendLogsResponse>(`/ui/api/logs${suffix}`, opts);
  },
};

export const audit = {
  list: (params?: Record<string, unknown>) =>
    apiFetch<AuditListResponse>(withQuery('/ui/api/audit/events', params)),
  get: (eventId: string) =>
    apiFetch<AuditEvent>(`/ui/api/audit/events/${encodeURIComponent(eventId)}`),
  timeline: (params: { request_id?: string; correlation_id?: string }) =>
    apiFetch<{ events: AuditEvent[] }>(withQuery('/ui/api/audit/timeline', params as Record<string, unknown>)),
  exportUrl: (params?: Record<string, unknown>) => withQuery('/ui/api/audit/export', params),
};

export { models } from './api/models';
export type {
  DeploymentHealth,
  ModelDeleteResponse,
  ModelDeploymentDetail,
  ModelInfo,
  ModelListResponse,
  ModelMutationResponse,
  ModelRuntimeParams,
  ModelWritePayload,
  ProviderHealthStatus,
  ProviderHealthSummary,
  ProviderHealthSummaryRow,
  ProviderModelDiscoveryPayload,
  ProviderModelDiscoveryResponse,
  ProviderModelOption,
  ProviderPreset,
} from './api/models';

export const namedCredentials = {
  list: (params?: { provider?: string }) =>
    apiFetch<{ data: NamedCredential[] }>(withQuery('/ui/api/named-credentials', params)),
  get: (credentialId: string) =>
    apiFetch<NamedCredential>(`/ui/api/named-credentials/${encodeURIComponent(credentialId)}`),
  create: (payload: {
    name: string;
    provider: string;
    connection_config: Record<string, unknown>;
    metadata?: Record<string, unknown>;
    access?: ManagedAssetAccessInput;
  }) => apiFetch<NamedCredential>('/ui/api/named-credentials', { method: 'POST', json: payload }),
  update: (credentialId: string, payload: {
    name?: string;
    provider?: string;
    connection_config?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  }) => apiFetch<NamedCredential>(`/ui/api/named-credentials/${encodeURIComponent(credentialId)}`, { method: 'PUT', json: payload }),
  delete: (credentialId: string) =>
    apiFetch<{ deleted: boolean; credential_id: string }>(`/ui/api/named-credentials/${encodeURIComponent(credentialId)}`, { method: 'DELETE' }),
  inlineReport: () =>
    apiFetch<{ data: InlineCredentialGroup[] }>('/ui/api/named-credentials/inline-report'),
  convertInlineGroup: (payload: {
    fingerprint: string;
    name: string;
    provider: string;
    deployment_ids: string[];
    metadata?: Record<string, unknown>;
  }) => apiFetch<{
    credential: NamedCredential;
    converted_deployments: Array<{ deployment_id: string; model_name: string }>;
    warnings: string[];
  }>('/ui/api/named-credentials/convert-inline-group', { method: 'POST', json: payload }),
};

export { routeGroups } from './api/routeGroups';
export type {
  DeleteRouteGroupResponse,
  MutationWarnings,
  RollbackRoutePolicyResponse,
  RouteGroup,
  RouteGroupBinding,
  RouteGroupListResponse,
  RouteGroupMember,
  RouteGroupMemberDetail,
  RouteGroupMemberMutationResponse,
  RouteGroupMemberWritePayload,
  RouteGroupMutationResponse,
  RouteGroupWritePayload,
  RoutePolicy,
  RoutePolicyContextDocument,
  RoutePolicyCurrentResponse,
  RoutePolicyDocument,
  RoutePolicyHistoryResponse,
  RoutePolicyMemberDocument,
  RoutePolicyMutationResponse,
  RoutePolicySelector,
  RoutePolicySelectorLane,
  RoutePolicySimulationAttempt,
  RoutePolicySimulationOutcome,
  RoutePolicySimulationRequest,
  RoutePolicySimulationResponse,
  RoutePolicySimulationSelection,
  RoutePolicyValidationResponse,
  StoredRoutePolicyDocument,
} from './api/routeGroups';

export * from './api/promptRegistry';

export interface SettingsResponse {
  router_settings?: {
    routing_strategy?: string;
    num_retries?: number;
    timeout?: number;
    cooldown_time?: number;
    retry_after?: number;
    allowed_fails?: number;
  };
  general_settings?: {
    instance_name?: string;
    cache_enabled?: boolean;
    cache_backend?: string;
    cache_ttl?: number;
    background_health_checks?: boolean;
    health_check_interval?: number;
    log_level?: string;
    tier_policy_mode?: string;
    ui_branding?: Partial<UIBrandingResponse>;
  };
  deltallm_settings?: {
    fallbacks?: Array<Record<string, string[]>>;
    context_window_fallbacks?: Array<Record<string, string[]>>;
    content_policy_fallbacks?: Array<Record<string, string[]>>;
  };
}

export const settings = {
  get: () => apiFetch<SettingsResponse>('/ui/api/settings'),
  update: (payload: object) =>
    apiFetch<SettingsResponse>('/ui/api/settings', { method: 'PUT', json: payload }),
};

export interface UIBrandingResponse {
  instance_name: string;
  logo_mark_url: string | null;
  logo_full_url: string | null;
  favicon_url: string | null;
  primary_color: string;
  secondary_color: string;
  menu_hover_color: string;
}

export type UIBrandingAssetKind = 'logo_mark' | 'logo_full' | 'favicon';

export type UIBrandingUpdate = Pick<
  UIBrandingResponse,
  'instance_name' | 'primary_color' | 'secondary_color' | 'menu_hover_color'
>;

export const branding = {
  get: (signal?: AbortSignal) => apiFetch<UIBrandingResponse>('/ui/api/branding', { signal }),
  update: (payload: UIBrandingUpdate) => apiFetch<UIBrandingResponse>('/ui/api/branding', {
    method: 'PUT',
    json: payload,
  }),
  uploadAsset: (asset: UIBrandingAssetKind, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return apiFetch<UIBrandingResponse>(`/ui/api/branding/assets/${asset}`, {
      method: 'PUT',
      body,
    });
  },
  deleteAsset: (asset: UIBrandingAssetKind) => apiFetch<UIBrandingResponse>(
    `/ui/api/branding/assets/${asset}`,
    { method: 'DELETE' },
  ),
};

export { tiers, tierCapacity } from './api/tiers';

export const organizations = {
  ...organizationRecordsApi,
  members: (orgId: string, signal?: AbortSignal) => apiFetch<Record<string, unknown>[]>(
    `/ui/api/organizations/${encodeURIComponent(orgId)}/members`,
    { signal },
  ),
  memberCandidates: (
    orgId: string,
    params?: { search?: string; limit?: number },
    signal?: AbortSignal,
  ) => apiFetch<Record<string, unknown>[]>(
    withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/member-candidates`, params),
    { signal },
  ),
  addMember: (orgId: string, payload: object) =>
    apiFetch<Record<string, unknown>>(`/ui/api/organizations/${encodeURIComponent(orgId)}/members`, { method: 'POST', json: payload }),
  removeMember: (orgId: string, membershipId: string) =>
    apiFetch<{ deleted: boolean }>(`/ui/api/organizations/${encodeURIComponent(orgId)}/members/${encodeURIComponent(membershipId)}`, { method: 'DELETE' }),
  teams: (orgId: string, signal?: AbortSignal) => apiFetch<TeamRecord[]>(
    `/ui/api/organizations/${encodeURIComponent(orgId)}/teams`,
    { signal },
  ),
  assetVisibility: (
    orgId: string,
    params?: AssetVisibilityParams,
    signal?: AbortSignal,
  ) => apiFetch<AssetVisibilityResponse>(
    withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-visibility`, params),
    { signal },
  ),
  assetAccess: (
    orgId: string,
    params?: ScopedAssetAccessParams,
    signal?: AbortSignal,
  ) => apiFetch<ScopedAssetAccess>(
    withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-access`, params),
    { signal },
  ),
  updateAssetAccess: (orgId: string, payload: { mode?: string; selected_callable_keys: string[]; selected_access_group_keys?: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-access`, { method: 'PUT', json: payload }),
  tierAssignments: (
    orgId: string,
    params?: { enabled?: boolean | string },
    signal?: AbortSignal,
  ) => apiFetch<{ data: OrganizationTierAssignment[] }>(
    withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-assignments`, params),
    { signal },
  ),
  createTierAssignment: (orgId: string, payload: OrganizationTierAssignmentPayload) =>
    apiFetch<OrganizationTierAssignment>(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-assignments`, { method: 'POST', json: payload }),
  updateTierAssignment: (orgId: string, assignmentId: string, payload: Partial<OrganizationTierAssignmentPayload>) =>
    apiFetch<OrganizationTierAssignment>(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-assignments/${encodeURIComponent(assignmentId)}`, { method: 'PATCH', json: payload }),
  deleteTierAssignment: (orgId: string, assignmentId: string) =>
    apiFetch<{ deleted: boolean; assignment_id: string; organization_id: string }>(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-assignments/${encodeURIComponent(assignmentId)}`, { method: 'DELETE' }),
  tierPolicyPreview: (orgId: string, signal?: AbortSignal) =>
    apiFetch<OrganizationTierPolicyPreview>(
      `/ui/api/organizations/${encodeURIComponent(orgId)}/tier-policy-preview`,
      { signal },
    ),
  simulateTierPolicy: (orgId: string, payload: TierPolicySimulationPayload) =>
    apiFetch<TierPolicySimulation>(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-policy/simulate`, { method: 'POST', json: payload }),
};

export interface SelfServicePolicy {
  self_service_keys_enabled: boolean;
  self_service_max_keys_per_user: number | null;
  self_service_budget_ceiling: number | null;
  self_service_require_expiry: boolean;
  self_service_max_expiry_days: number | null;
}

export const teams = {
  list: (
    params?: { search?: string; organization_id?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => apiFetch<Paginated<TeamRecord>>(withQuery('/ui/api/teams', params), { signal }),
  get: (teamId: string, signal?: AbortSignal) => apiFetch<TeamRecord>(
    `/ui/api/teams/${encodeURIComponent(teamId)}`,
    { signal },
  ),
  getSelfServicePolicy: async (teamId: string): Promise<SelfServicePolicy> => {
    const t = await apiFetch<TeamRecord>(`/ui/api/teams/${encodeURIComponent(teamId)}`);
    return {
      self_service_keys_enabled: !!t.self_service_keys_enabled,
      self_service_max_keys_per_user: t.self_service_max_keys_per_user ?? null,
      self_service_budget_ceiling: t.self_service_budget_ceiling ?? null,
      self_service_require_expiry: !!t.self_service_require_expiry,
      self_service_max_expiry_days: t.self_service_max_expiry_days ?? null,
    };
  },
  create: (payload: object, signal?: AbortSignal) => apiFetch<TeamRecord>('/ui/api/teams', { method: 'POST', json: payload, signal }),
  update: (teamId: string, payload: object, signal?: AbortSignal) => apiFetch<TeamRecord>(`/ui/api/teams/${encodeURIComponent(teamId)}`, { method: 'PUT', json: payload, signal }),
  delete: (teamId: string) => apiFetch<{ deleted: boolean }>(`/ui/api/teams/${encodeURIComponent(teamId)}`, { method: 'DELETE' }),
  members: (teamId: string) => apiFetch<TeamMemberRecord[]>(`/ui/api/teams/${encodeURIComponent(teamId)}/members`),
  memberCandidates: (teamId: string, params?: { search?: string; limit?: number }) =>
    apiFetch<TeamMemberCandidate[]>(withQuery(`/ui/api/teams/${encodeURIComponent(teamId)}/member-candidates`, params)),
  addMember: (teamId: string, payload: object) => apiFetch<Record<string, unknown>>(`/ui/api/teams/${encodeURIComponent(teamId)}/members`, { method: 'POST', json: payload }),
  removeMember: (teamId: string, userId: string) =>
    apiFetch<{ deleted: boolean }>(`/ui/api/teams/${encodeURIComponent(teamId)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  assetVisibility: (teamId: string, params?: AssetVisibilityParams) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/teams/${encodeURIComponent(teamId)}/asset-visibility`, params)),
  assetAccess: (teamId: string, params?: ScopedAssetAccessParams) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/teams/${encodeURIComponent(teamId)}/asset-access`, params)),
  updateAssetAccess: (teamId: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; selected_access_group_keys?: string[]; select_all_selectable?: boolean }, signal?: AbortSignal) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/teams/${encodeURIComponent(teamId)}/asset-access`, { method: 'PUT', json: payload, signal }),
};

export const serviceAccounts = {
  list: (params?: { team_id?: string; search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<ServiceAccount>>(withQuery('/ui/api/service-accounts', params)),
  create: (payload: { team_id: string; name: string; description?: string }) =>
    apiFetch<ServiceAccount>('/ui/api/service-accounts', { method: 'POST', json: payload }),
};

export const mcpServers = {
  list: (params?: { search?: string; enabled?: boolean; limit?: number; offset?: number }) =>
    apiFetch<Paginated<MCPServer>>(withQuery('/ui/api/mcp-servers', params)),
  get: (serverId: string) => apiFetch<MCPServerDetail>(`/ui/api/mcp-servers/${encodeURIComponent(serverId)}`),
  operations: (serverId: string, params?: { window_hours?: number; top_tools_limit?: number; failures_limit?: number }) =>
    apiFetch<MCPServerOperations>(withQuery(`/ui/api/mcp-servers/${encodeURIComponent(serverId)}/operations`, params)),
  create: (payload: object) => apiFetch<MCPServer>('/ui/api/mcp-servers', { method: 'POST', json: payload }),
  update: (serverId: string, payload: object) =>
    apiFetch<MCPServer>(`/ui/api/mcp-servers/${encodeURIComponent(serverId)}`, { method: 'PATCH', json: payload }),
  delete: (serverId: string) =>
    apiFetch<{ deleted: boolean; mcp_server_id: string }>(`/ui/api/mcp-servers/${encodeURIComponent(serverId)}`, { method: 'DELETE' }),
  refreshCapabilities: (serverId: string) =>
    apiFetch<{ server: MCPServer; tools: MCPNamespacedTool[] }>(
      `/ui/api/mcp-servers/${encodeURIComponent(serverId)}/refresh-capabilities`,
      { method: 'POST' }
    ),
  healthCheck: (serverId: string) =>
    apiFetch<{ server: MCPServer; health: { status: string; latency_ms: number; error?: string | null } }>(
      `/ui/api/mcp-servers/${encodeURIComponent(serverId)}/health-check`,
      { method: 'POST' }
    ),
  listBindings: (params?: { server_id?: string; scope_type?: string; scope_id?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<MCPBinding>>(withQuery('/ui/api/mcp-bindings', params)),
  upsertBinding: (payload: object) => apiFetch<MCPBinding>('/ui/api/mcp-bindings', { method: 'POST', json: payload }),
  deleteBinding: (bindingId: string) =>
    apiFetch<{ deleted: boolean; mcp_binding_id: string }>(`/ui/api/mcp-bindings/${encodeURIComponent(bindingId)}`, { method: 'DELETE' }),
  listToolPolicies: (params?: { server_id?: string; scope_type?: string; scope_id?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<MCPToolPolicy>>(withQuery('/ui/api/mcp-tool-policies', params)),
  upsertToolPolicy: (payload: object) =>
    apiFetch<MCPToolPolicy>('/ui/api/mcp-tool-policies', { method: 'POST', json: payload }),
  deleteToolPolicy: (policyId: string) =>
    apiFetch<{ deleted: boolean; mcp_tool_policy_id: string }>(`/ui/api/mcp-tool-policies/${encodeURIComponent(policyId)}`, { method: 'DELETE' }),
  listApprovalRequests: (params?: { server_id?: string; status?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<MCPApprovalRequest>>(withQuery('/ui/api/mcp-approval-requests', params)),
  decideApprovalRequest: (approvalRequestId: string, payload: { status: 'approved' | 'rejected'; decision_comment?: string }) =>
    apiFetch<MCPApprovalRequest>(`/ui/api/mcp-approval-requests/${encodeURIComponent(approvalRequestId)}/decision`, { method: 'POST', json: payload }),
};

export const keys = {
  list: (params?: { search?: string; team_id?: string; my_keys?: boolean; limit?: number; offset?: number }) =>
    apiFetch<Paginated<ApiKey>>(withQuery('/ui/api/keys', params)),
  create: (payload: object, signal?: AbortSignal) => apiFetch<ApiKey & { raw_key: string }>('/ui/api/keys', { method: 'POST', json: payload, signal }),
  update: (tokenHash: string, payload: object, signal?: AbortSignal) =>
    apiFetch<ApiKey>(`/ui/api/keys/${encodeURIComponent(tokenHash)}`, { method: 'PUT', json: payload, signal }),
  regenerate: (tokenHash: string) => apiFetch<{ token: string; raw_key: string }>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/regenerate`, { method: 'POST' }),
  revoke: (tokenHash: string) => apiFetch<KeyRemovalResult & { revoked: boolean }>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/revoke`, { method: 'POST' }),
  delete: (tokenHash: string) => apiFetch<KeyRemovalResult & { deleted: boolean }>(`/ui/api/keys/${encodeURIComponent(tokenHash)}`, { method: 'DELETE' }),
  assetVisibility: (tokenHash: string, params?: AssetVisibilityParams) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-visibility`, params)),
  assetAccess: (tokenHash: string, params?: ScopedAssetAccessParams) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-access`, params)),
  updateAssetAccess: (tokenHash: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; selected_access_group_keys?: string[]; select_all_selectable?: boolean }, signal?: AbortSignal) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-access`, { method: 'PUT', json: payload, signal }),
};

export const users = {
  updateOutputTpm: updateRuntimeOutputTpm,
  assetVisibility: (userId: string, params?: Omit<AssetVisibilityParams, 'user_id'>) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/users/${encodeURIComponent(userId)}/asset-visibility`, params)),
  assetAccess: (userId: string, params?: ScopedAssetAccessParams) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/users/${encodeURIComponent(userId)}/asset-access`, params)),
  updateAssetAccess: (userId: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; selected_access_group_keys?: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/users/${encodeURIComponent(userId)}/asset-access`, { method: 'PUT', json: payload }),
};

export const batches = {
  featureStatus: () => apiFetch<BatchFeatureStatus>('/ui/api/batches/feature-status'),
  list: (params?: { search?: string; status?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<BatchJobListItem>>(withQuery('/ui/api/batches', params)),
  summary: () => apiFetch<BatchJobSummary>('/ui/api/batches/summary'),
  get: (batchId: string, params?: { items_limit?: number; items_offset?: number; after_line_number?: number | null }) =>
    apiFetch<BatchJobDetail>(withQuery(`/ui/api/batches/${encodeURIComponent(batchId)}`, params)),
  webhookDeliveries: (batchId: string) =>
    apiFetch<BatchWebhookDeliveryList>(
      `/ui/api/batches/${encodeURIComponent(batchId)}/webhook-deliveries`,
    ),
  costs: (batchId: string) =>
    apiFetch<BatchJobCosts>(`/ui/api/batches/${encodeURIComponent(batchId)}/costs`),
  getItem: (batchId: string, itemId: string) =>
    apiFetch<BatchJobItemDetail>(`/ui/api/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}`),
  cancel: (batchId: string) => apiFetch<{ batch_id: string; status: string }>(`/ui/api/batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' }),
  replayWebhook: (batchId: string, eventId: string) =>
    apiFetch<BatchWebhookReplayResponse>(
      `/ui/api/batches/${encodeURIComponent(batchId)}/webhook-deliveries/${encodeURIComponent(eventId)}/replay`,
      { method: 'POST' },
    ),
};

export type GuardrailMode = 'pre_call' | 'post_call';
export type GuardrailAction = 'block' | 'log';

export interface GuardrailPresetFieldOption {
  value: string;
  label: string;
  disabled?: boolean;
  description?: string;
}

export interface GuardrailPresetField {
  key: string;
  label: string;
  input: 'boolean' | 'number' | 'text' | 'multiselect' | 'secret';
  default_value: string | number | boolean | string[];
  help_text?: string;
  placeholder?: string;
  min?: number;
  max?: number;
  step?: number;
  advanced?: boolean;
  options?: GuardrailPresetFieldOption[];
}

export interface GuardrailPreset {
  preset_id: string;
  label: string;
  description: string;
  type_label: string;
  class_path: string;
  supported_modes: GuardrailMode[];
  supported_actions: GuardrailAction[];
  fields: GuardrailPresetField[];
}

export interface GuardrailEditorConfig {
  preset_id: string | null;
  is_custom: boolean;
  class_path: string;
  mode: GuardrailMode;
  default_action: GuardrailAction;
  default_on: boolean;
  field_values: Record<string, unknown>;
  additional_params: Record<string, unknown>;
}

export interface GuardrailRecord {
  guardrail_name: string;
  type: string;
  preset_id: string | null;
  is_custom: boolean;
  class_path?: string | null;
  mode: GuardrailMode;
  enabled: boolean;
  default_action: GuardrailAction;
  threshold: number;
  editor: GuardrailEditorConfig;
  deltallm_params: Record<string, unknown>;
}

export interface GuardrailCatalog {
  presets: GuardrailPreset[];
  supported_modes: GuardrailMode[];
  supported_actions: GuardrailAction[];
  capabilities: {
    presidio: {
      engine_mode: 'full' | 'regex_fallback';
      fallback_supported_entities: string[];
    };
  };
}

export interface ScopedGuardrailResponse {
  guardrails_config?: {
    mode?: 'inherit' | 'override';
    include?: string[];
    exclude?: string[];
  } | null;
  available_guardrails?: string[];
}

export const guardrails = {
  list: async () => {
    const res = await apiFetch<{ guardrails: GuardrailRecord[] }>('/ui/api/guardrails');
    return res.guardrails || [];
  },
  catalog: () => apiFetch<GuardrailCatalog>('/ui/api/guardrails/catalog'),
  update: async (payload: { guardrails: Array<{ guardrail_name: string; deltallm_params: Record<string, unknown> }> }) => {
    const res = await apiFetch<{ guardrails: GuardrailRecord[] }>('/ui/api/guardrails', { method: 'PUT', json: payload });
    return res.guardrails || [];
  },
  getScoped: (scope: 'organization' | 'team' | 'key', entityId: string) =>
    apiFetch<ScopedGuardrailResponse>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`),
  updateScoped: (scope: 'organization' | 'team' | 'key', entityId: string, payload: object) =>
    apiFetch<ScopedGuardrailResponse>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`, { method: 'PUT', json: payload }),
  deleteScoped: (scope: 'organization' | 'team' | 'key', entityId: string) =>
    apiFetch<{ deleted: boolean }>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`, { method: 'DELETE' }),
};

export interface RBACAccount {
  account_id: string;
  email: string;
  role: string;
  is_active: boolean;
  force_password_change?: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  updated_at?: string;
}

export interface OrgMembership {
  membership_id: string;
  account_id: string;
  organization_id: string;
  organization_name?: string | null;
  role: string;
  self_registration_default?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface TeamMembership {
  membership_id: string;
  account_id: string;
  team_id: string;
  team_alias?: string | null;
  organization_id?: string | null;
  role: string;
  self_service_keys_enabled?: boolean;
  self_service_max_keys_per_user?: number | null;
  self_service_budget_ceiling?: number | null;
  self_service_require_expiry?: boolean;
  self_service_max_expiry_days?: number | null;
  self_registration_default?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface RuntimeUserProfile {
  user_id: string;
  user_email?: string | null;
  team_id?: string | null;
  team_alias?: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  max_budget?: number | null;
  soft_budget?: number | null;
  spend?: number | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  output_tpm_limit?: number | null;
  rph_limit?: number | null;
  rpd_limit?: number | null;
  tpd_limit?: number | null;
  blocked?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  self_registration_default?: boolean;
}

export interface PrincipalSelfRegistration {
  is_self_registered: boolean;
  seeded_user: boolean;
  seeded_team: boolean;
  seeded_organization: boolean;
  sandbox_team_id?: string | null;
  sandbox_organization_id?: string | null;
}

export interface Principal extends RBACAccount {
  runtime_user_id?: string | null;
  runtime_user?: RuntimeUserProfile | null;
  self_registration?: PrincipalSelfRegistration | null;
  self_service_policy?: (SelfServicePolicy & { team_id?: string | null; team_alias?: string | null }) | null;
  organization_memberships: OrgMembership[];
  team_memberships: TeamMembership[];
}

export interface Invitation {
  invitation_id: string;
  account_id: string;
  email: string;
  status: 'pending' | 'sent' | 'accepted' | 'cancelled' | 'expired';
  invite_scope_type: 'organization' | 'team' | 'mixed';
  expires_at: string;
  accepted_at?: string | null;
  cancelled_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  invited_by_account_id?: string | null;
  inviter_email?: string | null;
  message_email_id?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface ProvisionPersonResponse {
  mode: 'invite_email' | 'create_account';
  account_id?: string;
  invitation_id?: string;
  email: string;
  role?: string;
  is_active?: boolean;
  status?: string;
  invite_scope_type?: 'organization' | 'team' | 'mixed';
  organization_id?: string | null;
  team_id?: string | null;
  scope_type?: 'none' | 'organization' | 'team';
}

export interface PrincipalSummary {
  total_accounts: number;
  active_accounts: number;
  platform_admins: number;
  mfa_enabled_accounts: number;
  organization_memberships: number;
  team_memberships: number;
}

export const rbac = {
  principals: {
    list: (params?: { search?: string; limit?: number; offset?: number }) =>
      apiFetch<Paginated<Principal>>(withQuery('/ui/api/principals', params)),
    summary: () => apiFetch<PrincipalSummary>('/ui/api/principals/summary'),
  },
  accounts: {
    upsert: (payload: object) => apiFetch<unknown>('/ui/api/rbac/accounts', { method: 'POST', json: payload }),
    delete: (accountId: string) =>
      apiFetch<unknown>(`/ui/api/rbac/accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
  },
  provisionPerson: (payload: {
    email: string;
    mode: 'invite_email' | 'create_account';
    platform_role?: string;
    password?: string;
    is_active?: boolean;
    organization_id?: string;
    organization_role?: string;
    team_id?: string;
    team_role?: string;
  }) => apiFetch<ProvisionPersonResponse>('/ui/api/rbac/provision', { method: 'POST', json: payload }),
  orgMemberships: {
    list: () => apiFetch<OrgMembership[]>('/ui/api/rbac/organization-memberships'),
    upsert: (payload: object) => apiFetch<unknown>('/ui/api/rbac/organization-memberships', { method: 'POST', json: payload }),
    delete: (membershipId: string) =>
      apiFetch<unknown>(`/ui/api/rbac/organization-memberships/${encodeURIComponent(membershipId)}`, { method: 'DELETE' }),
  },
  teamMemberships: {
    list: () => apiFetch<TeamMembership[]>('/ui/api/rbac/team-memberships'),
    upsert: (payload: object) => apiFetch<unknown>('/ui/api/rbac/team-memberships', { method: 'POST', json: payload }),
    delete: (membershipId: string) =>
      apiFetch<unknown>(`/ui/api/rbac/team-memberships/${encodeURIComponent(membershipId)}`, { method: 'DELETE' }),
  },
};

export const invitations = {
  list: (params?: { status?: Invitation['status'] | 'active'; search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<Invitation>>(withQuery('/ui/api/invitations', params)),
  create: (payload: {
    email: string;
    organization_id?: string;
    organization_role?: string;
    team_id?: string;
    team_role?: string;
  }) => apiFetch<Invitation>('/ui/api/invitations', { method: 'POST', json: payload }),
  resend: (invitationId: string) =>
    apiFetch<Invitation>(`/ui/api/invitations/${encodeURIComponent(invitationId)}/resend`, { method: 'POST' }),
  cancel: (invitationId: string) =>
    apiFetch<{ cancelled: boolean; invitation_id: string }>(`/ui/api/invitations/${encodeURIComponent(invitationId)}/cancel`, { method: 'POST' }),
};

export { auth } from './api/auth';
export type { AuthSsoConfig, SelfRegistrationPublicConfig } from './api/auth';
