import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import type { SpendCapabilities, SpendView } from '../../lib/api';
import { usageReports, type RequestLog, type UsageDimension, type UsageFilterValue } from '../../lib/api/usageReports';
import {
  allowedUsageDimensions,
  chipDimensions,
  parseRequestWindow,
  parseUsageFilters,
  requestWindowSince,
  usageFiltersKey,
  withoutUsageFilter,
  withUsageFilter,
  writeUsageFilters,
} from '../../lib/usageFilters';
import { useReport } from '../../lib/useReport';
import { PanelError } from '../../components/usage/ReportBits';
import RequestFilterChips, { type RequestFlags } from '../../components/usage/RequestFilterChips';
import RequestLogDrawer from '../../components/usage/RequestLogDrawer';
import RequestLogTable from '../../components/usage/RequestLogTable';

const PAGE_SIZE = 25;
const CHIP_DIMENSIONS: readonly UsageDimension[] = ['model', 'provider', 'team', 'api_key'];

interface UsageRequestsProps {
  view: SpendView;
  capabilities: SpendCapabilities | undefined;
  refreshToken: number;
  reloadToken: number;
  canOpenAudit: boolean;
}

function readFlags(params: URLSearchParams): RequestFlags {
  const status = params.get('status');
  const cache = params.get('cache');
  return {
    status: status === 'error' || status === 'success' ? status : undefined,
    cacheHit: cache === 'hit' ? true : cache === 'miss' ? false : undefined,
  };
}

export default function UsageRequests({ view, capabilities, refreshToken, reloadToken, canOpenAudit }: UsageRequestsProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const allowed = useMemo(() => allowedUsageDimensions(view, capabilities), [capabilities, view]);
  const filters = useMemo(
    () => parseUsageFilters(searchParams).filter((filter) => allowed.includes(filter.dimension)),
    [allowed, searchParams],
  );
  const flags = readFlags(searchParams);
  const windowKey = parseRequestWindow(searchParams.get('window'));
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
  const queryKey = `${view}|${windowKey}|${usageFiltersKey(filters)}|${flags.status ?? ''}|${flags.cacheHit ?? ''}|${requestId}`;
  const sinceDate = useMemo(() => requestWindowSince(windowKey).slice(0, 10), [windowKey]);
  const [pages, setPages] = useState<{ key: string; cursors: Array<string | null> }>({ key: queryKey, cursors: [null] });
  const cursors = pages.key === queryKey ? pages.cursors : [null];
  const cursor = cursors[cursors.length - 1];
  const [selected, setSelected] = useState<RequestLog | null>(null);

  const logs = useReport(
    `requests:logs|${queryKey}|${cursor ?? ''}`,
    (options) => usageReports.requestLogs(
      {
        view,
        since: requestWindowSince(windowKey),
        filters,
        status: flags.status,
        cacheHit: flags.cacheHit,
        requestId: requestId || undefined,
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
    setDraft('');
    commit(params);
  };

  const rows = logs.data?.logs ?? [];
  const page = cursors.length;
  const hasMore = Boolean(logs.data?.pagination.has_more && logs.data.pagination.next_cursor);
  const anyFilter = filters.length > 0 || flags.status !== undefined || flags.cacheHit !== undefined || requestId !== '';

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
            className="w-[260px] rounded-lg border border-gray-300 bg-white py-1.5 pl-8 pr-2.5 text-xs text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
          />
        </form>
        <RequestFilterChips
          view={view}
          sinceDate={sinceDate}
          dimensions={chipDimensions(CHIP_DIMENSIONS, filters, allowed)}
          filters={filters}
          flags={flags}
          onFilter={setFilter}
          onFlags={setFlags}
        />
      </div>
      {logs.error && <PanelError message={logs.error} onRetry={logs.retry} stale={logs.data !== null} />}
      <section className="rounded-xl border border-gray-200 bg-white px-2 py-1" aria-label="Requests">
        {!logs.loading && rows.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-gray-500">
            No requests match these filters in this period.
            {anyFilter && (
              <button type="button" onClick={clearAll} className="ml-2 rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-900 hover:bg-gray-50">
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
            onSelect={setSelected}
          />
        )}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-2.5 py-2">
          <span className="text-xs text-gray-500">
            {logs.loading ? 'Loading requests' : rows.length ? `Page ${page} · ${rows.length} requests, newest first` : ''}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPages({ key: queryKey, cursors: cursors.slice(0, -1) })}
              className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs text-gray-900 hover:bg-gray-50 disabled:opacity-40"
            >
              <ChevronLeft className="h-3.5 w-3.5" /> Newer
            </button>
            <button
              type="button"
              disabled={!hasMore}
              onClick={() => setPages({ key: queryKey, cursors: [...cursors, logs.data?.pagination.next_cursor ?? null] })}
              className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs text-gray-900 hover:bg-gray-50 disabled:opacity-40"
            >
              Older <ChevronRight className="h-3.5 w-3.5" />
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
            setDraft('');
            commit(params);
          }}
        />
      )}
    </div>
  );
}
