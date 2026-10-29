/** Requests tab and Overview tab state read from the Usage page URL. */
import type { UsageDimension } from './api/usageReports';

export interface RequestFlags {
  status?: 'success' | 'error';
  cacheHit?: boolean;
}

/** Status and cache flags of the Requests tab, from ``status`` and ``cache`` in the URL. */
export function parseRequestFlags(params: URLSearchParams): RequestFlags {
  const status = params.get('status');
  const cache = params.get('cache');
  return {
    status: status === 'error' || status === 'success' ? status : undefined,
    cacheHit: cache === 'hit' ? true : cache === 'miss' ? false : undefined,
  };
}

/** The Overview "Top by" tab from ``tab`` in the URL, or ``model`` when it is missing or not allowed. */
export function parseUsageTab(params: URLSearchParams, allowed: readonly UsageDimension[]): UsageDimension {
  const requested = params.get('tab');
  return allowed.find((dimension) => dimension === requested) ?? 'model';
}
