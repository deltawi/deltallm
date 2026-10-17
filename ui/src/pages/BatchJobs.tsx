import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Info, Layers } from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { HeaderTabBar, IndexShell } from '../components/admin/shells';
import BatchFilterBar from '../components/batch-jobs/BatchFilterBar';
import BatchJobsMobileList from '../components/batch-jobs/BatchJobsMobileList';
import BatchJobsTable from '../components/batch-jobs/BatchJobsTable';
import RefreshControl from '../components/usage/RefreshControl';
import { PanelError, Segmented } from '../components/usage/ReportBits';
import { batches, type BatchJobListItem } from '../lib/api/batches';
import {
  BATCH_TABS,
  BATCH_WINDOWS,
  batchListFilters,
  hasBatchFilters,
  isActiveJob,
  parseBatchListState,
  writeBatchListState,
  type BatchListState,
  type BatchWindow,
} from '../lib/batchJobs';
import { errorMessage } from '../lib/errorMessage';
import { navSectionLabel } from '../lib/navigation';
import { useAutoRefresh, type AutoRefreshMs } from '../lib/useAutoRefresh';
import { useReport } from '../lib/useReport';

const PAGE_SIZE = 25;
/** Reload interval while a job on screen is still running. */
const LIVE_MS = 10_000;

function TabLabel({ label, count, tone }: { label: string; count: number | null; tone?: string }) {
  const toneClass = tone === 'run' && count ? 'bg-blue-50 text-blue-700' : tone === 'bad' && count ? 'bg-rose-50 text-rose-700' : 'bg-gray-100 text-gray-600';
  return (
    <>
      {label}
      {count !== null && <span className={`rounded-full px-1.5 py-px text-[11px] font-semibold tabular-nums ${toneClass}`}>{count.toLocaleString()}</span>}
    </>
  );
}

