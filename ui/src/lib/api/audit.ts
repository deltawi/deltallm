import { mountedPath } from '../uiMount';
import { apiFetch, withQuery } from './transport';
import { requestInit, type UsageReportOptions } from './usageReports';

export type AuditCategory = 'admin' | 'auth' | 'api' | 'mcp' | 'spend' | 'system';

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

/** Display names the server looked up for the IDs on one row. */
export interface AuditEventLabels {
  actor: string | null;
  key_name: string | null;
  team_id: string | null;
  team: string | null;
  organization: string | null;
  resource: string | null;
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
  labels?: AuditEventLabels;
}

/** The current state of the things one event touched, looked up when its panel opens. */
export interface AuditEventContext {
  labels: AuditEventLabels;
  actor: {
    email?: string | null;
    platform_role?: string | null;
    organization_role?: string | null;
    is_active?: boolean | null;
    state?: 'missing';
  } | null;
  key: {
    name: string | null;
    tail: string;
    team_id: string | null;
    team: string | null;
    owner: string | null;
    state: 'active' | 'expired' | 'missing';
  } | null;
  resource: {
    label: string | null;
    state?: string;
    progress?: number | null;
    total_items?: number;
    team_id?: string | null;
    team?: string | null;
    owner?: string | null;
  } | null;
}

export interface AuditEventDetail extends AuditEvent {
  payloads?: AuditPayload[];
  payload_summary?: { count: number; redacted: boolean; kinds: string[] };
  context?: AuditEventContext;
}

export type AuditRelatedEvent = Pick<
  AuditEvent,
  'event_id' | 'occurred_at' | 'action' | 'status' | 'actor_type' | 'actor_id' | 'resource_type' | 'resource_id' | 'error_code'
>;

export interface AuditRelatedResponse {
  request: AuditRelatedEvent[];
  resource: AuditRelatedEvent[];
  actor_signins: AuditRelatedEvent[];
}

export interface AuditCursorPagination {
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
}

export interface AuditListResponse {
  events: AuditEvent[];
  pagination: AuditCursorPagination;
}

export interface AuditEventQuery {
  since?: string;
  until?: string;
  category?: AuditCategory;
  status?: 'success' | 'error';
  action?: string;
  actor?: string;
  actor_type?: string;
  organization_id?: string;
  resource_type?: string;
  resource_id?: string;
  ip?: string;
  request_id?: string;
  search?: string;
}

export const audit = {
  /** One page, newest first, with names; no total count. */
  list: (query: AuditEventQuery, page: { cursor?: string | null; limit: number }, options?: UsageReportOptions) =>
    apiFetch<AuditListResponse>(
      withQuery('/ui/api/audit/events', { ...query, cursor: page.cursor ?? undefined, limit: page.limit, count: false, enrich: true }),
      requestInit(options),
    ),
  /** One event with its panel lookups; bodies load separately. */
  get: (eventId: string, options?: UsageReportOptions) =>
    apiFetch<AuditEventDetail>(
      withQuery(`/ui/api/audit/events/${encodeURIComponent(eventId)}`, { include_payloads: false, context: true }),
      requestInit(options),
    ),
  related: (eventId: string, options?: UsageReportOptions) =>
    apiFetch<AuditRelatedResponse>(`/ui/api/audit/events/${encodeURIComponent(eventId)}/related`, requestInit(options)),
  payloads: (eventId: string, options?: UsageReportOptions) =>
    apiFetch<{ payloads: AuditPayload[] }>(`/ui/api/audit/events/${encodeURIComponent(eventId)}/payloads`, requestInit(options)),
  actions: (options?: UsageReportOptions) =>
    apiFetch<{ actions: Array<{ action: string; category: AuditCategory }>; categories: AuditCategory[] }>(
      '/ui/api/audit/actions',
      requestInit(options),
    ),
  organizations: (search: string, options?: UsageReportOptions) =>
    apiFetch<{ organizations: Array<{ organization_id: string; organization_name: string | null }> }>(
      withQuery('/ui/api/audit/organizations', { search, limit: 8 }),
      requestInit(options),
    ),
  /** Mounted URL for a download with the current filters (up to 10,000 events). */
  exportUrl: (query: AuditEventQuery, format: 'csv' | 'jsonl') =>
    mountedPath(withQuery('/ui/api/audit/export', { ...query, format, limit: 10000 })),
};
