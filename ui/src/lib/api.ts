export class ApiError extends Error {
  status: number;
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

let masterKey: string | null = null;

export function setMasterKey(value: string | null) {
  masterKey = value;
}

function buildHeaders(init?: HeadersInit): HeadersInit {
  const headers = new Headers(init);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (masterKey) headers.set('X-Master-Key', masterKey);
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
  const res = await fetch(path, {
    credentials: 'include',
    ...opts,
    headers: buildHeaders(opts?.headers),
    body: opts && 'json' in opts ? JSON.stringify((opts as any).json ?? null) : opts?.body,
  });

  if (!res.ok) {
    const detail = await parseErrorDetail(res);
    throw new ApiError(errorMessage(res.status, detail), res.status, detail);
  }

  if (res.status === 204) return undefined as T;

  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) return (await res.text()) as unknown as T;
  return (await res.json()) as T;
}

export interface Pagination {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
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

export type SpendGroupBy = 'model' | 'organization' | 'team' | 'api_key';

export interface SpendSummary {
  total_spend: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_requests: number;
  unique_models?: number;
  successful_requests?: number;
  failed_requests?: number;
}

export interface SpendGroupRow {
  group_key: string;
  display_name?: string | null;
  total_spend: number;
  total_tokens: number;
  request_count: number;
}

export interface SpendGroupReport {
  group_by: SpendGroupBy | 'provider' | 'user';
  data: SpendGroupRow[];
  pagination: Pagination;
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

export interface BatchJobItem {
  item_id: string;
  line_number: number;
  custom_id?: string | null;
  status: string;
  attempts: number;
  provider_cost?: number | null;
  billed_cost?: number | null;
  last_error?: string | null;
  request_body?: Record<string, unknown> | null;
  response_body?: Record<string, unknown> | null;
  error_body?: Record<string, unknown> | null;
  usage?: Record<string, unknown> | null;
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
  total_provider_cost: number;
  total_billed_cost: number;
  created_by_api_key?: string | null;
  created_by_team_id?: string | null;
  team_alias?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  cancel_requested_at?: string | null;
  expires_at?: string | null;
  capabilities: BatchCapabilities;
  items: Paginated<BatchJobItem>;
}

export interface CallableTargetListItem {
  callable_key: string;
  target_type: 'model' | 'route_group';
  binding_count: number;
}

export interface AssetAccessTarget {
  callable_key: string;
  target_type: 'model' | 'route_group';
  selectable: boolean;
  selected: boolean;
  effective_visible: boolean;
  inherited_only: boolean;
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
  selectable_targets: AssetAccessTarget[];
  effective_targets: AssetAccessTarget[];
  summary: {
    selected_total: number;
    selectable_total: number;
    effective_total: number;
  };
}

export interface ProviderPreset {
  provider: string;
  api_base: string | null;
  compat: string;
  supported_modes: string[];
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
  api_key?: string | null;
  api_base?: string | null;
  api_version?: string | null;
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
  healthy?: boolean;
  health?: DeploymentHealth;
  deltallm_params: Record<string, any>;
  model_info: Record<string, any>;
}

export const callableTargets = {
  list: (params?: { search?: string; target_type?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<CallableTargetListItem>>(withQuery('/ui/api/callable-targets', params as any)),
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
  summary: (start_date?: string, end_date?: string) => {
    const qs = new URLSearchParams();
    if (start_date) qs.set('start_date', start_date);
    if (end_date) qs.set('end_date', end_date);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return apiFetch<SpendSummary>(`/ui/api/spend/summary${suffix}`);
  },
  report: (group_by: 'model' | 'provider' | 'day' | 'user' | 'team', start_date?: string, end_date?: string) => {
    const qs = new URLSearchParams({ group_by });
    if (start_date) qs.set('start_date', start_date);
    if (end_date) qs.set('end_date', end_date);
    return apiFetch<any>(`/ui/api/spend/report?${qs.toString()}`);
  },
  groupedReport: (
    group_by: SpendGroupBy,
    params?: { start_date?: string; end_date?: string; search?: string; limit?: number; offset?: number }
  ) => {
    const qs = new URLSearchParams({ group_by });
    if (params?.start_date) qs.set('start_date', params.start_date);
    if (params?.end_date) qs.set('end_date', params.end_date);
    if (params?.search) qs.set('search', params.search);
    if (params?.limit != null) qs.set('limit', String(params.limit));
    if (params?.offset != null) qs.set('offset', String(params.offset));
    return apiFetch<SpendGroupReport>(`/ui/api/spend/report?${qs.toString()}`);
  },
  logs: (params?: Record<string, string>) => {
    const qs = new URLSearchParams(params || {});
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return apiFetch<{ logs: SpendLog[]; pagination: Pagination }>(`/ui/api/logs${suffix}`);
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

export const settings = {
  get: () => apiFetch<any>('/ui/api/settings'),
  update: (payload: any) => apiFetch<any>('/ui/api/settings', { method: 'PUT', json: payload }),
};

export const organizations = {
  list: (params?: { search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/organizations', params as any)),
  get: (orgId: string) => apiFetch<any>(`/ui/api/organizations/${encodeURIComponent(orgId)}`),
  create: (payload: any) => apiFetch<any>('/ui/api/organizations', { method: 'POST', json: payload }),
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
  assetVisibility: (orgId: string, params?: { user_id?: string }) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-visibility`, params as any)),
  assetAccess: (orgId: string, params?: { include_targets?: boolean }) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-access`, params as any)),
  updateAssetAccess: (orgId: string, payload: { mode?: string; selected_callable_keys: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/organizations/${encodeURIComponent(orgId)}/asset-access`, { method: 'PUT', json: payload }),
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
  assetVisibility: (teamId: string, params?: { user_id?: string }) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/teams/${encodeURIComponent(teamId)}/asset-visibility`, params as any)),
  assetAccess: (teamId: string, params?: { include_targets?: boolean }) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/teams/${encodeURIComponent(teamId)}/asset-access`, params as any)),
  updateAssetAccess: (teamId: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; select_all_selectable?: boolean }) =>
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
  assetVisibility: (tokenHash: string, params?: { user_id?: string }) =>
    apiFetch<AssetVisibilityResponse>(withQuery(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-visibility`, params as any)),
  assetAccess: (tokenHash: string, params?: { include_targets?: boolean }) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-access`, params as any)),
  updateAssetAccess: (tokenHash: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/asset-access`, { method: 'PUT', json: payload }),
};

export const users = {
  assetVisibility: (userId: string) =>
    apiFetch<AssetVisibilityResponse>(`/ui/api/users/${encodeURIComponent(userId)}/asset-visibility`),
  assetAccess: (userId: string, params?: { include_targets?: boolean }) =>
    apiFetch<ScopedAssetAccess>(withQuery(`/ui/api/users/${encodeURIComponent(userId)}/asset-access`, params as any)),
  updateAssetAccess: (userId: string, payload: { mode: 'inherit' | 'restrict'; selected_callable_keys: string[]; select_all_selectable?: boolean }) =>
    apiFetch<ScopedAssetAccess>(`/ui/api/users/${encodeURIComponent(userId)}/asset-access`, { method: 'PUT', json: payload }),
};

export const batches = {
  list: (params?: { search?: string; status?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<BatchJobListItem>>(withQuery('/ui/api/batches', params as any)),
  summary: () => apiFetch<BatchJobSummary>('/ui/api/batches/summary'),
  get: (batchId: string, params?: { items_limit?: number; items_offset?: number }) =>
    apiFetch<BatchJobDetail>(withQuery(`/ui/api/batches/${encodeURIComponent(batchId)}`, params as any)),
  cancel: (batchId: string) => apiFetch<{ batch_id: string; status: string }>(`/ui/api/batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' }),
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
  role: string;
  created_at?: string;
  updated_at?: string;
}

export interface TeamMembership {
  membership_id: string;
  account_id: string;
  team_id: string;
  role: string;
  created_at?: string;
  updated_at?: string;
}

export interface Principal extends RBACAccount {
  runtime_user_id?: string | null;
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

export const auth = {
  me: () => apiFetch<any>('/auth/me', { headers: new Headers({ 'Content-Type': 'application/json' }) }),
  internalLogin: (payload: { email: string; password: string; mfa_code?: string }) =>
    apiFetch<any>('/auth/internal/login', { method: 'POST', json: payload }),
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
  ssoConfig: () => apiFetch<{ sso_enabled: boolean; provider?: string }>('/auth/sso-config'),
  ssoLogin: (state: string) => apiFetch<{ authorize_url: string }>(`/auth/login?state=${encodeURIComponent(state)}`),
  mfaEnrollStart: () => apiFetch<{ secret: string; otpauth_url: string }>('/auth/mfa/enroll/start', { method: 'POST' }),
  mfaEnrollConfirm: (code: string) => apiFetch<{ mfa_enabled: boolean }>('/auth/mfa/enroll/confirm', { method: 'POST', json: { code } }),
  mfaVerify: (code: string) => apiFetch<{ mfa_verified: boolean }>('/auth/mfa/verify', { method: 'POST', json: { code } }),
};