export default function BatchJobs() {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const state = useMemo(() => parseBatchListState(searchParams), [searchParams]);
  const [refreshToken, setRefreshToken] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [autoRefreshMs, setAutoRefreshMs] = useState<AutoRefreshMs>(0);
  const [cancelTarget, setCancelTarget] = useState<BatchJobListItem | null>(null);
  const [cancelling, setCancelling] = useState(false);

  // Keys name the window, not its start time, so a reload keeps the rows on screen.
  const filterKey = JSON.stringify(batchListFilters({ ...state, window: 'all' }));
  const listKey = `batch:list|${state.window}|${filterKey}|${state.page}`;
  const summaryKey = `batch:summary|${state.window}|${JSON.stringify(batchListFilters({ ...state, tab: 'all', window: 'all' }))}`;

  const list = useReport(
    listKey,
    ({ signal }) => batches.list({ ...batchListFilters(state), limit: PAGE_SIZE, offset: (state.page - 1) * PAGE_SIZE }, signal),
    { refreshToken, reloadToken, maxAgeMs: 10_000 },
  );
  const summary = useReport(
    summaryKey,
    ({ signal }) => {
      // Tab counts use every filter except the status tab itself.
      const filters = batchListFilters(state);
      delete filters.status;
      return batches.summary(filters, signal);
    },
    { refreshToken, reloadToken, maxAgeMs: 10_000 },
  );
  const feature = useReport('batch:feature-status', ({ signal }) => batches.featureStatus(signal), { maxAgeMs: 300_000 });

  const jobs = useMemo(() => list.data?.data ?? [], [list.data]);
  const anyActive = jobs.some((job) => isActiveJob(job.status));
  const live = anyActive && autoRefreshMs === 0;
  useAutoRefresh(live ? LIVE_MS : autoRefreshMs, () => setReloadToken((value) => value + 1));
  // Time labels move forward with each load, not with every render.
  const now = list.updatedAt ?? Date.now();

  const commit = useCallback((patch: Partial<BatchListState>, replace = false) => {
    const next = { ...parseBatchListState(searchParams), ...patch };
    if (!('page' in patch)) next.page = 1;
    setSearchParams(writeBatchListState(next), { replace });
  }, [searchParams, setSearchParams]);
  const onFilterChange = useCallback((patch: Partial<BatchListState>) => commit(patch, 'search' in patch), [commit]);

  const teamLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const job of jobs) {
      if (job.created_by_team_id && job.team_alias) labels[job.created_by_team_id] = job.team_alias;
    }
    return labels;
  }, [jobs]);

  const openJob = (batchId: string) => navigate(`/batches/${encodeURIComponent(batchId)}`);

  const confirmCancel = async () => {
    if (!cancelTarget) return;
    setCancelling(true);
    try {
      await batches.cancel(cancelTarget.batch_id);
      pushToast({ tone: 'success', title: 'Cancel requested', message: `Batch ${cancelTarget.batch_id} stops after the lines that are running now.` });
      setCancelTarget(null);
      setRefreshToken((value) => value + 1);
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Cancel failed', message: errorMessage(error, 'The batch could not be cancelled.') });
    } finally {
      setCancelling(false);
    }
  };

  const ownJobsOnly = list.data?.view === 'own';
  const pagination = list.data?.pagination;
  const total = pagination?.total ?? null;
  const firstRow = pagination && pagination.total > 0 ? pagination.offset + 1 : 0;
  const lastRow = pagination ? pagination.offset + jobs.length : 0;
  const anyFilter = hasBatchFilters(state) || state.tab !== 'all' || state.window !== 'all';
  const counts = summary.data;

  const tabs = BATCH_TABS.map((tab) => ({
    to: `/batches${(() => {
      const query = writeBatchListState({ ...state, tab: tab.key, page: 1 }).toString();
      return query ? `?${query}` : '';
    })()}`,
    label: <TabLabel label={tab.label} count={counts ? tab.count(counts) : null} tone={tab.tone} />,
    active: state.tab === tab.key,
  }));

  return (
    <IndexShell
      eyebrowPrefix={navSectionLabel('/batches')}
      title="Batch jobs"
      titleIcon={Layers}
      count={counts?.total ?? null}
      description={ownJobsOnly
        ? 'Batches you sent with your API keys, in your organizations and teams. Newest first.'
        : (<>Embedding and chat batches sent with <code className="rounded bg-gray-100 px-1 text-[12px]">/v1/batches</code>. Newest first.</>)}
      action={(
        <RefreshControl
          updatedAt={list.updatedAt}
          refreshing={list.refreshing}
          failed={list.error !== null}
          label={live ? (list.refreshing ? 'Updating' : 'Live · every 10 s') : undefined}
          onRefresh={() => setRefreshToken((value) => value + 1)}
          // While a job runs the page is live, so the interval menu has nothing to add.
          autoRefreshMs={live ? undefined : autoRefreshMs}
          onAutoRefreshChange={live ? undefined : setAutoRefreshMs}
        />
      )}
      tabs={(
        <HeaderTabBar
          label="Batch status"
          items={tabs}
          controls={(
            <Segmented<BatchWindow>
              label="Created in"
              value={state.window}
              options={BATCH_WINDOWS.map((item) => ({ key: item.key, label: item.label }))}
              onChange={(window) => commit({ window })}
            />
          )}
        />
      )}
    >
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3.5">
        {feature.data?.embeddings_batch_enabled === false && (
          <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
            <p className="min-w-0 break-words">
              <b className="font-medium">The batch API is off.</b> Set{' '}
              <code className="rounded bg-amber-100 px-1 text-xs">general_settings.embeddings_batch_enabled: true</code> to turn on{' '}
              <code className="rounded bg-amber-100 px-1 text-xs">/v1/files</code> and <code className="rounded bg-amber-100 px-1 text-xs">/v1/batches</code>. You can still see old batches here.
            </p>
          </div>
        )}
        <BatchFilterBar
          state={state}
          teamLabels={teamLabels}
          summary={total === null ? null : `${total.toLocaleString()} job${total === 1 ? '' : 's'}`}
          onChange={onFilterChange}
        />
        {list.error && <PanelError message={list.error} onRetry={list.retry} stale={list.data !== null} />}
        <section className="rounded-xl border border-gray-200 bg-white px-2 py-1" aria-label="Batch jobs">
          {!list.loading && jobs.length === 0 ? (
            <div className="px-4 py-10 text-center text-sm text-gray-500">
              {list.error
                ? 'Batch jobs could not be loaded.'
                : state.page > 1
                  ? 'There are no batch jobs on this page.'
                  : anyFilter
                  ? 'No batch jobs match these filters.'
                  : ownJobsOnly ? 'You have not sent a batch yet.' : 'No batch jobs yet.'}
              {state.page > 1 && (
                <button
                  type="button"
                  onClick={() => commit({ page: 1 })}
                  className="control-sm ml-2 rounded-lg border border-gray-300 text-gray-900 hover:bg-gray-50"
                >
                  Go to the first page
                </button>
              )}
              {anyFilter && state.page === 1 && (
                <button
                  type="button"
                  onClick={() => setSearchParams(new URLSearchParams())}
                  className="control-sm ml-2 rounded-lg border border-gray-300 text-gray-900 hover:bg-gray-50"
                >
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="hidden md:block">
                <BatchJobsTable jobs={jobs} loading={list.loading} refreshing={list.refreshing && !live} now={now} onOpen={openJob} onCancel={setCancelTarget} />
              </div>
              <div className="md:hidden">
                <BatchJobsMobileList jobs={jobs} loading={list.loading} now={now} onOpen={openJob} />
              </div>
            </>
          )}
          {pagination && jobs.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-2.5 py-2 text-[12.5px] text-gray-500">
              <span className="tabular-nums">{firstRow.toLocaleString()}–{lastRow.toLocaleString()} of {pagination.total.toLocaleString()}</span>
              <span className="flex gap-2">
                <button
                  type="button"
                  disabled={state.page <= 1}
                  onClick={() => commit({ page: state.page - 1 })}
                  className="control-sm rounded-lg border border-gray-200 bg-white text-gray-900 hover:bg-gray-50 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={!pagination.has_more}
                  onClick={() => commit({ page: state.page + 1 })}
                  className="control-sm rounded-lg border border-gray-200 bg-white text-gray-900 hover:bg-gray-50 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
                >
                  Next
                </button>
              </span>
            </div>
          )}
        </section>
      </div>
      <ConfirmDialog
        open={cancelTarget !== null}
        title="Cancel this batch?"
        description={`Batch ${cancelTarget?.batch_id ?? ''} stops after the lines that are running now. Lines that did not start are cancelled. You cannot undo this.`}
        confirmLabel="Cancel batch"
        cancelLabel="Keep running"
        destructive
        confirming={cancelling}
        onConfirm={() => void confirmCancel()}
        onClose={() => setCancelTarget(null)}
      />
    </IndexShell>
  );
}
