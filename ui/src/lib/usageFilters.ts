import type { SpendCapabilities, SpendView } from './api';
import type { UsageDimension, UsageFilterValue } from './api/usageReports';
import { providerDisplayName } from './providers';

export const USAGE_DIMENSIONS: readonly UsageDimension[] = [
  'model',
  'provider',
  'organization',
  'team',
  'user',
  'api_key',
];

/** Use the requested view when the account may see it, else the default view. */
export function resolveUsageView(
  capabilities: SpendCapabilities | undefined,
  requested: SpendView | null,
): SpendView {
  if (!capabilities) return 'platform';
  return requested && capabilities.available_views.includes(requested)
    ? requested
    : capabilities.default_view;
}

const LABELS: Record<UsageDimension, { singular: string; plural: string; unassigned: string }> = {
  model: { singular: 'Model', plural: 'Models', unassigned: 'Unspecified model' },
  provider: { singular: 'Provider', plural: 'Providers', unassigned: 'Unknown provider' },
  organization: { singular: 'Organization', plural: 'Organizations', unassigned: 'No organization' },
  team: { singular: 'Team', plural: 'Teams', unassigned: 'No team' },
  user: { singular: 'User', plural: 'Users', unassigned: 'No user' },
  api_key: { singular: 'API key', plural: 'API keys', unassigned: 'No API key' },
};

export function dimensionLabel(dimension: UsageDimension, plural = false): string {
  return plural ? LABELS[dimension].plural : LABELS[dimension].singular;
}

export function unassignedLabel(dimension: UsageDimension): string {
  return LABELS[dimension].unassigned;
}

/** Short tail of a key hash, so a key without a name is still recognizable. */
export function keyTail(hash: string | null | undefined): string {
  return hash ? `…${hash.slice(-4)}` : '';
}

export function groupLabel(
  dimension: UsageDimension,
  group: { group_key: string | null; display_name?: string | null; is_unassigned?: boolean },
): string {
  if (group.is_unassigned || group.group_key === null) return unassignedLabel(dimension);
  if (group.display_name) return group.display_name;
  if (dimension === 'provider') return providerDisplayName(group.group_key);
  if (dimension === 'api_key') return `Key ${keyTail(group.group_key)}`;
  return group.group_key;
}

export function filterLabel(filter: UsageFilterValue): string {
  if (filter.unassigned) return unassignedLabel(filter.dimension);
  if (filter.label) return filter.label;
  return groupLabel(filter.dimension, { group_key: filter.value });
}

/** Dimensions the active reporting view can group and filter by (the server checks this too). */
export function allowedUsageDimensions(
  view: SpendView,
  capabilities: SpendCapabilities | undefined,
): UsageDimension[] {
  if (view === 'platform' || view === 'organization' || !capabilities) return [...USAGE_DIMENSIONS];
  const allowed = new Set<UsageDimension>(['model', 'provider', ...capabilities.allowed_dimensions]);
  return USAGE_DIMENSIONS.filter((dimension) => allowed.has(dimension));
}

function isDimension(value: string): value is UsageDimension {
  return (USAGE_DIMENSIONS as readonly string[]).includes(value);
}

export function parseUsageFilters(params: URLSearchParams): UsageFilterValue[] {
  const unassigned = new Set(params.getAll('unassigned').filter(isDimension));
  const filters: UsageFilterValue[] = [];
  for (const dimension of USAGE_DIMENSIONS) {
    if (unassigned.has(dimension)) {
      filters.push({ dimension, value: '', unassigned: true });
      continue;
    }
    const value = params.get(dimension)?.trim();
    if (!value) continue;
    filters.push({ dimension, value, label: params.get(`${dimension}_label`) || null });
  }
  return filters;
}

export function writeUsageFilters(current: URLSearchParams, filters: readonly UsageFilterValue[]): URLSearchParams {
  const next = new URLSearchParams(current);
  next.delete('unassigned');
  for (const dimension of USAGE_DIMENSIONS) {
    next.delete(dimension);
    next.delete(`${dimension}_label`);
  }
  for (const filter of filters) {
    if (filter.unassigned) {
      next.append('unassigned', filter.dimension);
      continue;
    }
    next.set(filter.dimension, filter.value);
    if (filter.label && filter.label !== filter.value) next.set(`${filter.dimension}_label`, filter.label);
  }
  return next;
}

/** Replace the filter for one dimension (a dimension has at most one filter). */
export function withUsageFilter(filters: readonly UsageFilterValue[], filter: UsageFilterValue): UsageFilterValue[] {
  return [...filters.filter((current) => current.dimension !== filter.dimension), filter];
}

export function withoutUsageFilter(filters: readonly UsageFilterValue[], dimension: UsageDimension): UsageFilterValue[] {
  return filters.filter((current) => current.dimension !== dimension);
}

export function usageFiltersKey(filters: readonly UsageFilterValue[]): string {
  return [...filters]
    .sort((a, b) => a.dimension.localeCompare(b.dimension))
    .map((filter) => `${filter.dimension}=${filter.unassigned ? '∅' : filter.value}`)
    .join('&');
}

export type RequestWindowKey = '1h' | '24h' | '7d' | '30d';

export const REQUEST_WINDOWS: ReadonlyArray<{ key: RequestWindowKey; label: string; hours: number }> = [
  { key: '1h', label: '1h', hours: 1 },
  { key: '24h', label: '24h', hours: 24 },
  { key: '7d', label: '7d', hours: 24 * 7 },
  { key: '30d', label: '30d', hours: 24 * 30 },
];

export function parseRequestWindow(value: string | null): RequestWindowKey {
  return REQUEST_WINDOWS.some((window) => window.key === value) ? (value as RequestWindowKey) : '24h';
}

/** Start of a request window, rounded down to the minute so repeated loads share a cache entry. */
export function requestWindowSince(key: RequestWindowKey, now = new Date()): string {
  const hours = REQUEST_WINDOWS.find((window) => window.key === key)?.hours ?? 24;
  const since = new Date(now.getTime() - hours * 3_600_000);
  since.setUTCSeconds(0, 0);
  return since.toISOString();
}
