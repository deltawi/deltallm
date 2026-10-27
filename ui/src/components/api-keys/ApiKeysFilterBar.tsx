import { ArrowDown, ArrowUp, X } from 'lucide-react';
import Button from '../Button';
import {
  EMPTY_KEY_LIST_FILTERS,
  KEY_LAST_USED_OPTIONS,
  KEY_OWNER_OPTIONS,
  KEY_SORT_OPTIONS,
  KEY_STATUS_OPTIONS,
  KEY_TYPE_OPTIONS,
  activeKeyFilterCount,
  type KeyListFilters,
  type KeyListSort,
} from '../../lib/apiKeyListFilters';

export interface KeyFilterChoice { value: string; label: string }

interface ApiKeysFilterBarProps {
  filters: KeyListFilters;
  onFiltersChange: (filters: KeyListFilters) => void;
  teams: KeyFilterChoice[];
  organizations: KeyFilterChoice[];
}

const selectClass = 'control-field-md max-w-full rounded-lg border bg-white px-3 text-base focus:outline-none focus:ring-2 focus:ring-brand-primary md:text-[13px]';

function FilterSelect({ label, value, allLabel, options, onChange }: {
  label: string;
  value: string;
  allLabel: string;
  options: KeyFilterChoice[];
  onChange: (value: string) => void;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={`${selectClass} w-full min-w-0 sm:w-auto ${value ? 'border-brand-primary text-gray-900' : 'border-gray-200 text-gray-700'}`}
    >
      <option value="">{allLabel}</option>
      {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  );
}

export default function ApiKeysFilterBar({ filters, onFiltersChange, teams, organizations }: ApiKeysFilterBarProps) {
  const set = <Field extends keyof KeyListFilters>(field: Field, value: string) => (
    onFiltersChange({ ...filters, [field]: value } as KeyListFilters)
  );
  const activeCount = activeKeyFilterCount(filters);
  // A filter stays visible while it is set, even if its list no longer offers the value.
  const showOrganizations = organizations.length > 1 || Boolean(filters.organization_id);
  const showTeams = teams.length > 1 || Boolean(filters.team_id);

  return (
    <div className="grid grid-cols-2 items-center gap-2 sm:flex sm:flex-wrap">
      {showOrganizations ? (
        <FilterSelect label="Filter by organization" value={filters.organization_id} allLabel="All organizations" options={organizations} onChange={(value) => set('organization_id', value)} />
      ) : null}
      {showTeams ? (
        <FilterSelect label="Filter by team" value={filters.team_id} allLabel="All teams" options={teams} onChange={(value) => set('team_id', value)} />
      ) : null}
      <FilterSelect label="Filter by owner" value={filters.owner} allLabel="All owners" options={KEY_OWNER_OPTIONS} onChange={(value) => set('owner', value)} />
      <FilterSelect label="Filter by status" value={filters.status} allLabel="Any status" options={KEY_STATUS_OPTIONS} onChange={(value) => set('status', value)} />
      <FilterSelect label="Filter by last use" value={filters.last_used} allLabel="Any last use" options={KEY_LAST_USED_OPTIONS} onChange={(value) => set('last_used', value)} />
      <FilterSelect label="Filter by key type" value={filters.key_type} allLabel="All key types" options={KEY_TYPE_OPTIONS} onChange={(value) => set('key_type', value)} />
      {activeCount > 0 ? (
        <Button variant="ghost" className="justify-self-start" onClick={() => onFiltersChange(EMPTY_KEY_LIST_FILTERS)}>
          <X className="h-3.5 w-3.5" /> Clear {activeCount === 1 ? 'filter' : `${activeCount} filters`}
        </Button>
      ) : null}
    </div>
  );
}

export function ApiKeysSortControl({ sort, onSortChange }: { sort: KeyListSort; onSortChange: (sort: KeyListSort) => void }) {
  const sortLabel = KEY_SORT_OPTIONS.find((option) => option.key === sort.key)?.label || 'Created';
  return (
    <div className="flex max-w-full min-w-0 items-center gap-2 sm:ml-auto">
      <label className="flex min-w-0 items-center gap-2">
        <span className="hidden text-xs text-gray-500 lg:inline">Sort by</span>
        <select
          aria-label="Sort keys"
          value={sort.key}
          onChange={(event) => {
            const option = KEY_SORT_OPTIONS.find((item) => item.key === event.target.value);
            if (option) onSortChange({ key: option.key, direction: option.defaultDirection });
          }}
          className={`${selectClass} border-gray-200 text-gray-700`}
        >
          {KEY_SORT_OPTIONS.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
        </select>
      </label>
      <Button
        variant="outline"
        size="icon"
        aria-label={`Reverse ${sortLabel.toLowerCase()} sort, now ${sort.direction === 'asc' ? 'ascending' : 'descending'}`}
        onClick={() => onSortChange({ key: sort.key, direction: sort.direction === 'asc' ? 'desc' : 'asc' })}
      >
        {sort.direction === 'asc' ? <ArrowUp className="h-4 w-4" /> : <ArrowDown className="h-4 w-4" />}
      </Button>
    </div>
  );
}
