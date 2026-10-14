import { apiFetch } from './transport';
import type { SpendLog, SpendLogsPagination, SpendReportingContext, SpendView } from '../api';
import { BACKGROUND_REQUEST_HEADER } from '../sessionActivity';

export type UsageDimension = 'model' | 'provider' | 'organization' | 'team' | 'user' | 'api_key';
export type UsageSplit = 'model' | 'provider' | 'team';
export type UsageSort = 'spend' | 'tokens' | 'requests';
export type UsageInterval = 'day' | 'week' | 'month';

export interface UsageFilterValue {
  dimension: UsageDimension;
  /** Empty when ``unassigned`` is true. */
  value: string;
  unassigned?: boolean;
  label?: string | null;
}

export interface UsageReportScope {
  view: SpendView;
  startDate?: string;
  endDate?: string;
  filters: readonly UsageFilterValue[];
}

export interface UsageReportOptions {
  signal?: AbortSignal;
  /** Ask the gateway to skip its reporting cache (the Refresh button). */
  force?: boolean;
  /** An automatic reload: it must not keep an idle session signed in. */
  background?: boolean;
}

export interface UsageMetrics {
  total_spend: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  request_count: number;
  failed_requests: number;
}

export interface UsageWindow {
  start_date: string | null;
  end_date: string;
  previous_start_date: string | null;
  previous_end_date: string | null;
}

export interface UsageSummaryResponse {
  current: UsageMetrics;
  previous: UsageMetrics | null;
  window: UsageWindow;
  reporting_context?: SpendReportingContext;
}

export interface UsageSeriesGroup {
  series_index: number;
  group_key: string | null;
  display_name: string | null;
  is_unassigned: boolean;
  is_other: boolean;
  total_spend: number;
  total_tokens: number;
  request_count: number;
  failed_requests: number;
}

export interface UsageSeriesBucket {
  bucket: string;
  series_index: number;
  total_spend: number;
  total_tokens: number;
  request_count: number;
  failed_requests: number;
}

export interface UsageSeriesResponse {
  interval: UsageInterval;
  split_by: UsageSplit | null;
  groups: UsageSeriesGroup[];
  buckets: UsageSeriesBucket[];
  window: UsageWindow;
  reporting_context?: SpendReportingContext;
}

export interface UsageBreakdownRow extends UsageMetrics {
  group_key: string | null;
  is_unassigned: boolean;
  display_name: string | null;
  previous_spend: number | null;
}

export interface UsageBreakdownResponse {
  group_by: UsageDimension;
  data: UsageBreakdownRow[];
  scope_spend: number;
  pagination: { total: number; limit: number; offset: number; has_more: boolean };
  window: UsageWindow;
  reporting_context?: SpendReportingContext;
}

export interface LiveHourlyBucket {
  bucket: string;
  total_spend: number;
  request_count: number;
  failed_requests: number;
}

export interface LiveHourlyResponse {
  buckets: LiveHourlyBucket[];
  active_keys_24h: number;
  as_of: string;
}

export interface LiveDay {
  date: string;
  total_spend: number;
  request_count: number;
  failed_requests: number;
}

export interface LiveDailyResponse {
  days: LiveDay[];
  start_date: string;
  end_date: string;
}

export interface LiveProvider {
  provider: string;
  request_count: number;
  failed_requests: number;
  top_failing_model: string | null;
  top_failing_model_failures: number;
  top_error: string | null;
  first_error_at: string | null;
  last_error_at: string | null;
}

export interface LiveProvidersResponse {
  providers: LiveProvider[];
}

export interface LiveTopItem {
  dimension: 'api_key' | 'model' | 'team';
  group_key: string;
  display_name: string | null;
  request_count: number;
  share: number;
}

export interface LiveTopResponse {
  total_requests: number;
  items: LiveTopItem[];
}

export interface BudgetAlertItem {
  kind: 'organization' | 'team' | 'api_key';
  group_key: string;
  display_name: string | null;
  spend: number;
  max_budget: number;
  budget_duration: string | null;
  budget_reset_at: string | null;
}

export interface BudgetAlertsResponse {
  items: BudgetAlertItem[];
}

export interface RequestLog extends SpendLog {
  provider?: string | null;
  latency_ms?: number | null;
  organization_id?: string | null;
  team_alias?: string | null;
  organization_name?: string | null;
  key_name?: string | null;
  user_email?: string | null;
}

export interface RequestLogsResponse {
  logs: RequestLog[];
  pagination: SpendLogsPagination;
  reporting_context?: SpendReportingContext;
}

export interface RequestLogQuery {
  view: SpendView;
  since?: string;
  filters: readonly UsageFilterValue[];
  status?: 'success' | 'error';
  cacheHit?: boolean;
  requestId?: string;
  cursor?: string | null;
  limit: number;
}

