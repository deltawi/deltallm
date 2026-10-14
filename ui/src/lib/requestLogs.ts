import type { RequestLog } from './api/usageReports';
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
