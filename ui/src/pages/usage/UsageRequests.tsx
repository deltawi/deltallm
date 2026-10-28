import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import type { SpendCapabilities, SpendView } from '../../lib/api';
import {
  usageReports,
  type RequestLog,
  type RequestLogDetails,
  type RequestLogSort,
  type UsageDimension,
  type UsageFilterValue,
} from '../../lib/api/usageReports';
import {
  parseRequestDetails,
  parseRequestSort,
  readRequestLogColumns,
  REQUEST_DETAIL_PARAMS,
  REQUEST_LOG_COLUMNS,
  requestDetailsKey,
  saveRequestLogColumns,
  sortAllowed,
  writeRequestDetails,
  type RequestLogColumn,
} from '../../lib/requestLogs';
import {
  allowedUsageDimensions,
  chipDimensions,
  parseRequestWindowSelection,
  parseUsageFilters,
  requestTimeRange,
  usageFiltersKey,
  withoutUsageFilter,
  withUsageFilter,
  writeUsageFilters,
} from '../../lib/usageFilters';
import { parseRequestFlags } from '../../lib/usageParams';
import { useReport } from '../../lib/useReport';
import ColumnsMenu from '../../components/audit/ColumnsMenu';
import { PanelError, Segmented } from '../../components/usage/ReportBits';
import RequestDetailChips from '../../components/usage/RequestDetailChips';
import RequestFilterChips, { type RequestFlags } from '../../components/usage/RequestFilterChips';
import RequestLogDrawer from '../../components/usage/RequestLogDrawer';
import RequestLogTable from '../../components/usage/RequestLogTable';

const PAGE_SIZE = 25;
const CHIP_DIMENSIONS: readonly UsageDimension[] = ['model', 'provider', 'team', 'api_key'];
const SORT_LABELS: Record<RequestLogSort, string> = {
  newest: 'newest first',
  cost: 'highest cost first',
  duration: 'slowest first',
};

interface UsageRequestsProps {
  view: SpendView;
  capabilities: SpendCapabilities | undefined;
  refreshToken: number;
  reloadToken: number;
  canOpenAudit: boolean;
}

