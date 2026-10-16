/** Batch jobs admin API: list, filters, detail, items and repair actions. */
import type { BatchItemError } from './batchContracts';
import type { Paginated } from './pagination';
import { apiFetch, withQuery } from './transport';

export interface BatchCapabilities {
  view: boolean;
  cancel: boolean;
  retry_finalization?: boolean;
  requeue_stale?: boolean;
  mark_failed?: boolean;
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

/** `own`: the caller sees only batches it created (members without key.read). */
export type BatchJobListView = 'operator' | 'own';

export interface BatchJobList extends Paginated<BatchJobListItem> {
  view?: BatchJobListView;
}

export interface BatchJobSummary {
  total: number;
  queued: number;
  /** In progress plus finalizing. */
  in_progress: number;
  finalizing?: number;
  completed: number;
  failed: number;
  cancelled: number;
  expired?: number;
}

export type BatchEndpoint = '/v1/embeddings' | '/v1/chat/completions';

/** List filters. The status tabs use ``status``; the summary ignores it. */
export interface BatchListFilters {
  search?: string;
  /** One status or a comma-separated list. */
  status?: string;
  model?: string;
  endpoint?: BatchEndpoint;
  team_id?: string;
  /** ISO time; jobs created at or after it. */
  created_after?: string;
}

export interface BatchFacetValue {
  value: string;
  label: string;
  count: number;
}

export interface BatchFacets {
  models: BatchFacetValue[];
  teams: BatchFacetValue[];
}

export type BatchItemStatus = 'pending' | 'in_progress' | 'completed' | 'failed' | 'cancelled';

export interface BatchItemQuery {
  items_limit?: number;
  items_offset?: number;
  after_line_number?: number | null;
  item_status?: BatchItemStatus;
  custom_id?: string;
}

/** Failed items with the same public error. Provider text is never sent. */
export interface BatchErrorGroup {
  message: string;
  retry_category: string | null;
  code: string | null;
  count: number;
}

export interface BatchErrorGroups {
  batch_id: string;
  data: BatchErrorGroup[];
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
  input_file_id?: string | null;
  output_file_id?: string | null;
  error_file_id?: string | null;
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


export const batches = {
  featureStatus: (signal?: AbortSignal) =>
    apiFetch<BatchFeatureStatus>('/ui/api/batches/feature-status', { signal }),
  list: (params?: BatchListFilters & { limit?: number; offset?: number }, signal?: AbortSignal) =>
    apiFetch<BatchJobList>(withQuery('/ui/api/batches', params), { signal }),
  summary: (params?: Omit<BatchListFilters, 'status'>, signal?: AbortSignal) =>
    apiFetch<BatchJobSummary>(withQuery('/ui/api/batches/summary', params), { signal }),
  facets: (params?: { created_after?: string }, signal?: AbortSignal) =>
    apiFetch<BatchFacets>(withQuery('/ui/api/batches/facets', params), { signal }),
  get: (batchId: string, params?: BatchItemQuery, signal?: AbortSignal) =>
    apiFetch<BatchJobDetail>(withQuery(`/ui/api/batches/${encodeURIComponent(batchId)}`, params), { signal }),
  errors: (batchId: string, signal?: AbortSignal) =>
    apiFetch<BatchErrorGroups>(`/ui/api/batches/${encodeURIComponent(batchId)}/errors`, { signal }),
  webhookDeliveries: (batchId: string) =>
    apiFetch<BatchWebhookDeliveryList>(
      `/ui/api/batches/${encodeURIComponent(batchId)}/webhook-deliveries`,
    ),
  costs: (batchId: string, signal?: AbortSignal) =>
    apiFetch<BatchJobCosts>(`/ui/api/batches/${encodeURIComponent(batchId)}/costs`, { signal }),
  getItem: (batchId: string, itemId: string, signal?: AbortSignal) =>
    apiFetch<BatchJobItemDetail>(
      `/ui/api/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}`,
      { signal },
    ),
  cancel: (batchId: string) => apiFetch<{ batch_id: string; status: string }>(`/ui/api/batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' }),
  retryFinalization: (batchId: string) =>
    apiFetch<{ batch_id: string; status: string; retried: boolean }>(
      `/ui/api/batches/${encodeURIComponent(batchId)}/retry-finalization`,
      { method: 'POST' },
    ),
  requeueStale: (batchId: string) =>
    apiFetch<{ batch_id: string; status: string; requeued_items: number }>(
      `/ui/api/batches/${encodeURIComponent(batchId)}/requeue-stale`,
      { method: 'POST' },
    ),
  markFailed: (batchId: string, reason?: string) =>
    apiFetch<{ batch_id: string; current_status: string; failed_items: number }>(
      `/ui/api/batches/${encodeURIComponent(batchId)}/mark-failed`,
      { method: 'POST', json: { reason: reason || null } },
    ),
  replayWebhook: (batchId: string, eventId: string) =>
    apiFetch<BatchWebhookReplayResponse>(
      `/ui/api/batches/${encodeURIComponent(batchId)}/webhook-deliveries/${encodeURIComponent(eventId)}/replay`,
      { method: 'POST' },
    ),
};
