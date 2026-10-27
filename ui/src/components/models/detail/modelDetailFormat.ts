import type { DeploymentHealth } from '../../../lib/api';

export type ModelDetailTabId = 'connection' | 'limits' | 'pricing' | 'settings';

export const EMPTY_DEPLOYMENT_HEALTH: DeploymentHealth = {
  healthy: false,
  in_cooldown: false,
  consecutive_failures: 0,
  last_error: null,
  last_error_at: null,
  last_success_at: null,
};

export function formatInteger(value: unknown): string | null {
  if (value == null || value === '') return null;
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  return num.toLocaleString();
}

/** 128000 -> "128K", 1047576 -> "1M". */
export function formatCompactTokens(value: unknown): string | null {
  if (value == null || value === '') return null;
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return null;
  if (num >= 1_000_000) return `${Number((num / 1_000_000).toFixed(1))}M`;
  if (num >= 1000) return `${Math.round(num / 1000)}K`;
  return String(num);
}

export function formatSeconds(value: unknown): string | null {
  const formatted = formatInteger(value);
  return formatted ? `${formatted} s` : null;
}

export function formatFraction(value: unknown): string | null {
  if (value == null || value === '') return null;
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  return num.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export function formatUnixTimestamp(value: number | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString();
}

export function timeSince(ts: number | null | undefined): string | null {
  if (!ts) return null;
  const secs = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (secs < 60) return `${secs} s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86_400) return `${Math.floor(secs / 3600)} h ago`;
  return `${Math.floor(secs / 86_400)} d ago`;
}

export function formatDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function formatParamValue(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}
