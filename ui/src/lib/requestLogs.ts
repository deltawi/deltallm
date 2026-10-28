import type { RequestLog, RequestLogDetails, RequestLogSort } from './api/usageReports';
import { fmtDuration, fmtSpendValue } from './format';
import { providerDisplayName } from './providers';
import { keyTail } from './usageFilters';

export function parseLogTime(value: string | null | undefined): Date | null {
  if (!value) return null;
  const hasZone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(value);
  const parsed = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function relativeTime(value: string | null | undefined, now = Date.now()): string {
  const date = parseLogTime(value);
  if (!date) return '—';
  const seconds = Math.max(0, Math.round((now - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function utcTimestamp(value: string | null | undefined): string {
  const date = parseLogTime(value);
  return date ? `${date.toISOString().replace('T', ' ').slice(0, 19)} UTC` : '';
}

export function logDurationMs(log: RequestLog): number | null {
  if (typeof log.latency_ms === 'number') return log.latency_ms;
  if (typeof log.duration_ms === 'number') return log.duration_ms;
  const start = parseLogTime(log.start_time);
  const end = parseLogTime(log.end_time);
  return start && end ? Math.max(0, end.getTime() - start.getTime()) : null;
}

export function logStatusCode(log: RequestLog): string {
  if (log.http_status_code) return String(log.http_status_code);
  return log.status === 'error' ? 'Error' : '200';
}

export function logProvider(log: RequestLog): string {
  return log.provider ? providerDisplayName(log.provider) : '';
}

export function logKeyName(log: RequestLog): string {
  return log.key_name || (log.api_key ? `Key ${keyTail(log.api_key)}` : '—');
}

/** Labels for the stored call types. Unknown types show as stored. */
export const CALL_TYPES: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'completion', label: 'Chat and text' },
  { key: 'embedding', label: 'Embeddings' },
  { key: 'image_generation', label: 'Images' },
  { key: 'audio_speech', label: 'Speech' },
  { key: 'audio_transcription', label: 'Transcription' },
  { key: 'realtime_transcription', label: 'Realtime transcription' },
  { key: 'rerank', label: 'Rerank' },
  { key: 'mcp_tool', label: 'MCP tool' },
];

export function callTypeLabel(callType: string | null | undefined): string {
  if (!callType) return '—';
  return CALL_TYPES.find((item) => item.key === callType)?.label ?? callType;
}

export type RequestLogColumn =
  | 'person'
  | 'organization'
  | 'deployment'
  | 'endpoint'
  | 'end_user'
  | 'tags'
  | 'error'
  | 'cached'
  | 'request_id';

/** Columns a viewer can add with the Columns menu, in table order. */
export const REQUEST_LOG_COLUMNS: ReadonlyArray<{ key: RequestLogColumn; label: string }> = [
  { key: 'person', label: 'Person' },
  { key: 'organization', label: 'Organization' },
  { key: 'endpoint', label: 'Endpoint' },
  { key: 'deployment', label: 'Deployment' },
  { key: 'end_user', label: 'End user' },
  { key: 'tags', label: 'Tags' },
  { key: 'error', label: 'Error type' },
  { key: 'cached', label: 'Cached tokens' },
  { key: 'request_id', label: 'Request ID' },
];

const COLUMNS_STORAGE_KEY = 'deltallm.requests.columns';

export function readRequestLogColumns(storage?: Pick<Storage, 'getItem'>): RequestLogColumn[] {
  try {
    const parsed: unknown = JSON.parse((storage ?? globalThis.localStorage)?.getItem(COLUMNS_STORAGE_KEY) ?? '[]');
    if (!Array.isArray(parsed)) return [];
    return REQUEST_LOG_COLUMNS.map((column) => column.key).filter((key) => parsed.includes(key));
  } catch {
    return [];
  }
}

export function saveRequestLogColumns(columns: readonly RequestLogColumn[], storage?: Pick<Storage, 'setItem'>): void {
  try {
    (storage ?? globalThis.localStorage)?.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(columns));
  } catch {
    // Private windows and blocked storage: the choice lasts for this visit only.
  }
}

export type RequestDetailKey = keyof RequestLogDetails;

// Page URL parameter of each request log detail filter.
const DETAIL_PARAMS: Record<RequestDetailKey, string> = {
  tag: 'tag',
  endUser: 'end_user',
  callType: 'endpoint',
  deployment: 'deployment',
  httpStatus: 'http_status',
  errorType: 'error_type',
  minSpend: 'min_cost',
  minDurationMs: 'min_ms',
};

export const REQUEST_DETAIL_PARAMS: readonly string[] = Object.values(DETAIL_PARAMS);

function readNumber(value: string | null, { min, max, integer }: { min: number; max: number; integer: boolean }): number | undefined {
  if (value === null || value.trim() === '') return undefined;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < min || parsed > max) return undefined;
  if (integer && !Number.isInteger(parsed)) return undefined;
  return parsed;
}

function readText(value: string | null): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed.slice(0, 256) : undefined;
}

