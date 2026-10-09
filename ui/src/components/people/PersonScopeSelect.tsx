import { useEffect, useId, useMemo, useState } from 'react';
import { teams } from '../../lib/api';
import { organizationRecordsApi } from '../../lib/api/organizations';
import { useApi } from '../../lib/hooks';
import { personInputClass } from '../../lib/peopleForm';
import Button from '../Button';

type Kind = 'organization' | 'team';
type Option = { id: string; label: string; organizationName?: string | null };
export type ScopeSelectionState = { kind: Kind; value: string; loading: boolean; error: string | null; selected: Option | null };
interface Props {
  kind: Kind; value: string; onChange: (value: string) => void;
  onStatusChange: (status: ScopeSelectionState) => void;
  disabled?: boolean; errorId?: string;
}
const PAGE_SIZE = 50;

export default function PersonScopeSelect({ kind, value, onChange, onStatusChange, disabled, errorId }: Props) {
  const id = useId();
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(searchInput.trim()); setOffset(0); }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);
  const queryKey = JSON.stringify([kind, search, offset]);
  const page = useApi(async (signal) => {
    const params = { search: search || undefined, offset, limit: PAGE_SIZE };
    if (kind === 'organization') {
      const result = await organizationRecordsApi.list(params, signal);
      return { key: queryKey, options: result.data.map((item): Option => ({ id: item.organization_id, label: item.organization_name || item.organization_id })), pagination: result.pagination };
    }
    const result = await teams.list(params, signal);
    return { key: queryKey, options: result.data.map((item): Option => ({ id: item.team_id, label: item.team_alias || item.team_id, organizationName: item.organization_name })), pagination: result.pagination };
  }, [queryKey]);
  const currentPage = page.data?.key === queryKey ? page.data : null;
  const fromPage = currentPage?.options.find((option) => option.id === value);
  const selectedKey = JSON.stringify([kind, value]);
  const selectedQuery = useApi(async (signal) => {
    if (!value || fromPage) return null;
    if (kind === 'organization') {
      const item = await organizationRecordsApi.get(value, signal);
      if (item.organization_id !== value) throw new Error('Unable to confirm this organization.');
      return { key: selectedKey, option: { id: item.organization_id, label: item.organization_name || item.organization_id } as Option };
    }
    const item = await teams.get(value, signal);
    if (item.team_id !== value) throw new Error('Unable to confirm this team.');
    return { key: selectedKey, option: { id: item.team_id, label: item.team_alias || item.team_id, organizationName: item.organization_name } as Option };
  }, [selectedKey, fromPage?.id]);
  const fetched = selectedQuery.data?.key === selectedKey ? selectedQuery.data.option : null;
  const selected = fromPage ?? fetched;
  const loadError = page.error || (!fromPage && selectedQuery.error);
  const error = loadError ? loadError instanceof Error ? loadError.message : `Unable to load ${kind} access.` : null;
  const pageLoading = page.loading || (!currentPage && !page.error);
  const loading = pageLoading || Boolean(value && !fromPage && (selectedQuery.loading || (!fetched && !selectedQuery.error)));
  const options = useMemo(() => selected ? [selected, ...(currentPage?.options ?? []).filter((item) => item.id !== selected.id)] : currentPage?.options ?? [], [currentPage, selected]);
  useEffect(() => { onStatusChange({ kind, value, loading, error, selected: selected ?? null }); }, [kind, value, loading, error, selected, onStatusChange]);
  const plural = kind === 'organization' ? 'organizations' : 'teams';
  const pagination = currentPage?.pagination;
  return <div className="min-w-0 space-y-2">
    <label htmlFor={id} className="block text-sm font-medium text-gray-700">{kind === 'organization' ? 'Organization' : 'Team'}</label>
    {(searchInput || search || offset > 0 || (pagination?.total ?? 0) > PAGE_SIZE || pagination?.has_more) && <>
      <label htmlFor={`${id}-search`} className="sr-only">Search {plural}</label>
      <input id={`${id}-search`} type="search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder={`Search ${plural}…`} disabled={disabled} className={personInputClass} />
    </>}
    <select id={id} data-person-field={kind === 'organization' ? 'organizationId' : 'teamId'} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled || pageLoading || Boolean(page.error)} aria-describedby={errorId || `${id}-state`} aria-invalid={Boolean(errorId) || undefined} className={personInputClass}>
      <option value="">{loading ? `Loading ${plural}…` : `Select ${kind === 'organization' ? 'an organization' : 'a team'}`}</option>
      {value && !selected && <option value={value} disabled>{value} ({loading ? 'loading' : 'unavailable'})</option>}
      {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
    </select>
    <div id={`${id}-state`}>
      {error ? <div className="space-y-1"><p role="alert" className="text-xs leading-5 text-red-700">{error}</p><Button size="sm" variant="link" disabled={disabled} onClick={() => { page.refetch(); selectedQuery.refetch(); }}>Retry</Button></div> : loading ? <p role="status" className="text-xs text-gray-500">Loading {plural}…</p> : currentPage?.options.length === 0 ? <p className="text-xs text-gray-500">{search ? 'No matching results.' : `No ${plural} available.`}</p> : null}
    </div>
    {pagination && (pagination.has_more || offset > 0) && <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-xs text-gray-500">Page {Math.floor(offset / PAGE_SIZE) + 1} · {pagination.total} {plural}</p>
      <div className="flex gap-1"><Button variant="ghost" size="sm" disabled={disabled || pageLoading || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button><Button variant="ghost" size="sm" disabled={disabled || pageLoading || !pagination.has_more} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button></div>
    </div>}
  </div>;
}