const LOG_FILTER_PARAMS: Record<UsageDimension, string> = {
  model: 'model',
  provider: 'provider',
  organization: 'organization_id',
  team: 'team_id',
  user: 'user_id',
  api_key: 'api_key',
};

export function requestInit(options?: UsageReportOptions): RequestInit {
  const headers: Record<string, string> = {};
  if (options?.force) headers['Cache-Control'] = 'no-cache';
  if (options?.background) headers[BACKGROUND_REQUEST_HEADER] = '1';
  return Object.keys(headers).length ? { signal: options?.signal, headers } : { signal: options?.signal };
}

function withView(query: URLSearchParams, view: SpendView): URLSearchParams {
  if (view !== 'platform') query.set('view', view);
  return query;
}

export function usageScopeQuery(scope: UsageReportScope): URLSearchParams {
  const query = withView(new URLSearchParams(), scope.view);
  if (scope.startDate) query.set('start_date', scope.startDate);
  if (scope.endDate) query.set('end_date', scope.endDate);
  for (const filter of scope.filters) {
    if (filter.unassigned) query.append('unassigned', filter.dimension);
    else if (filter.value) query.set(filter.dimension, filter.value);
  }
  return query;
}

function path(base: string, query: URLSearchParams): string {
  const suffix = query.toString();
  return suffix ? `${base}?${suffix}` : base;
}

export function requestLogQuery(params: RequestLogQuery): URLSearchParams {
  const query = withView(new URLSearchParams(), params.view);
  query.set('pagination_mode', 'cursor');
  query.set('limit', String(params.limit));
  if (params.since) query.set('since', params.since);
  for (const filter of params.filters) {
    if (filter.unassigned) query.append('unassigned', filter.dimension);
    else if (filter.value) query.set(LOG_FILTER_PARAMS[filter.dimension], filter.value);
  }
  if (params.status) query.set('status', params.status);
  if (params.cacheHit !== undefined) query.set('cache_hit', String(params.cacheHit));
  if (params.requestId) query.set('request_id', params.requestId);
  if (params.cursor) query.set('cursor', params.cursor);
  return query;
}

export const usageReports = {
  summary: (scope: UsageReportScope, options?: UsageReportOptions) => apiFetch<UsageSummaryResponse>(
    path('/ui/api/spend/explorer/summary', usageScopeQuery(scope)),
    requestInit(options),
  ),
  series: (
    scope: UsageReportScope,
    params: { interval: UsageInterval; splitBy: UsageSplit; maxGroups: number },
    options?: UsageReportOptions,
  ) => {
    const query = usageScopeQuery(scope);
    query.set('interval', params.interval);
    query.set('split_by', params.splitBy);
    query.set('max_groups', String(params.maxGroups));
    return apiFetch<UsageSeriesResponse>(path('/ui/api/spend/explorer/series', query), requestInit(options));
  },
  breakdown: (
    scope: UsageReportScope,
    params: { groupBy: UsageDimension; sortBy?: UsageSort; search?: string; limit: number; offset: number },
    options?: UsageReportOptions,
  ) => {
    const query = usageScopeQuery(scope);
    query.set('group_by', params.groupBy);
    query.set('sort_by', params.sortBy ?? 'spend');
    query.set('limit', String(params.limit));
    query.set('offset', String(params.offset));
    if (params.search?.trim()) query.set('search', params.search.trim());
    return apiFetch<UsageBreakdownResponse>(path('/ui/api/spend/explorer/breakdown', query), requestInit(options));
  },
  liveHourly: (view: SpendView, options?: UsageReportOptions) => apiFetch<LiveHourlyResponse>(
    path('/ui/api/spend/live/hourly', withView(new URLSearchParams(), view)),
    requestInit(options),
  ),
  liveDaily: (view: SpendView, options?: UsageReportOptions) => apiFetch<LiveDailyResponse>(
    path('/ui/api/spend/live/daily', withView(new URLSearchParams(), view)),
    requestInit(options),
  ),
  liveProviders: (view: SpendView, options?: UsageReportOptions) => apiFetch<LiveProvidersResponse>(
    path('/ui/api/spend/live/providers', withView(new URLSearchParams(), view)),
    requestInit(options),
  ),
  liveTop: (view: SpendView, options?: UsageReportOptions) => apiFetch<LiveTopResponse>(
    path('/ui/api/spend/live/top', withView(new URLSearchParams(), view)),
    requestInit(options),
  ),
  budgetAlerts: (view: SpendView, options?: UsageReportOptions) => apiFetch<BudgetAlertsResponse>(
    path('/ui/api/spend/live/budgets', withView(new URLSearchParams(), view)),
    requestInit(options),
  ),
  requestLogs: (params: RequestLogQuery, options?: UsageReportOptions) => apiFetch<RequestLogsResponse>(
    path('/ui/api/logs', requestLogQuery(params)),
    requestInit(options),
  ),
};