export function parseRequestDetails(params: URLSearchParams): RequestLogDetails {
  const details: RequestLogDetails = {
    tag: readText(params.get(DETAIL_PARAMS.tag)),
    endUser: readText(params.get(DETAIL_PARAMS.endUser)),
    callType: readText(params.get(DETAIL_PARAMS.callType)),
    deployment: readText(params.get(DETAIL_PARAMS.deployment)),
    httpStatus: readNumber(params.get(DETAIL_PARAMS.httpStatus), { min: 100, max: 599, integer: true }),
    errorType: readText(params.get(DETAIL_PARAMS.errorType)),
    minSpend: readNumber(params.get(DETAIL_PARAMS.minSpend), { min: 0, max: 1_000_000_000, integer: false }),
    minDurationMs: readNumber(params.get(DETAIL_PARAMS.minDurationMs), { min: 0, max: 86_400_000, integer: true }),
  };
  return Object.fromEntries(Object.entries(details).filter(([, value]) => value !== undefined)) as RequestLogDetails;
}

export function writeRequestDetails(params: URLSearchParams, details: RequestLogDetails): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const [key, param] of Object.entries(DETAIL_PARAMS) as Array<[RequestDetailKey, string]>) {
    const value = details[key];
    if (value === undefined || value === '') next.delete(param);
    else next.set(param, String(value));
  }
  return next;
}

export function requestDetailsKey(details: RequestLogDetails): string {
  return (Object.keys(DETAIL_PARAMS) as RequestDetailKey[])
    .filter((key) => details[key] !== undefined)
    .map((key) => `${key}=${details[key]}`)
    .join('&');
}

export const REQUEST_DETAIL_LABELS: Record<RequestDetailKey, string> = {
  tag: 'Tag',
  endUser: 'End user',
  callType: 'Endpoint',
  deployment: 'Deployment',
  httpStatus: 'HTTP status',
  errorType: 'Error type',
  minSpend: 'Cost',
  minDurationMs: 'Duration',
};

export function requestDetailValue(key: RequestDetailKey, details: RequestLogDetails): string | undefined {
  const value = details[key];
  if (value === undefined) return undefined;
  if (key === 'callType') return callTypeLabel(String(value));
  if (key === 'minSpend') return `≥ ${fmtSpendValue(Number(value))}`;
  if (key === 'minDurationMs') return `≥ ${fmtDuration(Number(value))}`;
  return String(value);
}

/** Parses typed text into the filter value; undefined when the text is not valid for the field. */
export function parseDetailInput(key: RequestDetailKey, text: string): string | number | undefined {
  const value = text.trim();
  if (!value) return undefined;
  if (key === 'httpStatus') {
    const code = Number(value);
    return Number.isInteger(code) && code >= 100 && code <= 599 ? code : undefined;
  }
  if (key === 'minSpend') {
    const amount = Number(value.replace(/^\$/, ''));
    return Number.isFinite(amount) && amount >= 0 ? amount : undefined;
  }
  if (key === 'minDurationMs') {
    const seconds = Number(value.replace(/\s*s$/i, ''));
    return Number.isFinite(seconds) && seconds >= 0 && seconds <= 86_400 ? Math.round(seconds * 1000) : undefined;
  }
  return value.slice(0, 256);
}

export function parseRequestSort(value: string | null): RequestLogSort {
  return value === 'cost' || value === 'duration' ? value : 'newest';
}

/** Sorting by cost or duration reads the whole window, so the server allows it for 31 days at most. */
export const MAX_SORTED_WINDOW_MS = 31 * 24 * 3_600_000;

export function sortAllowed(range: { since: string; until?: string }, now = Date.now()): boolean {
  const since = Date.parse(range.since);
  const until = range.until ? Date.parse(range.until) : now;
  return Number.isFinite(since) && Number.isFinite(until) && until - since <= MAX_SORTED_WINDOW_MS;
}
