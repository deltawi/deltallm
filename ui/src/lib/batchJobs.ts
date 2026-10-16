/** Batch jobs view model: status names, list tabs, URL filters, progress and time left. */
import type {
  BatchEndpoint,
  BatchItemStatus,
  BatchJobSummary,
  BatchListFilters,
} from './api/batches';

export type BatchTone = 'ok' | 'bad' | 'run' | 'warn' | 'idle';

const JOB_STATUS: Record<string, { label: string; tone: BatchTone }> = {
  validating: { label: 'Validating', tone: 'run' },
  queued: { label: 'Queued', tone: 'idle' },
  in_progress: { label: 'Running', tone: 'run' },
  finalizing: { label: 'Finalizing', tone: 'run' },
  completed: { label: 'Completed', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
  cancelled: { label: 'Cancelled', tone: 'idle' },
  expired: { label: 'Expired', tone: 'warn' },
};

const ITEM_STATUS: Record<string, { label: string; tone: BatchTone }> = {
  pending: { label: 'Waiting', tone: 'idle' },
  in_progress: { label: 'Running', tone: 'run' },
  completed: { label: 'Done', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
  cancelled: { label: 'Cancelled', tone: 'idle' },
};

const WEBHOOK_STATUS: Record<string, { label: string; tone: BatchTone }> = {
  pending: { label: 'Pending', tone: 'idle' },
  processing: { label: 'Sending', tone: 'run' },
  retrying: { label: 'Retrying', tone: 'warn' },
  delivered: { label: 'Delivered', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
};

function lookup(map: Record<string, { label: string; tone: BatchTone }>, status: string) {
  return map[status] ?? { label: status || 'Unknown', tone: 'idle' as const };
}

export const jobStatus = (status: string) => lookup(JOB_STATUS, status);
export const itemStatus = (status: string) => lookup(ITEM_STATUS, status);
export const webhookStatus = (status: string) => lookup(WEBHOOK_STATUS, status);

export const ACTIVE_JOB_STATUSES = new Set(['validating', 'queued', 'in_progress', 'finalizing']);

export function isActiveJob(status: string): boolean {
  return ACTIVE_JOB_STATUSES.has(status);
}

/** Cancel shows only for a job that is still running and that the caller may change. */
export function canCancelJob(job: {
  status: string;
  capabilities?: { cancel?: boolean } | null;
  cancel_requested_at?: string | null;
}): boolean {
  return Boolean(job.capabilities?.cancel) && !job.cancel_requested_at && isActiveJob(job.status);
}

export function endpointLabel(endpoint: string | null | undefined): string {
  if (endpoint === '/v1/embeddings') return 'Embeddings';
  if (endpoint === '/v1/chat/completions') return 'Chat';
  return endpoint || 'Unknown';
}

export const BATCH_ENDPOINTS: ReadonlyArray<{ value: BatchEndpoint; label: string }> = [
  { value: '/v1/embeddings', label: 'Embeddings' },
  { value: '/v1/chat/completions', label: 'Chat' },
];

export type BatchTab = 'all' | 'running' | 'queued' | 'completed' | 'failed' | 'cancelled' | 'expired';

export const BATCH_TABS: ReadonlyArray<{
  key: BatchTab;
  label: string;
  statuses: string;
  count: (summary: BatchJobSummary) => number;
  tone?: BatchTone;
}> = [
  { key: 'all', label: 'All', statuses: '', count: (s) => s.total },
  { key: 'running', label: 'Running', statuses: 'in_progress,finalizing', count: (s) => s.in_progress, tone: 'run' },
  { key: 'queued', label: 'Queued', statuses: 'queued', count: (s) => s.queued },
  { key: 'completed', label: 'Completed', statuses: 'completed', count: (s) => s.completed },
  { key: 'failed', label: 'Failed', statuses: 'failed', count: (s) => s.failed, tone: 'bad' },
  { key: 'cancelled', label: 'Cancelled', statuses: 'cancelled', count: (s) => s.cancelled },
  { key: 'expired', label: 'Expired', statuses: 'expired', count: (s) => s.expired ?? 0 },
];

export type BatchWindow = '24h' | '7d' | '30d' | 'all';

export const BATCH_WINDOWS: ReadonlyArray<{ key: BatchWindow; label: string; hours: number | null }> = [
  { key: '24h', label: '24 h', hours: 24 },
  { key: '7d', label: '7 days', hours: 24 * 7 },
  { key: '30d', label: '30 days', hours: 24 * 30 },
  { key: 'all', label: 'All', hours: null },
];

export interface BatchListState {
  tab: BatchTab;
  window: BatchWindow;
  search: string;
  model: string | null;
  endpoint: BatchEndpoint | null;
  teamId: string | null;
  page: number;
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

export function parseBatchListState(params: URLSearchParams): BatchListState {
  const page = Number.parseInt(params.get('page') ?? '', 10);
  const endpoint = params.get('endpoint');
  return {
    tab: oneOf(params.get('status'), BATCH_TABS.map((tab) => tab.key), 'all'),
    window: oneOf(params.get('window'), BATCH_WINDOWS.map((item) => item.key), 'all'),
    search: params.get('q') ?? '',
    model: params.get('model') || null,
    endpoint: BATCH_ENDPOINTS.some((item) => item.value === endpoint) ? (endpoint as BatchEndpoint) : null,
    teamId: params.get('team') || null,
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
}

/** Write the state back to the URL. Defaults are left out, so a clean list has a clean link. */
export function writeBatchListState(state: BatchListState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.tab !== 'all') params.set('status', state.tab);
  if (state.window !== 'all') params.set('window', state.window);
  if (state.search.trim()) params.set('q', state.search.trim());
  if (state.model) params.set('model', state.model);
  if (state.endpoint) params.set('endpoint', state.endpoint);
  if (state.teamId) params.set('team', state.teamId);
  if (state.page > 1) params.set('page', String(state.page));
  return params;
}

export function hasBatchFilters(state: BatchListState): boolean {
  return Boolean(state.search.trim() || state.model || state.endpoint || state.teamId);
}

/** API filters for the state. ``now`` is a parameter so tests can fix the clock. */
export function batchListFilters(state: BatchListState, now: Date = new Date()): BatchListFilters {
  const hours = BATCH_WINDOWS.find((item) => item.key === state.window)?.hours ?? null;
  return {
    search: state.search.trim() || undefined,
    status: BATCH_TABS.find((tab) => tab.key === state.tab)?.statuses || undefined,
    model: state.model ?? undefined,
    endpoint: state.endpoint ?? undefined,
    team_id: state.teamId ?? undefined,
    created_after: hours === null ? undefined : new Date(now.getTime() - hours * 3_600_000).toISOString(),
  };
}

export interface ProgressCounts {
  total: number;
  completed_items: number;
  failed_items: number;
  in_progress_items: number;
  cancelled_items: number;
}

export interface BatchProgress {
  total: number;
  done: number;
  failed: number;
  running: number;
  cancelled: number;
  waiting: number;
  /** Lines that are finished in any way, as a whole percent rounded down. */
  percent: number;
}

export function batchProgress(counts: ProgressCounts): BatchProgress {
  const total = Math.max(0, counts.total);
  const done = Math.max(0, counts.completed_items);
  const failed = Math.max(0, counts.failed_items);
  const running = Math.max(0, counts.in_progress_items);
  const cancelled = Math.max(0, counts.cancelled_items);
  const waiting = Math.max(0, total - done - failed - running - cancelled);
  const finished = done + failed + cancelled;
  return {
    total,
    done,
    failed,
    running,
    cancelled,
    waiting,
    percent: total > 0 ? Math.floor((Math.min(finished, total) / total) * 100) : 0,
  };
}

export interface BatchPace {
  /** Finished lines per minute since the job started. */
  perMinute: number;
  /** Milliseconds left at that pace. */
  remainingMs: number;
}

/**
 * Pace from the start time and the finished count. This is an estimate: it
 * assumes the rest of the lines run at the average speed so far.
 */
export function batchPace(
  progress: BatchProgress,
  startedAt: string | null | undefined,
  now: number = Date.now(),
): BatchPace | null {
  if (!startedAt) return null;
  const elapsedMs = now - new Date(startedAt).getTime();
  const finished = progress.done + progress.failed + progress.cancelled;
  // Under a minute or a few lines, the average is too noisy to show.
  if (!Number.isFinite(elapsedMs) || elapsedMs < 60_000 || finished < 5) return null;
  const perMs = finished / elapsedMs;
  const left = progress.waiting + progress.running;
  return { perMinute: perMs * 60_000, remainingMs: left / perMs };
}

export function elapsedMs(
  start: string | null | undefined,
  end: string | null | undefined,
  now: number = Date.now(),
): number | null {
  if (!start) return null;
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : now;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return null;
  return Math.max(0, endMs - startMs);
}

/** "5 min ago", "3 h ago", then a short date. ``short`` leaves the time out of older dates. */
export function fmtAgo(value: string | null | undefined, now: number = Date.now(), short = false): string {
  if (!value) return '—';
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return '—';
  const seconds = Math.max(0, Math.round((now - time) / 1000));
  if (seconds < 60) return 'Just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  if (short) return new Date(time).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return new Date(time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const time = new Date(value);
  if (!Number.isFinite(time.getTime())) return '—';
  return time.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export const BATCH_ITEM_FILTERS: ReadonlyArray<{
  key: BatchItemStatus | 'all';
  label: string;
  count: (progress: BatchProgress) => number;
}> = [
  { key: 'all', label: 'All', count: (p) => p.total },
  { key: 'failed', label: 'Failed', count: (p) => p.failed },
  { key: 'in_progress', label: 'Running', count: (p) => p.running },
  { key: 'completed', label: 'Done', count: (p) => p.done },
  { key: 'pending', label: 'Waiting', count: (p) => p.waiting },
];

/** Money for tables: cents above one cent, two significant digits below it. */
export function fmtCost(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value === 0) return '$0';
  if (Math.abs(value) >= 0.01) return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `$${value.toLocaleString('en-US', { maximumSignificantDigits: 2 })}`;
}
