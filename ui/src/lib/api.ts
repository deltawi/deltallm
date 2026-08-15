export class ApiError extends Error {
  status: number;
  detail?: unknown;
  retryAfterSeconds?: number;

  constructor(message: string, status: number, detail?: unknown, retryAfterSeconds?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function buildHeaders(init?: HeadersInit, body?: BodyInit | null): HeadersInit {
  const headers = new Headers(init);
  if (!(body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  return headers;
}

async function parseErrorDetail(res: Response): Promise<unknown> {
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    try {
      return await res.json();
    } catch {
      return undefined;
    }
  }
  try {
    return await res.text();
  } catch {
    return undefined;
  }
}

function errorMessage(status: number, detail: unknown): string {
  if (detail && typeof detail === 'object' && 'detail' in (detail as any)) {
    const d = (detail as any).detail;
    if (typeof d === 'string' && d.trim()) return d;
  }
  if (typeof detail === 'string' && detail.trim()) return detail;
  return `Request failed (${status})`;
}

async function apiFetch<T>(path: string, opts?: RequestInit & { json?: unknown }): Promise<T> {
  const body = opts && 'json' in opts ? JSON.stringify((opts as any).json ?? null) : opts?.body;
  const res = await fetch(path, {
    credentials: 'include',
    ...opts,
    headers: buildHeaders(opts?.headers, body),
    body,
  });

  if (!res.ok) {
    const detail = await parseErrorDetail(res);
    const rawRetryAfter = res.headers.get('retry-after');
    const retryAfterSeconds = rawRetryAfter && /^\d+$/.test(rawRetryAfter)
      ? Number.parseInt(rawRetryAfter, 10)
      : undefined;
    throw new ApiError(errorMessage(res.status, detail), res.status, detail, retryAfterSeconds);
  }

  if (res.status === 204) return undefined as T;

  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) return (await res.text()) as unknown as T;
  return (await res.json()) as T;
}

export function reportingRequestInit(signal: AbortSignal, forceRefresh = false): RequestInit {
  return forceRefresh
    ? { signal, headers: { 'Cache-Control': 'no-cache' } }
    : { signal };
}

export interface Pagination {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  after_line_number?: number | null;
  next_after_line_number?: number | null;
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

export interface Paginated<T> {
  data: T[];
  pagination: Pagination;
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

export type ProviderHealthStatus = 'healthy' | 'degraded' | 'down';

export interface ProviderHealthSummaryRow {
  provider: string;
  models: number;
  healthy_models: number;
  unhealthy_models: number;
  status: ProviderHealthStatus;
}

export interface ProviderHealthSummary {
  total_models: number;
  providers: ProviderHealthSummaryRow[];
  summary: {
    total_providers: number;
    active_providers: number;
    down_providers: number;
  };
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
  rph_limit: number | null;
  rpd_limit: number | null;
  tpd_limit: number | null;
  expires: string | null;
  created_at?: string | null;
  updated_at?: string | null;
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
  error_body?: Record<string, unknown> | null;
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

export interface ProviderPreset {
  provider: string;
  api_base: string | null;
  compat: string;
  supported_modes: string[];
}

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
}

export interface InlineCredentialGroup {
  fingerprint: string;
  provider: string;
  connection_config: Record<string, unknown> | null;
  credentials_present: boolean;
  deployment_count: number;
  deployments: Array<{ deployment_id: string; model_name: string }>;
}

export interface ProviderModelOption {
  id: string;
  label: string;
  provider: string;
  source: 'catalog' | 'provider_api' | 'catalog+provider_api';
  supported_modes: string[];
  known_metadata: Record<string, number | null> | null;
}

export interface ProviderModelDiscoveryPayload {
  provider: string;
  mode?: string | null;
  named_credential_id?: string | null;
  api_key?: string | null;
  api_base?: string | null;
  api_version?: string | null;
  auth_header_name?: string | null;
  auth_header_format?: string | null;
}

export interface ProviderModelDiscoveryResponse {
  data: ProviderModelOption[];
  warnings: string[];
}

export interface DeploymentHealth {
  healthy: boolean;
  in_cooldown: boolean;
  consecutive_failures: number;
  last_error: string | null;
  last_error_at: number | null;
  last_success_at: number | null;
}

export interface ModelDeploymentDetail {
  deployment_id: string;
  model_name: string;
  provider: string;
  mode?: string;
  credential_source?: 'inline' | 'named';
  named_credential_id?: string | null;
  named_credential_name?: string | null;
  inline_credentials_present?: boolean;
  connection_summary?: {
    api_base?: string | null;
    api_version?: string | null;
    region?: string | null;
    auth_header_name?: string | null;
    custom_auth_label?: string | null;
  };
  healthy?: boolean;
  health?: DeploymentHealth;
  deltallm_params: Record<string, any>;
  model_info: Record<string, any>;
}

export const callableTargets = {
  list: (params?: { search?: string; target_type?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<CallableTargetListItem>>(withQuery('/ui/api/callable-targets', params as any)),
  listAccessGroups: (params?: { search?: string; include_members?: boolean; limit?: number; offset?: number }) =>
    apiFetch<Paginated<CallableTargetAccessGroupListItem>>(withQuery('/ui/api/callable-target-access-groups', params as any)),
  listAll: async (params?: { search?: string; target_type?: string }) => {
    const limit = 500;
    let offset = 0;
    let items: CallableTargetListItem[] = [];
    while (true) {
      const page = await apiFetch<Paginated<CallableTargetListItem>>(
        withQuery('/ui/api/callable-targets', { ...(params || {}), limit, offset } as any),
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

function withQuery(path: string, params?: Record<string, unknown>): string {
  if (!params) return path;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    const s = String(v);
    if (!s.trim()) continue;
    qs.set(k, s);
  }
  const suffix = qs.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export const health = {
  check: () => apiFetch<any>('/health'),
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
    return apiFetch<any>(`/ui/api/spend/report?${qs.toString()}`, opts);
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

export const models = {
  list: (params?: { search?: string; provider?: string; mode?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/models', params as any)),
  providerHealthSummary: () => apiFetch<ProviderHealthSummary>('/ui/api/models/provider-health-summary'),
  providerPresets: () => apiFetch<{ data: ProviderPreset[] }>('/ui/api/provider-presets'),
  discoverProviderModels: (payload: ProviderModelDiscoveryPayload) =>
    apiFetch<ProviderModelDiscoveryResponse>('/ui/api/provider-models/discover', { method: 'POST', json: payload }),
  get: (deploymentId: string) => apiFetch<ModelDeploymentDetail>(`/ui/api/models/${encodeURIComponent(deploymentId)}`),
  checkHealth: (deploymentId: string) =>
    apiFetch<{ deployment_id: string; healthy: boolean; health: DeploymentHealth; message: string; status_code?: number | null; checked_at: number }>(
      `/ui/api/models/${encodeURIComponent(deploymentId)}/health-check`,
      { method: 'POST' },
    ),
  create: (payload: any) => apiFetch<any>('/ui/api/models', { method: 'POST', json: payload }),
  update: (deploymentId: string, payload: any) =>
    apiFetch<any>(`/ui/api/models/${encodeURIComponent(deploymentId)}`, { method: 'PUT', json: payload }),
  delete: (deploymentId: string) => apiFetch<any>(`/ui/api/models/${encodeURIComponent(deploymentId)}`, { method: 'DELETE' }),
};

export const namedCredentials = {
  list: (params?: { provider?: string }) =>
    apiFetch<{ data: NamedCredential[] }>(withQuery('/ui/api/named-credentials', params as any)),
  get: (credentialId: string) =>
    apiFetch<NamedCredential>(`/ui/api/named-credentials/${encodeURIComponent(credentialId)}`),
  create: (payload: {
    name: string;
    provider: string;
    connection_config: Record<string, unknown>;
    metadata?: Record<string, unknown>;
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
  }>('/ui/api/named-credentials/convert-inline-group', { method: 'POST', json: payload }),
};

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
  status: string;
  policy_json: Record<string, unknown>;
  published_at: string | null;
  published_by: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export const routeGroups = {
  list: (params?: { search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<RouteGroup>>(withQuery('/ui/api/route-groups', params as any)),
  get: (groupKey: string) =>
    apiFetch<{ group: RouteGroup; members: RouteGroupMemberDetail[]; policy: RoutePolicy | null }>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}`),
  create: (payload: any) => apiFetch<RouteGroup>('/ui/api/route-groups', { method: 'POST', json: payload }),
  update: (groupKey: string, payload: any) =>
    apiFetch<RouteGroup>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}`, { method: 'PUT', json: payload }),
  delete: (groupKey: string) => apiFetch<{ deleted: boolean }>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}`, { method: 'DELETE' }),
  members: (groupKey: string) =>
    apiFetch<RouteGroupMember[]>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}/members`),
  upsertMember: (groupKey: string, payload: any) =>
    apiFetch<RouteGroupMember>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}/members`, { method: 'POST', json: payload }),
  removeMember: (groupKey: string, deploymentId: string) =>
    apiFetch<{ deleted: boolean }>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}/members/${encodeURIComponent(deploymentId)}`, { method: 'DELETE' }),
  getPolicy: (groupKey: string) =>
    apiFetch<{ group_key: string; policy: RoutePolicy | null }>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy`),
  listPolicies: (groupKey: string) =>
    apiFetch<{ group_key: string; policies: RoutePolicy[] }>(`/ui/api/route-groups/${encodeURIComponent(groupKey)}/policies`),
  validatePolicy: (groupKey: string, payload: any) =>
    apiFetch<{ group_key: string; valid: boolean; policy: Record<string, unknown>; warnings: string[] }>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/validate`,
      { method: 'POST', json: payload }
    ),
  savePolicyDraft: (groupKey: string, payload: any) =>
    apiFetch<{ group_key: string; policy: RoutePolicy; warnings: string[] }>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/draft`,
      { method: 'POST', json: payload }
    ),
  publishPolicy: (groupKey: string, payload?: any) =>
    apiFetch<{ group_key: string; policy: RoutePolicy; warnings: string[] }>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/publish`,
      { method: 'POST', json: payload ?? {} }
    ),
  rollbackPolicy: (groupKey: string, version: number) =>
    apiFetch<{ group_key: string; policy: RoutePolicy; rolled_back_from_version: number }>(
      `/ui/api/route-groups/${encodeURIComponent(groupKey)}/policy/rollback`,
      { method: 'POST', json: { version } }
  ),
};

export interface PromptTemplate {
  prompt_template_id: string;
  template_key: string;
  name: string;
  description: string | null;
  owner_scope: string | null;
  metadata: Record<string, unknown> | null;
  version_count: number;
  label_count: number;
  binding_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PromptVersion {
  prompt_version_id: string;
  prompt_template_id: string;
  template_key: string;
  version: number;
  status: string;
  template_body: Record<string, unknown>;
  variables_schema: Record<string, unknown> | null;
  model_hints: Record<string, unknown> | null;
  route_preferences: Record<string, unknown> | null;
  published_at?: string | null;
  published_by?: string | null;
  archived_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PromptLabel {
  prompt_label_id: string;
  prompt_template_id: string;
  template_key: string;
  label: string;
  prompt_version_id: string;
  version: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PromptBinding {
  prompt_binding_id: string;
  scope_type: 'key' | 'team' | 'org' | 'group';
  scope_id: string;
  prompt_template_id: string;
  template_key: string;
  label: string;
  priority: number;
  enabled: boolean;
  metadata: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export const promptRegistry = {
  listTemplates: (params?: { search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<PromptTemplate>>(withQuery('/ui/api/prompt-registry/templates', params as any)),
  getTemplate: (templateKey: string) =>
    apiFetch<{ template: PromptTemplate; versions: PromptVersion[]; labels: PromptLabel[]; bindings: PromptBinding[] }>(
      `/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}`
    ),
  createTemplate: (payload: any) =>
    apiFetch<PromptTemplate>('/ui/api/prompt-registry/templates', { method: 'POST', json: payload }),
  updateTemplate: (templateKey: string, payload: any) =>
    apiFetch<PromptTemplate>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}`, { method: 'PUT', json: payload }),
  deleteTemplate: (templateKey: string) =>
    apiFetch<{ deleted: boolean }>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}`, { method: 'DELETE' }),
  createVersion: (templateKey: string, payload: any) =>
    apiFetch<PromptVersion>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/versions`, { method: 'POST', json: payload }),
  publishVersion: (templateKey: string, version: number) =>
    apiFetch<PromptVersion>(
      `/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/versions/${encodeURIComponent(String(version))}/publish`,
      { method: 'POST' }
    ),
  listLabels: (templateKey: string) =>
    apiFetch<PromptLabel[]>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/labels`),
  assignLabel: (templateKey: string, payload: any) =>
    apiFetch<PromptLabel>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/labels`, { method: 'POST', json: payload }),
  deleteLabel: (templateKey: string, label: string) =>
    apiFetch<{ deleted: boolean }>(
      `/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/labels/${encodeURIComponent(label)}`,
      { method: 'DELETE' }
    ),
  listBindings: (params?: { scope_type?: string; scope_id?: string; template_key?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<PromptBinding>>(withQuery('/ui/api/prompt-registry/bindings', params as any)),
  upsertBinding: (payload: any) =>
    apiFetch<PromptBinding>('/ui/api/prompt-registry/bindings', { method: 'POST', json: payload }),
  deleteBinding: (bindingId: string) =>
    apiFetch<{ deleted: boolean }>(`/ui/api/prompt-registry/bindings/${encodeURIComponent(bindingId)}`, { method: 'DELETE' }),
  dryRunRender: (payload: any) =>
    apiFetch<any>('/ui/api/prompt-registry/render', { method: 'POST', json: payload }),
  previewResolution: (payload: any) =>
    apiFetch<{ winner: any; candidates: any[] }>('/ui/api/prompt-registry/preview-resolution', { method: 'POST', json: payload }),
};

export interface Tier {
  tier_id: string;
  tier_key: string;
  name: string;
  description?: string | null;
  enabled: boolean;
  metadata?: Record<string, unknown> | null;
  active_version_id?: string | null;
  version_count: number;
  assignment_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierCreatePayload {
  tier_key: string;
  name: string;
  description?: string | null;
  enabled?: boolean;
  metadata?: Record<string, unknown> | null;
}

export type TierUpdatePayload = Partial<TierCreatePayload>;

export interface TierVersion {
  tier_version_id: string;
  tier_id: string;
  version_number: number;
  status: 'draft' | 'active' | 'archived' | string;
  configuration_revision: number;
  published_at?: string | null;
  published_by_account_id?: string | null;
  created_by_account_id?: string | null;
  created_by_kind: 'account' | 'master_key' | 'system' | 'unknown' | string;
  source_tier_version_id?: string | null;
  metadata?: Record<string, unknown> | null;
  model_policy_count: number;
  capacity_pool_count: number;
  assignment_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierVersionCreatePayload {
  version_number?: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface TierModelPolicy {
  tier_model_policy_id?: string;
  tier_version_id?: string;
  callable_key: string;
  enabled: boolean;
  access_mode: 'allow' | 'deny' | string;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  rph_limit?: number | null;
  rpd_limit?: number | null;
  tpd_limit?: number | null;
  max_parallel_requests?: number | null;
  batch_rpm_limit?: number | null;
  batch_tpm_limit?: number | null;
  pricing?: Record<string, number> | null;
  capacity_pool_key?: string | null;
  priority: number;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierModelPolicyPayload {
  callable_key: string;
  enabled: boolean;
  access_mode: string;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  rph_limit?: number | null;
  rpd_limit?: number | null;
  tpd_limit?: number | null;
  max_parallel_requests?: number | null;
  batch_rpm_limit?: number | null;
  batch_tpm_limit?: number | null;
  pricing?: Record<string, number> | null;
  capacity_pool_key?: string | null;
  priority: number;
  metadata?: Record<string, unknown> | null;
}

export interface TierCapacityPool {
  tier_capacity_pool_id?: string;
  tier_version_id?: string;
  pool_key: string;
  callable_key: string;
  rpm_capacity?: number | null;
  tpm_capacity?: number | null;
  max_parallel_requests?: number | null;
  strategy: string;
  saturation_threshold?: number | null;
  burst_multiplier?: number | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierCapacityPoolPayload {
  pool_key: string;
  callable_key: string;
  rpm_capacity?: number | null;
  tpm_capacity?: number | null;
  max_parallel_requests?: number | null;
  strategy: string;
  saturation_threshold?: number | null;
  burst_multiplier?: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface TierDetail {
  tier: Tier;
  versions: TierVersion[];
}

export interface TierVersionDetail {
  tier_version: TierVersion;
  model_policies: TierModelPolicy[];
  capacity_pools: TierCapacityPool[];
}

export interface OrganizationTierAssignment {
  assignment_id: string;
  organization_id: string;
  tier_id: string;
  tier_key?: string | null;
  tier_name?: string | null;
  tier_version_id?: string | null;
  tier_version_number?: number | null;
  tier_version_status?: string | null;
  assignment_type: 'primary' | 'addon' | 'override' | string;
  enabled: boolean;
  weight: number;
  starts_at?: string | null;
  ends_at?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface OrganizationTierAssignmentPayload {
  tier_id: string;
  tier_version_id?: string | null;
  assignment_type: string;
  enabled?: boolean;
  weight?: number;
  starts_at?: string | null;
  ends_at?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface OrganizationPrimaryTierSummary {
  assignment_id?: string | null;
  tier_id?: string | null;
  tier_key?: string | null;
  tier_name?: string | null;
  tier_version_id?: string | null;
  tier_version_number?: number | null;
  follows_active_version: boolean;
}

export interface OrganizationServicePolicy {
  source: 'tier' | 'legacy';
  runtime_source: 'tier' | 'legacy';
  tier_authoritative: boolean;
  tier_policy_mode: 'disabled' | 'shadow' | 'enforce' | string;
  primary_tier: OrganizationPrimaryTierSummary | null;
  active_assignment_count: number;
  overlay_count: number;
  hard_caps_configured: boolean;
  organization_hard_caps: Partial<Record<'rpm_limit' | 'tpm_limit' | 'rph_limit' | 'rpd_limit' | 'tpd_limit', number>>;
  legacy_model_limits_configured: boolean;
}

export interface OrganizationRecord {
  organization_id: string;
  organization_name?: string | null;
  max_budget?: number | null;
  soft_budget?: number | null;
  spend?: number | null;
  budget_duration?: string | null;
  budget_reset_at?: string | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  rph_limit?: number | null;
  rpd_limit?: number | null;
  tpd_limit?: number | null;
  model_rpm_limit?: Record<string, number> | null;
  model_tpm_limit?: Record<string, number> | null;
  audit_content_storage_enabled?: boolean | null;
  service_policy: OrganizationServicePolicy;
  primary_tier_assignment?: OrganizationTierAssignment;
  capabilities?: Record<string, boolean>;
  team_count?: number;
  member_count?: number;
  user_count?: number;
  created_at?: string | null;
  updated_at?: string | null;
  [key: string]: unknown;
}

export interface OrganizationCreatePayload {
  organization_name: string;
  legacy_policy_exception?: boolean;
  primary_tier?: {
    tier_id: string;
    tier_version_id?: string | null;
  };
  max_budget?: number;
  soft_budget?: number;
  budget_duration?: string;
  budget_reset_at?: string;
  rpm_limit?: number;
  tpm_limit?: number;
  rph_limit?: number;
  rpd_limit?: number;
  tpd_limit?: number;
  audit_content_storage_enabled?: boolean;
  callable_target_bindings?: Array<{ callable_key: string }>;
}

export interface TierPolicySnapshotInfo {
  etag: string;
  generated_at: string;
  org_count: number;
  assignment_count: number;
  model_policy_count: number;
  capacity_pool_count: number;
  next_transition_at?: string | null;
  mode: string;
  snapshot_stale: boolean;
  last_reload_failed: boolean;
  last_reload_error_at?: string | null;
}

export interface TierRateLimitDescriptor {
  scope: string;
  entity_id: string;
  limit: number;
  amount_kind: 'requests' | 'tokens' | string;
  window_seconds: number;
  mode: string;
}

export interface TierCompiledModelPolicy {
  organization_id: string;
  callable_key: string;
  access_mode: string;
  source: Record<string, unknown>;
  limits: Record<string, number | null>;
  pricing: Record<string, number>;
  capacity_pool_key?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface TierCompiledPricingPolicy {
  organization_id: string;
  callable_key: string;
  mode: string;
  pricing: Record<string, number>;
  source: Record<string, unknown>;
}

export interface TierCompiledCapacityPool {
  pool_key: string;
  callable_key: string;
  rpm_capacity?: number | null;
  tpm_capacity?: number | null;
  max_parallel_requests?: number | null;
  strategy: string;
  saturation_threshold?: number | null;
  burst_multiplier?: number | null;
  source_tier_version_ids: string[];
  source_pool_ids: string[];
  metadata?: Record<string, unknown> | null;
  rate_limit_descriptors: TierRateLimitDescriptor[];
}

export interface OrganizationTierPolicyPreview {
  organization_id: string;
  snapshot: TierPolicySnapshotInfo;
  explicit_policy: boolean;
  tier_keys: string[];
  assignments: OrganizationTierAssignment[];
  allowed_callable_keys: string[];
  model_policies: TierCompiledModelPolicy[];
  pricing_policies: TierCompiledPricingPolicy[];
  rate_limits: TierRateLimitDescriptor[];
  organization_hard_caps: Partial<Record<
    'rpm_limit' | 'tpm_limit' | 'rph_limit' | 'rpd_limit' | 'tpd_limit' | 'model_rpm_limit' | 'model_tpm_limit',
    number | Record<string, number>
  >>;
  organization_rate_limits: TierRateLimitDescriptor[];
  capacity_pools: TierCompiledCapacityPool[];
}

export type TierSimulationBillingMode =
  | 'chat'
  | 'embedding'
  | 'rerank'
  | 'image_generation'
  | 'audio_speech'
  | 'audio_transcription';

export interface TierPolicySimulation {
  organization_id: string;
  callable_key: string;
  mode: string;
  request: {
    request_count: number;
    prompt_tokens: number;
    completion_tokens: number;
    tokens_per_request: number;
    aggregate_tokens: number;
    billing_mode: TierSimulationBillingMode | null;
    usage: Record<string, number>;
  };
  access: {
    allowed: boolean;
    reason: string;
    explicit_policy: boolean;
    tier_keys: string[];
  };
  decision: {
    allowed: boolean;
    reason: string;
    primary_limiting_scope: string | null;
    limiting_scopes: string[];
    basis: 'empty_window_static';
    live_capacity_evaluated: false;
  };
  model_policy: TierCompiledModelPolicy | null;
  pricing: TierCompiledPricingPolicy | null;
  calculated_price: {
    status: 'available' | 'partial' | 'unavailable';
    reason: string | null;
    currency: string;
    kind: 'exact' | 'range' | null;
    amount: number | null;
    minimum_amount: number | null;
    maximum_amount: number | null;
    request_count: number;
    amount_scope: 'aggregate';
    per_request_amount: number | null;
    per_request_minimum_amount: number | null;
    per_request_maximum_amount: number | null;
    billing_mode: TierSimulationBillingMode | null;
    usage_snapshot: Record<string, number>;
    configured_candidate_count: number;
    priced_candidate_count: number;
    unpriced_candidate_count: number;
    unevaluated_candidate_count: number;
    unpriced_reasons: string[];
    pricing_sources: string[];
    basis: 'configured_routes';
  };
  rate_limits: TierRateLimitDescriptor[];
  organization_hard_caps: OrganizationTierPolicyPreview['organization_hard_caps'];
  organization_rate_limits: TierRateLimitDescriptor[];
  capacity_pool: TierCompiledCapacityPool | null;
  capacity_pool_rate_limits: TierRateLimitDescriptor[];
  static_limit_checks: Array<TierRateLimitDescriptor & {
    amount: number;
    would_exceed_limit: boolean;
    remaining_after_amount: number;
  }>;
  snapshot: TierPolicySnapshotInfo;
}

export interface TierPolicySimulationPayload {
  callable_key: string;
  mode?: string;
  billing_mode?: TierSimulationBillingMode;
  request_count?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  input_images?: number;
  output_images?: number;
  input_characters?: number;
  output_characters?: number;
  input_audio_tokens?: number;
  output_audio_tokens?: number;
  duration_seconds?: number;
}

export interface TierCapacityDashboardOrgUsage {
  organization_id: string;
  rpm_used: number;
  tpm_used: number;
  total_usage: number;
}

export interface TierCapacityDashboardBoost {
  organization_id: string;
  weight_multiplier: number;
  reason?: string | null;
  expires_at?: string | null;
}

export interface TierCapacityDashboardPool {
  pool_key: string;
  callable_key: string;
  strategy: string;
  advanced_fair_share: boolean;
  rpm_capacity?: number | null;
  tpm_capacity?: number | null;
  rpm_used: number | null;
  tpm_used: number | null;
  rpm_saturation?: number | null;
  tpm_saturation?: number | null;
  saturation_threshold?: number | null;
  burst_multiplier?: number | null;
  member_count: number;
  active_org_count: number | null;
  top_orgs: TierCapacityDashboardOrgUsage[];
  active_boosts: TierCapacityDashboardBoost[];
  active_boost_count: number | null;
  cleanup_lagged?: boolean | null;
}

export interface TierCapacityLimitHit {
  pool_key: string;
  callable_key: string;
  organization_id?: string | null;
  scope: string;
  tier_key?: string | null;
  count: number;
}

export interface TierCapacityDashboard {
  snapshot: TierPolicySnapshotInfo;
  window_seconds: number;
  window_id: number;
  generated_at: string;
  pools: TierCapacityDashboardPool[];
  total_pool_count: number;
  scanned_pool_count: number;
  pool_scan_limit: number;
  pool_scan_truncated: boolean;
  advanced_pool_count: number;
  saturated_pool_count: number | null;
  pool_limit: number;
  truncated: boolean;
  limit_hit_count: number | null;
  limit_hit_heatmap: TierCapacityLimitHit[];
  live_data: {
    status: 'healthy' | 'partial' | 'unavailable';
    redis_available: boolean;
    failed_sections: string[];
  };
}

export interface TierCapacityBoostPayload {
  organization_id: string;
  pool_key: string;
  callable_key: string;
  weight_multiplier?: number;
  ttl_seconds?: number;
  reason?: string | null;
}

export const settings = {
  get: () => apiFetch<any>('/ui/api/settings'),
  update: (payload: any) => apiFetch<any>('/ui/api/settings', { method: 'PUT', json: payload }),
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

export const tierCapacity = {
  dashboard: (params?: { top_org_limit?: number; pool_limit?: number }) =>
    apiFetch<TierCapacityDashboard>(withQuery('/ui/api/tier-capacity/dashboard', params)),
  upsertBoost: (payload: TierCapacityBoostPayload) =>
    apiFetch<TierCapacityDashboardBoost & {
      pool_key: string;
      callable_key: string;
      ttl_seconds: number;
    }>('/ui/api/tier-capacity/boosts', { method: 'POST', json: payload }),
  deleteBoost: (params: { organization_id: string; pool_key: string; callable_key: string }) =>
    apiFetch<{ deleted: boolean; organization_id: string; pool_key: string; callable_key: string }>(
      withQuery('/ui/api/tier-capacity/boosts', params),
      { method: 'DELETE' },
    ),
};

export const tiers = {
  list: (params?: { search?: string; enabled?: boolean | string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<Tier>>(withQuery('/ui/api/tiers', params)),
  listAll: async (params?: { search?: string; enabled?: boolean | string }) => {
    const limit = 200;
    let offset = 0;
    let items: Tier[] = [];
    while (true) {
      const page = await apiFetch<Paginated<Tier>>(
        withQuery('/ui/api/tiers', { ...(params || {}), limit, offset }),
      );
      items = items.concat(page.data || []);
      if (!page.pagination?.has_more) {
        break;
      }
      offset += limit;
    }
    return items;
  },
  get: (tierId: string) =>
    apiFetch<TierDetail>(`/ui/api/tiers/${encodeURIComponent(tierId)}`),
  create: (payload: TierCreatePayload) =>
    apiFetch<Tier>('/ui/api/tiers', { method: 'POST', json: payload }),
  update: (tierId: string, payload: TierUpdatePayload) =>
    apiFetch<Tier>(`/ui/api/tiers/${encodeURIComponent(tierId)}`, { method: 'PATCH', json: payload }),
  delete: (tierId: string) =>
    apiFetch<{ deleted: boolean; tier_id: string }>(`/ui/api/tiers/${encodeURIComponent(tierId)}`, { method: 'DELETE' }),
  createVersion: (tierId: string, payload: TierVersionCreatePayload = {}) =>
    apiFetch<TierVersion>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions`, { method: 'POST', json: payload }),
  cloneVersion: (tierId: string, sourceVersionId: string) =>
    apiFetch<TierVersion>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(sourceVersionId)}/clone`, { method: 'POST' }),
  getVersion: (tierId: string, versionId: string) =>
    apiFetch<TierVersionDetail>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}`),
  replaceModelPolicies: (tierId: string, versionId: string, policies: TierModelPolicyPayload[]) =>
    apiFetch<{ data: TierModelPolicy[] }>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/model-policies`, {
      method: 'PUT',
      json: { policies },
    }),
  replaceCapacityPools: (tierId: string, versionId: string, pools: TierCapacityPoolPayload[]) =>
    apiFetch<{ data: TierCapacityPool[] }>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/capacity-pools`, {
      method: 'PUT',
      json: { pools },
    }),
  publishVersion: (tierId: string, versionId: string) =>
    apiFetch<TierVersion>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/publish`, { method: 'POST' }),
  archiveVersion: (tierId: string, versionId: string) =>
    apiFetch<TierVersion>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/archive`, { method: 'POST' }),
};

export const organizations = {
  list: (params?: { search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<OrganizationRecord>>(withQuery('/ui/api/organizations', params as any)),
  get: (orgId: string) => apiFetch<OrganizationRecord>(`/ui/api/organizations/${encodeURIComponent(orgId)}`),
  create: (payload: OrganizationCreatePayload) =>
    apiFetch<OrganizationRecord>('/ui/api/organizations', { method: 'POST', json: payload }),
  update: (orgId: string, payload: any) =>
    apiFetch<any>(`/ui/api/organizations/${encodeURIComponent(orgId)}`, { method: 'PUT', json: payload }),
  members: (orgId: string) => apiFetch<any[]>(`/ui/api/organizations/${encodeURIComponent(orgId)}/members`),
  memberCandidates: (orgId: string, params?: { search?: string; limit?: number }) =>
    apiFetch<any[]>(withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/member-candidates`, params as any)),
  addMember: (orgId: string, payload: any) =>
    apiFetch<any>(`/ui/api/organizations/${encodeURIComponent(orgId)}/members`, { method: 'POST', json: payload }),
  removeMember: (orgId: string, membershipId: string) =>
    apiFetch<any>(`/ui/api/organizations/${encodeURIComponent(orgId)}/members/${encodeURIComponent(membershipId)}`, { method: 'DELETE' }),
  teams: (orgId: string) => apiFetch<any[]>(`/ui/api/organizations/${encodeURIComponent(orgId)}/teams`),
  assetVisibility: (orgId: string, params?: AssetVisibilityParams) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-visibility`, params as any)),
  assetAccess: (orgId: string, params?: ScopedAssetAccessParams) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-access`, params as any)),
  updateAssetAccess: (orgId: string, payload: { mode?: string; selected_callable_keys: string[]; selected_access_group_keys?: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-access`, { method: 'PUT', json: payload }),
  tierAssignments: (orgId: string, params?: { enabled?: boolean | string }) =>
    apiFetch<{ data: OrganizationTierAssignment[] }>(withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-assignments`, params)),
  createTierAssignment: (orgId: string, payload: OrganizationTierAssignmentPayload) =>
    apiFetch<OrganizationTierAssignment>(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-assignments`, { method: 'POST', json: payload }),
  updateTierAssignment: (orgId: string, assignmentId: string, payload: Partial<OrganizationTierAssignmentPayload>) =>
    apiFetch<OrganizationTierAssignment>(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-assignments/${encodeURIComponent(assignmentId)}`, { method: 'PATCH', json: payload }),
  deleteTierAssignment: (orgId: string, assignmentId: string) =>
    apiFetch<{ deleted: boolean; assignment_id: string; organization_id: string }>(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-assignments/${encodeURIComponent(assignmentId)}`, { method: 'DELETE' }),
  tierPolicyPreview: (orgId: string) =>
    apiFetch<OrganizationTierPolicyPreview>(`/ui/api/organizations/${encodeURIComponent(orgId)}/tier-policy-preview`),
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
  list: (params?: { search?: string; organization_id?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/teams', params as any)),
  get: (teamId: string) => apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}`),
  getSelfServicePolicy: async (teamId: string): Promise<SelfServicePolicy> => {
    const t = await apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}`);
    return {
      self_service_keys_enabled: !!t.self_service_keys_enabled,
      self_service_max_keys_per_user: t.self_service_max_keys_per_user ?? null,
      self_service_budget_ceiling: t.self_service_budget_ceiling ?? null,
      self_service_require_expiry: !!t.self_service_require_expiry,
      self_service_max_expiry_days: t.self_service_max_expiry_days ?? null,
    };
  },
  create: (payload: any) => apiFetch<any>('/ui/api/teams', { method: 'POST', json: payload }),
  update: (teamId: string, payload: any) => apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}`, { method: 'PUT', json: payload }),
  delete: (teamId: string) => apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}`, { method: 'DELETE' }),
  members: (teamId: string) => apiFetch<any[]>(`/ui/api/teams/${encodeURIComponent(teamId)}/members`),
  memberCandidates: (teamId: string, params?: { search?: string; limit?: number }) =>
    apiFetch<any[]>(withQuery(`/ui/api/teams/${encodeURIComponent(teamId)}/member-candidates`, params as any)),
  addMember: (teamId: string, payload: any) => apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}/members`, { method: 'POST', json: payload }),
  removeMember: (teamId: string, userId: string) =>
    apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  assetVisibility: (teamId: string, params?: AssetVisibilityParams) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/teams/${encodeURIComponent(teamId)}/asset-visibility`, params as any)),
  assetAccess: (teamId: string, params?: ScopedAssetAccessParams) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/teams/${encodeURIComponent(teamId)}/asset-access`, params as any)),
  updateAssetAccess: (teamId: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; selected_access_group_keys?: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/teams/${encodeURIComponent(teamId)}/asset-access`, { method: 'PUT', json: payload }),
};

