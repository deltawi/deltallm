import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ChevronDown, Download, ScrollText } from 'lucide-react';
import { HeaderTabBar, IndexShell } from '../components/admin/shells';
import AuditEventPanel from '../components/audit/AuditEventPanel';
import AuditEventTable from '../components/audit/AuditEventTable';
import AuditFilterBar from '../components/audit/AuditFilterBar';
import ColumnsMenu from '../components/audit/ColumnsMenu';
import RefreshControl from '../components/usage/RefreshControl';
import { PanelError, Segmented } from '../components/usage/ReportBits';
import { audit, type AuditEvent } from '../lib/api/audit';
import { useAuth } from '../lib/auth';
import { resolveUiAccess } from '../lib/authorization';
import {
  auditQuery,
  hasAuditFilters,
  parseAuditFilters,
  parseAuditWindow,
  readAuditColumns,
  readAuditUtc,
  saveAuditColumns,
  saveAuditUtc,
  utcInputValue,
  windowLabel,
  writeAuditFilters,
  type AuditColumn,
  type AuditFilters,
  type AuditWindow,
} from '../lib/auditEvents';
import { errorMessage } from '../lib/errorMessage';
import { navSectionLabel } from '../lib/navigation';
import { useAutoRefresh, type AutoRefreshMs } from '../lib/useAutoRefresh';
import { useReport } from '../lib/useReport';
import { REQUEST_WINDOWS } from '../lib/usageFilters';

const PAGE_SIZE = 50;

const WINDOW_OPTIONS: ReadonlyArray<{ key: AuditWindow; label: string }> = [
  ...REQUEST_WINDOWS.map((item) => ({ key: item.key, label: item.label })),
  { key: 'custom', label: 'Custom' },
];

const DATE_INPUT =
  'rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary';

interface MorePages {
  key: string;
  events: AuditEvent[];
  nextCursor: string | null;
  hasMore: boolean;
  loading: boolean;
  error: string | null;
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

function ExportMenu({ onExport }: { onExport: (format: 'csv' | 'jsonl') => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    return () => document.removeEventListener('mousedown', onPointer);
  }, [open]);
  const run = (format: 'csv' | 'jsonl') => {
    setBusy(true);
    setError(null);
    onExport(format)
      .then(() => setOpen(false), (reason: unknown) => setError(errorMessage(reason, 'Export failed.')))
      .finally(() => setBusy(false));
  };
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        disabled={busy}
        onClick={() => setOpen((current) => !current)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 shadow-sm hover:bg-gray-50 hover:text-gray-900 disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      >
        <Download className="h-4 w-4 text-gray-500" aria-hidden="true" />
        {busy ? 'Exporting' : 'Export'}
        <ChevronDown className="h-3 w-3 text-gray-500" aria-hidden="true" />
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 grid w-56 rounded-lg border border-gray-200 bg-white p-1 shadow-lg">
          <button type="button" onClick={() => run('csv')} className="rounded-md px-2 py-1.5 text-left text-xs text-gray-800 hover:bg-gray-50">Export as CSV</button>
          <button type="button" onClick={() => run('jsonl')} className="rounded-md px-2 py-1.5 text-left text-xs text-gray-800 hover:bg-gray-50">Export as JSONL</button>
          <p className="px-2 py-1.5 text-[11.5px] text-gray-500">Uses the filters and time window on screen, up to 10,000 events.</p>
          {error && <p role="alert" className="px-2 pb-1.5 text-[11.5px] text-rose-700">{error}</p>}
        </div>
      )}
    </div>
  );
}

