import { useEffect, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, Search } from 'lucide-react';
import DataTable, { type Column, type TableSort } from '../../DataTable';
import { ContentCard } from '../shells';
import { ApiError, type Pagination } from '../../../lib/api';

export interface ListSortOption { key: string; label: string; defaultDirection?: 'asc' | 'desc' }

interface AssetListProps<T> {
  columns: Column<T>[];
  data: T[];
  rowKey: (row: T) => string;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  emptyMessage: string;
  search: string;
  searchLabel: string;
  onSearchChange: (search: string) => void;
  sort: TableSort;
  sortOptions?: ListSortOption[];
  onSortChange: (sort: TableSort) => void;
  pagination?: Pagination;
  onPageChange: (offset: number) => void;
  onRowClick: (row: T) => void;
  filters?: ReactNode;
}

export default function AssetList<T extends object>(props: AssetListProps<T>) {
  const { pagination, loading, error, onPageChange } = props;
  const lastOffset = pagination && pagination.limit > 0
    ? Math.max(0, (Math.ceil(pagination.total / pagination.limit) - 1) * pagination.limit)
    : null;
  const invalidOffset = pagination && lastOffset !== null && pagination.offset > lastOffset;
  useEffect(() => {
    if (!loading && !error && invalidOffset && lastOffset !== null) onPageChange(lastOffset);
  }, [loading, error, invalidOffset, lastOffset, onPageChange]);
  const denied = props.error instanceof ApiError && props.error.status === 403;
  const hasRows = props.data.length > 0 && !denied;
  const sortOptions = props.sortOptions || props.columns.flatMap((column) => column.sortKey ? [{ key: column.sortKey, label: column.header, defaultDirection: column.defaultDirection }] : []);
  const label = sortOptions.find((option) => option.key === props.sort.key)?.label || 'Updated at';
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative w-full sm:w-80">
          <span className="sr-only">{props.searchLabel}</span><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
          <input type="search" value={props.search} onChange={(event) => props.onSearchChange(event.target.value)} placeholder={`${props.searchLabel}…`} className="h-10 w-full rounded-lg border border-gray-200 bg-white pl-9 pr-3 text-base focus:outline-none focus:ring-2 focus:ring-brand-primary md:text-sm" />
        </label>
        {props.filters}
        <div className="flex max-w-full min-w-0 items-center gap-2 sm:ml-auto">
          <label className="flex min-w-0 items-center gap-2"><span className="hidden text-xs text-gray-500 lg:inline">Sort by</span>
            <select aria-label="Sort list" value={props.sort.key} onChange={(event) => { const option = sortOptions.find((item) => item.key === event.target.value); if (option) props.onSortChange({ key: option.key, direction: option.defaultDirection || 'asc' }); }} className="h-10 max-w-full rounded-lg border border-gray-200 bg-white px-3 text-base text-gray-700 focus:outline-none focus:ring-2 focus:ring-brand-primary md:text-sm">
              {sortOptions.map((option) => <option key={option.key} value={option.key}>{option.label}{option.key === props.sort.key && ['updated_at', 'health'].includes(option.key) ? ` · ${option.key === 'updated_at' ? props.sort.direction === 'desc' ? 'Newest first' : 'Oldest first' : props.sort.direction === 'asc' ? 'Attention first' : 'Healthy first'}` : ''}</option>)}
            </select>
          </label>
          <button type="button" aria-label={`Reverse ${label.toLowerCase()} sort direction`} onClick={() => props.onSortChange({ key: props.sort.key, direction: props.sort.direction === 'asc' ? 'desc' : 'asc' })} className="flex h-10 w-10 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary">{props.sort.direction === 'asc' ? <ArrowUp className="h-4 w-4" /> : <ArrowDown className="h-4 w-4" />}</button>
        </div>
      </div>
      {props.error ? <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"><span>{denied ? 'You do not have access to this list.' : hasRows ? 'Could not update the list. The previous result is shown.' : 'Could not load the list.'}</span>{!denied ? <button type="button" onClick={props.onRetry} className="rounded px-2 py-1 font-medium underline focus-visible:outline focus-visible:outline-2">Retry</button> : null}</div> : null}
      {props.loading && hasRows ? <p role="status" className="text-xs text-gray-500">Updating the list…</p> : null}
      {!props.error || hasRows ? <ContentCard><div aria-busy={props.loading}><DataTable columns={props.columns} data={props.data} loading={props.loading && !hasRows} responsive rowKey={props.rowKey} caption={`${props.searchLabel.replace('Search ', '')}. Sorted by ${label.toLowerCase()}, ${props.sort.direction === 'asc' ? 'ascending' : 'descending'}.`} sort={props.sort} onSortChange={props.onSortChange} emptyMessage={props.emptyMessage} pagination={props.pagination} onPageChange={props.onPageChange} onRowClick={props.onRowClick} /></div></ContentCard> : null}
    </div>
  );
}