export default function UsageRequests({ view, capabilities, refreshToken, reloadToken, canOpenAudit }: UsageRequestsProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const allowed = useMemo(() => allowedUsageDimensions(view, capabilities), [capabilities, view]);
  const filters = useMemo(
    () => parseUsageFilters(searchParams).filter((filter) => allowed.includes(filter.dimension)),
    [allowed, searchParams],
  );
  const flags = parseRequestFlags(searchParams);
  const details = useMemo(() => parseRequestDetails(searchParams), [searchParams]);
  const requestedSort = parseRequestSort(searchParams.get('sort'));
  const windowKey = parseRequestWindowSelection(searchParams.get('window'));
  const customFrom = searchParams.get('from');
  const customTo = searchParams.get('to');
  const requestId = searchParams.get('request_id')?.trim() ?? '';
  const [draft, setDraft] = useState(requestId);
  const [draftFor, setDraftFor] = useState(requestId);
  if (draftFor !== requestId) {
    // The URL changed (back or forward): show its search, not an old draft.
    setDraftFor(requestId);
    setDraft(requestId);
  }
  // The key names the window, not its start time, so a reload keeps the page
  // and the rows on screen. Each load computes the start time again.
  const windowId = windowKey === 'custom' ? `custom:${customFrom ?? ''}:${customTo ?? ''}` : windowKey;
  const valueRange = useMemo(() => {
    const bounds = requestTimeRange(windowKey, { from: customFrom, to: customTo });
    return { sinceDate: bounds.since.slice(0, 10), untilDate: bounds.until?.slice(0, 10), canSort: sortAllowed(bounds) };
  }, [customFrom, customTo, windowKey]);
  // A custom window longer than 31 days cannot be sorted by value; it shows newest first.
  const sort: RequestLogSort = valueRange.canSort ? requestedSort : 'newest';
  const queryKey = `${view}|${windowId}|${usageFiltersKey(filters)}|${flags.status ?? ''}|${flags.cacheHit ?? ''}|${requestId}|${requestDetailsKey(details)}|${sort}`;
  const [columns, setColumns] = useState<RequestLogColumn[]>(() => readRequestLogColumns());
  const [pages, setPages] = useState<{ key: string; cursors: Array<string | null> }>({ key: queryKey, cursors: [null] });
  const cursors = pages.key === queryKey ? pages.cursors : [null];
  const cursor = cursors[cursors.length - 1];
  const [selected, setSelected] = useState<RequestLog | null>(null);

  const logs = useReport(
    `requests:logs|${queryKey}|${cursor ?? ''}`,
    (options) => usageReports.requestLogs(
      {
        view,
        ...requestTimeRange(windowKey, { from: customFrom, to: customTo }),
        filters,
        status: flags.status,
        cacheHit: flags.cacheHit,
        requestId: requestId || undefined,
        details,
        sort,
        cursor,
        limit: PAGE_SIZE,
      },
      options,
    ),
    { refreshToken, reloadToken, maxAgeMs: 30_000 },
  );

  const commit = useCallback((next: URLSearchParams) => {
    setSelected(null);
    setSearchParams(next);
  }, [setSearchParams]);

  const setFilter = (filter: UsageFilterValue | null, dimension: UsageDimension) => {
    commit(writeUsageFilters(searchParams, filter ? withUsageFilter(filters, filter) : withoutUsageFilter(filters, dimension)));
  };

  const setFlags = (next: RequestFlags) => {
    const params = new URLSearchParams(searchParams);
    if (next.status) params.set('status', next.status);
    else params.delete('status');
    if (next.cacheHit === undefined) params.delete('cache');
    else params.set('cache', next.cacheHit ? 'hit' : 'miss');
    commit(params);
  };

  const setDetails = (next: RequestLogDetails) => {
    commit(writeRequestDetails(searchParams, next));
  };

  const setSort = (next: RequestLogSort) => {
    const params = new URLSearchParams(searchParams);
    if (next === 'newest') params.delete('sort');
    else params.set('sort', next);
    commit(params);
  };

  const changeColumns = (next: RequestLogColumn[]) => {
    setColumns(next);
    saveRequestLogColumns(next);
  };

  const applySearch = () => {
    const params = new URLSearchParams(searchParams);
    if (draft.trim()) params.set('request_id', draft.trim());
    else params.delete('request_id');
    commit(params);
  };

  const clearAll = () => {
    const params = writeUsageFilters(searchParams, []);
    params.delete('status');
    params.delete('cache');
    params.delete('request_id');
    for (const param of REQUEST_DETAIL_PARAMS) params.delete(param);
    setDraft('');
    commit(params);
  };

  const rows = logs.data?.logs ?? [];
  const page = cursors.length;
  const hasMore = Boolean(logs.data?.pagination.has_more && logs.data.pagination.next_cursor);
  const anyFilter = filters.length > 0
    || flags.status !== undefined
    || flags.cacheHit !== undefined
    || requestId !== ''
    || requestDetailsKey(details) !== '';

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <form
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            applySearch();
          }}
          className="relative"
        >
          <label htmlFor="request-id-search" className="sr-only">Find a request ID</label>
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
          <input
            id="request-id-search"
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => {
              if (draft.trim() !== requestId) applySearch();
            }}
            placeholder="Find a request ID"
            className="control-field-md w-[260px] rounded-lg border border-gray-300 bg-white pl-8 pr-2.5 text-xs text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
          />
        </form>
        <RequestFilterChips
          view={view}
          sinceDate={valueRange.sinceDate}
          untilDate={valueRange.untilDate}
          dimensions={chipDimensions(CHIP_DIMENSIONS, filters, allowed)}
          filters={filters}
          flags={flags}
          onFilter={setFilter}
          onFlags={setFlags}
        />
        <RequestDetailChips details={details} logs={rows} onChange={setDetails} />
        <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
          <span aria-hidden="true">Sort</span>
          <Segmented<RequestLogSort>
            label="Sort requests"
            value={sort}
            onChange={setSort}
            options={[
              { key: 'newest', label: 'Newest' },
              { key: 'cost', label: 'Cost', disabled: !valueRange.canSort },
              { key: 'duration', label: 'Duration', disabled: !valueRange.canSort },
            ]}
          />
        </div>
      </div>
      {logs.error && <PanelError message={logs.error} onRetry={logs.retry} stale={logs.data !== null} />}
      <section className="relative rounded-xl border border-gray-200 bg-white px-2 py-1" aria-label="Requests">
        <div className="absolute right-2 top-1.5 z-10 hidden min-[880px]:block">
          <ColumnsMenu options={REQUEST_LOG_COLUMNS} columns={columns} onColumns={changeColumns} />
        </div>
        {!logs.loading && rows.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-gray-500">
            No requests match these filters in this period.
            {anyFilter && (
              <button type="button" onClick={clearAll} className="control-sm ml-2 rounded-lg border border-gray-300 text-gray-900 hover:bg-gray-50">
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <RequestLogTable
            logs={rows}
            loading={logs.loading}
            refreshing={logs.refreshing}
            selectedId={selected?.id ?? null}
            columns={columns}
            onSelect={setSelected}
          />
        )}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-2.5 py-2">
          <span className="text-xs text-gray-500">
            {logs.loading ? 'Loading requests' : rows.length ? `Page ${page} · ${rows.length} requests, ${SORT_LABELS[sort]}` : ''}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPages({ key: queryKey, cursors: cursors.slice(0, -1) })}
              className="control-sm inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white text-gray-900 hover:bg-gray-50 disabled:opacity-40"
            >
              <ChevronLeft className="h-3.5 w-3.5" /> {sort === 'newest' ? 'Newer' : 'Previous'}
            </button>
            <button
              type="button"
              disabled={!hasMore}
              onClick={() => setPages({ key: queryKey, cursors: [...cursors, logs.data?.pagination.next_cursor ?? null] })}
              className="control-sm inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white text-gray-900 hover:bg-gray-50 disabled:opacity-40"
            >
              {sort === 'newest' ? 'Older' : 'Next'} <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </section>
      {selected && (
        <RequestLogDrawer
          log={selected}
          canOpenAudit={canOpenAudit}
          onClose={() => setSelected(null)}
          onFilterKey={(log) => {
            const params = writeUsageFilters(searchParams, [
              { dimension: 'api_key', value: log.api_key, label: log.key_name || null },
            ]);
            params.delete('status');
            params.delete('cache');
            params.delete('request_id');
            for (const param of REQUEST_DETAIL_PARAMS) params.delete(param);
            setDraft('');
            commit(params);
          }}
          onFilterDetail={(next) => setDetails({ ...details, ...next })}
        />
      )}
    </div>
  );
}