export default function AuditLogs() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { session, authMode } = useAuth();
  const access = resolveUiAccess(authMode, session);
  const filters = useMemo(() => parseAuditFilters(searchParams), [searchParams]);
  // Usage links here with a request ID from up to 30 days ago; without a chosen window, look that far back.
  const windowKey = parseAuditWindow(searchParams.get('window') ?? (filters.requestId ? '30d' : null));
  const customFrom = searchParams.get('from');
  const customTo = searchParams.get('to');
  const selectedId = searchParams.get('event');

  const [columns, setColumns] = useState<AuditColumn[]>(() => readAuditColumns());
  const [showUtc, setShowUtc] = useState(() => readAuditUtc());
  const [refreshToken, setRefreshToken] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [autoRefreshMs, setAutoRefreshMs] = useState<AutoRefreshMs>(0);

  // The key names the window, not its start time, so a reload keeps the rows on
  // screen. Each load computes the start time again.
  const queryKey = JSON.stringify([windowKey, customFrom, customTo, auditQuery(filters, '1h', {}, new Date(0))]);
  const first = useReport(
    `audit:events|${queryKey}`,
    (options) => audit.list(auditQuery(filters, windowKey, { from: customFrom, to: customTo }), { limit: PAGE_SIZE }, options),
    { refreshToken, reloadToken, maxAgeMs: 30_000 },
  );
  useAutoRefresh(autoRefreshMs, () => setReloadToken((value) => value + 1));

  // "Load 50 more" pages belong to one query and one load of the first page.
  const moreKey = `${queryKey}|${first.updatedAt ?? ''}`;
  const [more, setMore] = useState<MorePages | null>(null);
  const extra = more?.key === moreKey ? more : null;
  const events = useMemo(() => {
    const seen = new Set<string>();
    return [...(first.data?.events ?? []), ...(extra?.events ?? [])].filter((event) => {
      if (seen.has(event.event_id)) return false;
      seen.add(event.event_id);
      return true;
    });
  }, [extra?.events, first.data?.events]);
  const nextCursor = extra ? extra.nextCursor : first.data?.pagination.next_cursor ?? null;
  const hasMore = extra ? extra.hasMore : Boolean(first.data?.pagination.has_more);

  const loadMore = () => {
    if (!nextCursor) return;
    const key = moreKey;
    const loaded = extra?.events ?? [];
    setMore({ key, events: loaded, nextCursor, hasMore, loading: true, error: null });
    audit
      .list(auditQuery(filters, windowKey, { from: customFrom, to: customTo }), { cursor: nextCursor, limit: PAGE_SIZE })
      .then(
        (page) => setMore((current) => (current?.key === key
          ? { key, events: [...loaded, ...page.events], nextCursor: page.pagination.next_cursor, hasMore: page.pagination.has_more, loading: false, error: null }
          : current)),
        (reason: unknown) => setMore((current) => (current?.key === key
          ? { ...current, loading: false, error: errorMessage(reason, 'More events could not be loaded.') }
          : current)),
      );
  };

  const commitFilters = useCallback((next: AuditFilters, keepEvent = false) => {
    const params = writeAuditFilters(searchParams, next);
    if (keepEvent && selectedId) params.set('event', selectedId);
    setSearchParams(params);
  }, [searchParams, selectedId, setSearchParams]);

  const setWindow = (value: AuditWindow) => {
    const params = new URLSearchParams(searchParams);
    params.set('window', value);
    if (value === 'custom') {
      const now = new Date();
      if (!params.get('from')) params.set('from', utcInputValue(new Date(now.getTime() - 24 * 3_600_000)));
      if (!params.get('to')) params.set('to', utcInputValue(now));
    } else {
      params.delete('from');
      params.delete('to');
    }
    setSearchParams(params);
  };

  const setCustomBound = (key: 'from' | 'to', value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value) params.set(key, value);
    else params.delete(key);
    setSearchParams(params);
  };

  const selectEvent = useCallback((eventId: string | null) => {
    const params = new URLSearchParams(searchParams);
    if (eventId) params.set('event', eventId);
    else params.delete('event');
    // Stepping through events should not fill the browser history.
    setSearchParams(params, { replace: true });
  }, [searchParams, setSearchParams]);

  const selectedIndex = selectedId ? events.findIndex((event) => event.event_id === selectedId) : -1;
  const step = useCallback((direction: -1 | 1) => {
    if (selectedIndex < 0) return;
    const target = events[selectedIndex + direction];
    if (target) selectEvent(target.event_id);
  }, [events, selectEvent, selectedIndex]);

  useEffect(() => {
    if (!selectedId) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || isTyping(event.target)) return;
      if (event.key === 'Escape') selectEvent(null);
      else if (event.key === 'ArrowDown') {
        event.preventDefault();
        step(1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        step(-1);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selectEvent, selectedId, step]);

  const exportEvents = async (format: 'csv' | 'jsonl') => {
    const response = await fetch(audit.exportUrl(auditQuery(filters, windowKey, { from: customFrom, to: customTo }), format), {
      credentials: 'include',
    });
    if (!response.ok) throw new Error(`Export failed (${response.status})`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `audit-events.${format}`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const changeColumns = (next: AuditColumn[]) => {
    setColumns(next);
    saveAuditColumns(next);
  };

  const changeUtc = (next: boolean) => {
    setShowUtc(next);
    saveAuditUtc(next);
  };

  const anyFilter = hasAuditFilters(filters);

  return (
    <IndexShell
      eyebrowPrefix={navSectionLabel('/audit')}
      title="Audit logs"
      titleIcon={ScrollText}
      description="Who did what, and when · newest first · UTC"
      action={(
        <>
          <RefreshControl
            updatedAt={first.updatedAt}
            refreshing={first.refreshing}
            failed={first.error !== null}
            onRefresh={() => setRefreshToken((value) => value + 1)}
            autoRefreshMs={autoRefreshMs}
            onAutoRefreshChange={setAutoRefreshMs}
          />
          <ExportMenu onExport={exportEvents} />
        </>
      )}
      tabs={(
        <HeaderTabBar
          label="Audit log views"
          controls={(
            <>
              {windowKey === 'custom' && (
                <span className="flex flex-wrap items-center gap-1.5 text-xs text-gray-600">
                  <label className="sr-only" htmlFor="audit-from">From (UTC)</label>
                  <input id="audit-from" type="datetime-local" value={customFrom ?? ''} onChange={(event) => setCustomBound('from', event.target.value)} className={DATE_INPUT} />
                  to
                  <label className="sr-only" htmlFor="audit-to">To (UTC)</label>
                  <input id="audit-to" type="datetime-local" value={customTo ?? ''} onChange={(event) => setCustomBound('to', event.target.value)} className={DATE_INPUT} />
                  UTC
                </span>
              )}
              <Segmented<AuditWindow> label="Time window" value={windowKey} options={WINDOW_OPTIONS} onChange={setWindow} />
            </>
          )}
        />
      )}
    >
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3.5">
        <AuditFilterBar
          filters={filters}
          events={events}
          onChange={(next) => commitFilters(next)}
        />
        {first.error && <PanelError message={first.error} onRetry={first.retry} stale={first.data !== null} />}
        <section className="relative rounded-xl border border-gray-200 bg-white px-2 py-1" aria-label="Audit events">
          <div className="absolute right-2 top-1.5 z-10 hidden min-[880px]:block">
            <ColumnsMenu columns={columns} onColumns={changeColumns} showUtc={showUtc} onShowUtc={changeUtc} />
          </div>
          {!first.loading && events.length === 0 ? (
            <div className="px-4 py-10 text-center text-sm text-gray-500">
              {first.error ? 'Events could not be loaded.' : 'No events match these filters in this period.'}
              {anyFilter && (
                <button type="button" onClick={() => commitFilters({})} className="ml-2 rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-900 hover:bg-gray-50">
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <AuditEventTable
              events={events}
              loading={first.loading}
              refreshing={first.refreshing}
              selectedId={selectedId}
              columns={columns}
              showUtc={showUtc}
              onSelect={(event) => selectEvent(event.event_id)}
              onFilter={(patch) => commitFilters({ ...filters, ...patch })}
            />
          )}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-2.5 py-2 text-[12.5px] text-gray-500">
            <span>
              {first.loading ? 'Loading events' : `${events.length.toLocaleString()} event${events.length === 1 ? '' : 's'} loaded · ${windowLabel(windowKey)}`}
            </span>
            {extra?.error && <span role="alert" className="text-rose-700">{extra.error}</span>}
            {hasMore ? (
              <button
                type="button"
                onClick={loadMore}
                disabled={extra?.loading}
                className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-[13px] text-gray-900 hover:bg-gray-50 disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
              >
                {extra?.loading ? 'Loading' : `Load ${PAGE_SIZE} more`}
              </button>
            ) : (
              !first.loading && events.length > 0 && <span>That is every event in this period.</span>
            )}
          </div>
        </section>
      </div>
      {selectedId && (
        <AuditEventPanel
          eventId={selectedId}
          row={selectedIndex >= 0 ? events[selectedIndex] : null}
          access={access}
          hasPrevious={selectedIndex > 0}
          hasNext={selectedIndex >= 0 && selectedIndex < events.length - 1}
          onStep={step}
          onOpen={(eventId) => selectEvent(eventId)}
          onClose={() => selectEvent(null)}
          onFilter={(patch, replace) => commitFilters(replace ? patch : { ...filters, ...patch }, true)}
        />
      )}
    </IndexShell>
  );
}