export const serviceAccounts = {
  list: (params?: { team_id?: string; search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<ServiceAccount>>(withQuery('/ui/api/service-accounts', params as any)),
  create: (payload: { team_id: string; name: string; description?: string }) =>
    apiFetch<ServiceAccount>('/ui/api/service-accounts', { method: 'POST', json: payload }),
};

export const mcpServers = {
  list: (params?: { search?: string; enabled?: boolean; limit?: number; offset?: number }) =>
    apiFetch<Paginated<MCPServer>>(withQuery('/ui/api/mcp-servers', params as any)),
  get: (serverId: string) => apiFetch<MCPServerDetail>(`/ui/api/mcp-servers/${encodeURIComponent(serverId)}`),
  operations: (serverId: string, params?: { window_hours?: number; top_tools_limit?: number; failures_limit?: number }) =>
    apiFetch<MCPServerOperations>(withQuery(`/ui/api/mcp-servers/${encodeURIComponent(serverId)}/operations`, params as any)),
  create: (payload: any) => apiFetch<MCPServer>('/ui/api/mcp-servers', { method: 'POST', json: payload }),
  update: (serverId: string, payload: any) =>
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
    apiFetch<Paginated<MCPBinding>>(withQuery('/ui/api/mcp-bindings', params as any)),
  upsertBinding: (payload: any) => apiFetch<MCPBinding>('/ui/api/mcp-bindings', { method: 'POST', json: payload }),
  deleteBinding: (bindingId: string) =>
    apiFetch<{ deleted: boolean; mcp_binding_id: string }>(`/ui/api/mcp-bindings/${encodeURIComponent(bindingId)}`, { method: 'DELETE' }),
  listToolPolicies: (params?: { server_id?: string; scope_type?: string; scope_id?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<MCPToolPolicy>>(withQuery('/ui/api/mcp-tool-policies', params as any)),
  upsertToolPolicy: (payload: any) =>
    apiFetch<MCPToolPolicy>('/ui/api/mcp-tool-policies', { method: 'POST', json: payload }),
  deleteToolPolicy: (policyId: string) =>
    apiFetch<{ deleted: boolean; mcp_tool_policy_id: string }>(`/ui/api/mcp-tool-policies/${encodeURIComponent(policyId)}`, { method: 'DELETE' }),
  listApprovalRequests: (params?: { server_id?: string; status?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<MCPApprovalRequest>>(withQuery('/ui/api/mcp-approval-requests', params as any)),
  decideApprovalRequest: (approvalRequestId: string, payload: { status: 'approved' | 'rejected'; decision_comment?: string }) =>
    apiFetch<MCPApprovalRequest>(`/ui/api/mcp-approval-requests/${encodeURIComponent(approvalRequestId)}/decision`, { method: 'POST', json: payload }),
};

export const keys = {
  list: (params?: { search?: string; team_id?: string; my_keys?: boolean; limit?: number; offset?: number }) =>
    apiFetch<Paginated<ApiKey>>(withQuery('/ui/api/keys', params as any)),
  create: (payload: any) => apiFetch<ApiKey & { raw_key: string }>('/ui/api/keys', { method: 'POST', json: payload }),
  update: (tokenHash: string, payload: any) =>
    apiFetch<ApiKey>(`/ui/api/keys/${encodeURIComponent(tokenHash)}`, { method: 'PUT', json: payload }),
  regenerate: (tokenHash: string) => apiFetch<{ token: string; raw_key: string }>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/regenerate`, { method: 'POST' }),
  revoke: (tokenHash: string) => apiFetch<{ revoked: boolean }>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/revoke`, { method: 'POST' }),
  delete: (tokenHash: string) => apiFetch<{ deleted: boolean }>(`/ui/api/keys/${encodeURIComponent(tokenHash)}`, { method: 'DELETE' }),
  assetVisibility: (tokenHash: string, params?: AssetVisibilityParams) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-visibility`, params as any)),
  assetAccess: (tokenHash: string, params?: ScopedAssetAccessParams) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-access`, params as any)),
  updateAssetAccess: (tokenHash: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; selected_access_group_keys?: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-access`, { method: 'PUT', json: payload }),
};

export const users = {
  assetVisibility: (userId: string, params?: Omit<AssetVisibilityParams, 'user_id'>) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/users/${encodeURIComponent(userId)}/asset-visibility`, params as any)),
  assetAccess: (userId: string, params?: ScopedAssetAccessParams) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/users/${encodeURIComponent(userId)}/asset-access`, params as any)),
  updateAssetAccess: (userId: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; selected_access_group_keys?: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/users/${encodeURIComponent(userId)}/asset-access`, { method: 'PUT', json: payload }),
};

export const batches = {
  featureStatus: () => apiFetch<BatchFeatureStatus>('/ui/api/batches/feature-status'),
  list: (params?: { search?: string; status?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<BatchJobListItem>>(withQuery('/ui/api/batches', params as any)),
  summary: () => apiFetch<BatchJobSummary>('/ui/api/batches/summary'),
  get: (batchId: string, params?: { items_limit?: number; items_offset?: number; after_line_number?: number | null }) =>
    apiFetch<BatchJobDetail>(withQuery(`/ui/api/batches/${encodeURIComponent(batchId)}`, params as any)),
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
    apiFetch<any>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`),
  updateScoped: (scope: 'organization' | 'team' | 'key', entityId: string, payload: any) =>
    apiFetch<any>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`, { method: 'PUT', json: payload }),
  deleteScoped: (scope: 'organization' | 'team' | 'key', entityId: string) =>
    apiFetch<any>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`, { method: 'DELETE' }),
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
      apiFetch<Paginated<Principal>>(withQuery('/ui/api/principals', params as any)),
    summary: () => apiFetch<PrincipalSummary>('/ui/api/principals/summary'),
  },
  accounts: {
    upsert: (payload: any) => apiFetch<any>('/ui/api/rbac/accounts', { method: 'POST', json: payload }),
    delete: (accountId: string) =>
      apiFetch<any>(`/ui/api/rbac/accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
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
    upsert: (payload: any) => apiFetch<any>('/ui/api/rbac/organization-memberships', { method: 'POST', json: payload }),
    delete: (membershipId: string) =>
      apiFetch<any>(`/ui/api/rbac/organization-memberships/${encodeURIComponent(membershipId)}`, { method: 'DELETE' }),
  },
  teamMemberships: {
    list: () => apiFetch<TeamMembership[]>('/ui/api/rbac/team-memberships'),
    upsert: (payload: any) => apiFetch<any>('/ui/api/rbac/team-memberships', { method: 'POST', json: payload }),
    delete: (membershipId: string) =>
      apiFetch<any>(`/ui/api/rbac/team-memberships/${encodeURIComponent(membershipId)}`, { method: 'DELETE' }),
  },
};

export const invitations = {
  list: (params?: { status?: Invitation['status'] | 'active'; search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<Invitation>>(withQuery('/ui/api/invitations', params as any)),
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

export interface SelfRegistrationPublicConfig {
  enabled: boolean;
  mode: string | null;
  sandbox_access_enabled: boolean;
}

export interface AuthSsoConfig {
  sso_enabled: boolean;
  provider?: string;
  self_registration?: SelfRegistrationPublicConfig;
}

export const auth = {
  me: () => apiFetch<unknown>('/auth/me', { headers: new Headers({ 'Content-Type': 'application/json' }) }),
  internalLogin: (payload: { email: string; password: string; mfa_code?: string }) =>
    apiFetch<any>('/auth/internal/login', { method: 'POST', json: payload }),
  masterLogin: (masterKey: string) =>
    apiFetch<any>('/auth/master/login', { method: 'POST', json: { master_key: masterKey } }),
  internalLogout: () => apiFetch<any>('/auth/internal/logout', { method: 'POST' }),
  changePassword: (current_password: string | null, new_password: string) =>
    apiFetch<any>('/auth/internal/change-password', { method: 'POST', json: { current_password, new_password } }),
  invitation: (token: string) => apiFetch<any>(`/auth/invitations/${encodeURIComponent(token)}`),
  acceptInvitation: (payload: { token: string; password?: string | null }) =>
    apiFetch<InvitationAcceptResult>('/auth/invitations/accept', { method: 'POST', json: payload }),
  forgotPassword: (email: string) =>
    apiFetch<{ requested: boolean }>('/auth/internal/forgot-password', { method: 'POST', json: { email } }),
  validateResetPasswordToken: (token: string) =>
    apiFetch<any>(`/auth/internal/reset-password/${encodeURIComponent(token)}`),
  resetPassword: (token: string, new_password: string) =>
    apiFetch<{ changed: boolean }>('/auth/internal/reset-password', { method: 'POST', json: { token, new_password } }),
  ssoConfig: () => apiFetch<AuthSsoConfig>('/auth/sso-config'),
  ssoLogin: (state: string, returnTo = '/') => apiFetch<{ authorize_url: string }>(
    `/auth/login?state=${encodeURIComponent(state)}&return_to=${encodeURIComponent(returnTo)}`,
  ),
  mfaEnrollStart: () => apiFetch<{ secret: string; otpauth_url: string }>('/auth/mfa/enroll/start', { method: 'POST' }),
  mfaEnrollConfirm: (code: string) => apiFetch<{ mfa_enabled: boolean }>('/auth/mfa/enroll/confirm', { method: 'POST', json: { code } }),
  mfaVerify: (code: string) => apiFetch<{ mfa_verified: boolean }>('/auth/mfa/verify', { method: 'POST', json: { code } }),
};
