export type KeyOwnerFilter = 'person' | 'service_account' | 'unassigned';
export type KeyStatusFilter = 'active' | 'expired' | 'expiring' | 'budget_exhausted';
export type KeyLastUsedFilter = 'never' | 'active_7d' | 'active_30d' | 'inactive_30d' | 'inactive_90d';
export type KeyTypeFilter = 'coding_agent' | 'standard';
export type KeySortKey = 'created_at' | 'name' | 'last_used' | 'expires' | 'budget_used';
export type KeySortDirection = 'asc' | 'desc';

export interface KeyListFilters {
  team_id: string;
  organization_id: string;
  owner: KeyOwnerFilter | '';
  status: KeyStatusFilter | '';
  last_used: KeyLastUsedFilter | '';
  key_type: KeyTypeFilter | '';
}

export interface KeyListSort { key: KeySortKey; direction: KeySortDirection }

export const EMPTY_KEY_LIST_FILTERS: KeyListFilters = {
  team_id: '',
  organization_id: '',
  owner: '',
  status: '',
  last_used: '',
  key_type: '',
};

export const DEFAULT_KEY_LIST_SORT: KeyListSort = { key: 'created_at', direction: 'desc' };

export const KEY_OWNER_OPTIONS: { value: KeyOwnerFilter; label: string }[] = [
  { value: 'person', label: 'People' },
  { value: 'service_account', label: 'Service accounts' },
  { value: 'unassigned', label: 'Unassigned' },
];

export const KEY_STATUS_OPTIONS: { value: KeyStatusFilter; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'expiring', label: 'Expiring in 7 days' },
  { value: 'expired', label: 'Expired' },
  { value: 'budget_exhausted', label: 'Budget used up' },
];

export const KEY_LAST_USED_OPTIONS: { value: KeyLastUsedFilter; label: string }[] = [
  { value: 'active_7d', label: 'Used in last 7 days' },
  { value: 'active_30d', label: 'Used in last 30 days' },
  { value: 'inactive_30d', label: 'Not used in 30+ days' },
  { value: 'inactive_90d', label: 'Not used in 90+ days' },
  { value: 'never', label: 'Never used' },
];

export const KEY_TYPE_OPTIONS: { value: KeyTypeFilter; label: string }[] = [
  { value: 'coding_agent', label: 'Coding agent keys' },
  { value: 'standard', label: 'Standard keys' },
];

// The first direction a sort key takes when picked: newest, latest or fullest first.
export const KEY_SORT_OPTIONS: { key: KeySortKey; label: string; defaultDirection: KeySortDirection }[] = [
  { key: 'created_at', label: 'Created', defaultDirection: 'desc' },
  { key: 'name', label: 'Name', defaultDirection: 'asc' },
  { key: 'last_used', label: 'Last used', defaultDirection: 'desc' },
  { key: 'expires', label: 'Expires', defaultDirection: 'asc' },
  { key: 'budget_used', label: 'Budget used', defaultDirection: 'desc' },
];

export function isKeySortKey(value: string): value is KeySortKey {
  return KEY_SORT_OPTIONS.some((option) => option.key === value);
}

export function activeKeyFilterCount(filters: KeyListFilters): number {
  return Object.values(filters).filter(Boolean).length;
}

/** Query parameters for GET /ui/api/keys; empty filters are left out. */
export function keyListQuery(filters: KeyListFilters, sort: KeyListSort) {
  return {
    team_id: filters.team_id || undefined,
    organization_id: filters.organization_id || undefined,
    owner: filters.owner || undefined,
    status: filters.status || undefined,
    last_used: filters.last_used || undefined,
    coding_agent: filters.key_type ? filters.key_type === 'coding_agent' : undefined,
    sort_by: sort.key,
    sort_direction: sort.direction,
  };
}
