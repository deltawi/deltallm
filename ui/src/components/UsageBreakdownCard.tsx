import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, ChevronLeft, ChevronRight, LockKeyhole, RefreshCw, Search } from 'lucide-react';

import {
  spend,
  reportingRequestInit,
  type SpendGroupReport,
  type SpendGroupRow,
  type SpendUsageDimension,
  type SpendUsageMetric,
  type SpendView,
} from '../lib/api';
import { fmtCompact, fmtSpendValue } from '../lib/format';
import type { ResolvedReportingRange } from '../lib/reportingRange';
import {
  beginTrackedReportingAttempt,
  createReportingAttemptTracker,
  settleTrackedReportingAttempt,
  type ReportingAttemptToken,
  type ReportingPartAttempt,
  type ReportingPartOutcome,
} from '../lib/reportingRefresh';
import {
  relativeUsageBarWidth,
  resolveUsageDimension,
  usageDimensionLabel,
  usageGroupIdentity,
  usageGroupLabel,
  usageGroupSecondaryLabel,
  usageMetricLabel,
  usageMetricValue,
  usageModelLabel,
  verifiedReportingResponse,
} from '../lib/usageBreakdown';
import Card from './Card';

const DIMENSIONS: SpendUsageDimension[] = ['organization', 'team', 'user'];
const METRICS: SpendUsageMetric[] = ['spend', 'tokens'];
const OWNER_PAGE_SIZE = 8;
const MODEL_LIMIT = 8;

interface SelectedOwner {
  dimension: SpendUsageDimension;
  rangeKey: string;
  row: SpendGroupRow;
}

interface OwnerOffsetState {
  scopeKey: string;
  offset: number;
}

interface UsageBreakdownCardProps {
  dimensions?: SpendUsageDimension[];
  selfScoped?: boolean;
  view: SpendView;
  active: boolean;
  pageVisible: boolean;
  range: ResolvedReportingRange;
  rangeRequestKey: string;
  refreshNonce: number;
  reportingGeneration: string;
  reportingV2Enabled: boolean;
  forceRefresh: boolean;
  onReportingAttempt: (attempt: ReportingPartAttempt) => void;
  onReportingOutcome: (outcome: ReportingPartOutcome) => void;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function metricText(row: SpendGroupRow, metric: SpendUsageMetric): string {
  return metric === 'spend' ? fmtSpendValue(row.total_spend) : fmtCompact(row.total_tokens);
}

function LoadingRows({ count = 5 }: { count?: number }) {
  return (
    <div className="space-y-3" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="h-[58px] animate-pulse rounded-lg bg-gray-100" />
      ))}
    </div>
  );
}

function LocalError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-rose-200 bg-rose-50 px-5 py-8 text-center" role="alert">
      <AlertCircle className="h-5 w-5 text-rose-600" />
      <p className="mt-2 text-sm font-medium text-rose-800">Unable to load this breakdown</p>
      <p className="mt-1 max-w-md text-xs text-rose-600">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
      >
        <RefreshCw className="h-3.5 w-3.5" />
        Retry
      </button>
    </div>
  );
}

export default function UsageBreakdownCard({
  dimensions = DIMENSIONS,
  selfScoped = false,
  view,
  active,
  pageVisible,
  range,
  rangeRequestKey,
  refreshNonce,
  reportingGeneration,
  reportingV2Enabled,
  forceRefresh,
  onReportingAttempt,
  onReportingOutcome,
}: UsageBreakdownCardProps) {
  const availableDimensions = dimensions;
  const [dimension, setDimension] = useState<SpendUsageDimension>(availableDimensions[0] ?? 'organization');
  const activeDimension = resolveUsageDimension(dimension, availableDimensions);
  const availableDimensionsKey = availableDimensions.join(':');
  const [metric, setMetric] = useState<SpendUsageMetric>('spend');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [offsetState, setOffsetState] = useState<OwnerOffsetState>({ scopeKey: '', offset: 0 });
  const [selectedOwner, setSelectedOwner] = useState<SelectedOwner | null>(null);
  const [ownerReport, setOwnerReport] = useState<SpendGroupReport | null>(null);
  const [ownerReportKey, setOwnerReportKey] = useState('');
  const [ownerReportRefreshNonce, setOwnerReportRefreshNonce] = useState(-1);
  const [ownersLoading, setOwnersLoading] = useState(false);
  const [ownersError, setOwnersError] = useState<string | null>(null);
  const [ownerRetryNonce, setOwnerRetryNonce] = useState(0);
  const [modelReport, setModelReport] = useState<SpendGroupReport | null>(null);
  const [modelReportKey, setModelReportKey] = useState('');
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [ownerReadyAttempt, setOwnerReadyAttempt] = useState<ReportingAttemptToken | null>(null);
  const ownerRefreshNonceRef = useRef(refreshNonce);
  const modelRefreshNonceRef = useRef(refreshNonce);
  const attemptTrackerRef = useRef(createReportingAttemptTracker(reportingGeneration));

  const beginReportingRequest = useCallback((): ReportingAttemptToken => {
    const begun = beginTrackedReportingAttempt(attemptTrackerRef.current, reportingGeneration);
    attemptTrackerRef.current = begun.tracker;
    if (begun.notifyParent) {
      onReportingAttempt({
        generation: begun.token.generation,
        part: 'breakdown',
        attempt: begun.token.attempt,
      });
    }
    return begun.token;
  }, [onReportingAttempt, reportingGeneration]);

  const settleReportingRequest = useCallback((
    token: ReportingAttemptToken,
    status: 'success' | 'error',
  ) => {
    const settled = settleTrackedReportingAttempt(attemptTrackerRef.current, token);
    attemptTrackerRef.current = settled.tracker;
    if (!settled.accepted) return;
    onReportingOutcome({
      generation: token.generation,
      part: 'breakdown',
      attempt: token.attempt,
      status,
    });
  }, [onReportingOutcome]);

  const beginBreakdownRetry = useCallback((target: 'owner' | 'model') => {
    if (target === 'model') {
      setOwnerReadyAttempt(beginReportingRequest());
    } else {
      setOwnerRetryNonce((value) => value + 1);
    }
  }, [beginReportingRequest]);

  const activeSearch = dimension === activeDimension ? search : '';
  const ownerOffsetScopeKey = `${rangeRequestKey}:${activeDimension}:${metric}:${activeSearch}`;
  const offset = offsetState.scopeKey === ownerOffsetScopeKey ? offsetState.offset : 0;
  const ownerQueryKey = `${ownerOffsetScopeKey}:${offset}`;
  const visibleOwnerReport = ownerReportKey === ownerQueryKey ? ownerReport : null;
  const visibleOwners = useMemo(() => visibleOwnerReport?.data ?? [], [visibleOwnerReport]);
  const ownerPagination = visibleOwnerReport?.pagination;
  const userIdentityLabelsHidden = activeDimension === 'user'
    && visibleOwnerReport?.capabilities?.user_identity_labels === false;
  const activeSelectedOwner = selectedOwner?.dimension === activeDimension && selectedOwner.rangeKey === rangeRequestKey
    ? selectedOwner
    : null;
  const selectedOwnerIdentity = activeSelectedOwner ? usageGroupIdentity(activeSelectedOwner.row) : '';
  const modelQueryKey = selectedOwnerIdentity
    ? `${rangeRequestKey}:${activeDimension}:${metric}:${selectedOwnerIdentity}`
    : '';
  const visibleModelReport = modelQueryKey && modelReportKey === modelQueryKey ? modelReport : null;
  const modelRows = useMemo(() => visibleModelReport?.data ?? [], [visibleModelReport]);

  useEffect(() => {
    if (availableDimensions.length === 0 || dimension === activeDimension) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setDimension(activeDimension);
      setSearchInput('');
      setSearch('');
      setOffsetState({ scopeKey: '', offset: 0 });
      setSelectedOwner(null);
    });
    return () => { cancelled = true; };
  }, [activeDimension, availableDimensions.length, availableDimensionsKey, dimension]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setOffsetState({ scopeKey: '', offset: 0 });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    if (!active || !pageVisible || availableDimensions.length === 0) return;

    const controller = new AbortController();
    const requestAttempt = beginReportingRequest();
    const refreshTriggered = ownerRefreshNonceRef.current !== refreshNonce;
    ownerRefreshNonceRef.current = refreshNonce;
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setOwnersLoading(true);
      setOwnersError(null);
      setModelsLoading(false);
    });

    void spend.groupedReport(activeDimension, {
      start_date: range.startDate,
      end_date: range.endDate,
      search: activeSearch || undefined,
      sort_by: metric,
      limit: OWNER_PAGE_SIZE,
      offset,
      view,
    }, reportingRequestInit(controller.signal, forceRefresh && refreshTriggered)).then((response) => (
      reportingV2Enabled ? verifiedReportingResponse(response, view) : response
    )).then((nextReport) => {
      if (controller.signal.aborted) return;

      if (offset > 0 && nextReport.data.length === 0 && nextReport.pagination.total <= offset) {
        const lastOffset = Math.max(0, Math.floor((nextReport.pagination.total - 1) / OWNER_PAGE_SIZE) * OWNER_PAGE_SIZE);
        setOffsetState({ scopeKey: ownerOffsetScopeKey, offset: lastOffset });
        return;
      }

      setOwnerReport(nextReport);
      setOwnerReportKey(ownerQueryKey);
      setOwnerReportRefreshNonce(refreshNonce);
      setOwnerReadyAttempt(requestAttempt);
      setSelectedOwner((current) => {
        const currentIdentity = current?.dimension === activeDimension && current.rangeKey === rangeRequestKey
          ? usageGroupIdentity(current.row)
          : '';
        const nextRow = nextReport.data.find((row) => usageGroupIdentity(row) === currentIdentity)
          ?? nextReport.data[0];
        return nextRow ? { dimension: activeDimension, rangeKey: rangeRequestKey, row: nextRow } : null;
      });
      if (nextReport.data.length === 0) {
        settleReportingRequest(requestAttempt, 'success');
      }
    }).catch((error: unknown) => {
      if (controller.signal.aborted || isAbortError(error)) return;
      setOwnersError(error instanceof Error ? error.message : 'Owner usage request failed');
      settleReportingRequest(requestAttempt, 'error');
    }).finally(() => {
      if (!controller.signal.aborted) setOwnersLoading(false);
    });

    return () => controller.abort();
  }, [
    active,
    availableDimensions.length,
    pageVisible,
    ownerQueryKey,
    range.startDate,
    range.endDate,
    rangeRequestKey,
    activeDimension,
    metric,
    activeSearch,
    offset,
    ownerOffsetScopeKey,
    refreshNonce,
    forceRefresh,
    ownerRetryNonce,
    reportingV2Enabled,
    view,
    beginReportingRequest,
    settleReportingRequest,
  ]);

  useEffect(() => {
    if (
      !active
      || !pageVisible
      || !activeSelectedOwner
      || !modelQueryKey
      || ownerReportKey !== ownerQueryKey
      || ownerReportRefreshNonce !== refreshNonce
      || !ownerReadyAttempt
      || ownerReadyAttempt.generation !== reportingGeneration
    ) return;

    const controller = new AbortController();
    const requestAttempt = ownerReadyAttempt;
    const refreshTriggered = modelRefreshNonceRef.current !== refreshNonce;
    modelRefreshNonceRef.current = refreshNonce;
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setModelsLoading(true);
      setModelsError(null);
    });

    void spend.groupedReport('model', {
      start_date: range.startDate,
      end_date: range.endDate,
      sort_by: metric,
      scope_type: activeDimension,
      scope_id: activeSelectedOwner.row.is_unassigned
        ? undefined
        : activeSelectedOwner.row.group_key ?? undefined,
      scope_unassigned: activeSelectedOwner.row.is_unassigned,
      limit: MODEL_LIMIT,
      offset: 0,
      view,
    }, reportingRequestInit(controller.signal, forceRefresh && refreshTriggered)).then((response) => (
      reportingV2Enabled ? verifiedReportingResponse(response, view) : response
    )).then((nextReport) => {
      if (controller.signal.aborted) return;
      setModelReport(nextReport);
      setModelReportKey(modelQueryKey);
      settleReportingRequest(requestAttempt, 'success');
    }).catch((error: unknown) => {
      if (controller.signal.aborted || isAbortError(error)) return;
      setModelsError(error instanceof Error ? error.message : 'Model usage request failed');
      settleReportingRequest(requestAttempt, 'error');
    }).finally(() => {
      if (!controller.signal.aborted) {
        setModelsLoading(false);
      }
    });

    return () => controller.abort();
  }, [
    active,
    pageVisible,
    modelQueryKey,
    activeSelectedOwner,
    range.startDate,
    range.endDate,
    activeDimension,
    metric,
    ownerQueryKey,
    ownerReportKey,
    ownerReportRefreshNonce,
    ownerReadyAttempt,
    reportingGeneration,
    reportingV2Enabled,
    refreshNonce,
    forceRefresh,
    settleReportingRequest,
    view,
  ]);

  const ownerMaximum = useMemo(
    () => Math.max(...visibleOwners.map((row) => usageMetricValue(row, metric)), 0),
    [visibleOwners, metric],
  );
  const modelMaximum = useMemo(
    () => Math.max(...modelRows.map((row) => usageMetricValue(row, metric)), 0),
    [modelRows, metric],
  );

  if (availableDimensions.length === 0) {
    return (
      <Card>
        <div className="flex min-h-40 items-center justify-center rounded-xl border border-dashed border-gray-200 px-5 text-center text-sm text-gray-400">
          No usage breakdown dimensions are available for this view
        </div>
      </Card>
    );
  }

  const changeDimension = (nextDimension: SpendUsageDimension) => {
    setDimension(nextDimension);
    setSearchInput('');
    setSearch('');
    setOffsetState({ scopeKey: '', offset: 0 });
    setSelectedOwner(null);
  };

  const changeMetric = (nextMetric: SpendUsageMetric) => {
    setMetric(nextMetric);
    setOffsetState({ scopeKey: '', offset: 0 });
  };

  const selectOwner = (row: SpendGroupRow) => {
    if (usageGroupIdentity(row) === selectedOwnerIdentity) return;
    setOwnerReadyAttempt(beginReportingRequest());
    setSelectedOwner({ dimension: activeDimension, rangeKey: rangeRequestKey, row });
  };

  return (
    <Card>
      <div className="flex flex-col gap-4 border-b border-gray-100 pb-5 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">
            {selfScoped ? 'Your usage breakdown' : 'Usage breakdown'}
          </h3>
          <p className="mt-1 text-xs text-gray-500">
            {selfScoped
              ? `See where your usage occurred, then inspect the model mix · ${range.label}`
              : `Compare owners, then inspect their model mix · ${range.label}`}
          </p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <fieldset>
            <legend className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-500">Group by</legend>
            <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-1">
              {availableDimensions.map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={activeDimension === value}
                  onClick={() => changeDimension(value)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${
                    activeDimension === value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
                  }`}
                >
                  {usageDimensionLabel(value)}
                </button>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-500">Measure</legend>
            <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-1">
              {METRICS.map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={metric === value}
                  onClick={() => changeMetric(value)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${
                    metric === value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
                  }`}
                >
                  {usageMetricLabel(value)}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
      </div>

      <div className="grid gap-6 pt-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.35fr)] xl:divide-x xl:divide-gray-100">
        <section aria-labelledby="usage-owner-heading">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <h4 id="usage-owner-heading" className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                {usageDimensionLabel(activeDimension, true)}
              </h4>
              <p className="mt-1 text-xs text-gray-400">Ranked by {usageMetricLabel(metric)}</p>
            </div>
            <label className="relative block w-44 sm:w-52">
              <span className="sr-only">Search {usageDimensionLabel(activeDimension, true).toLowerCase()}</span>
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
              <input
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder={selfScoped
                  ? `Search ${usageDimensionLabel(activeDimension, true).toLowerCase()}`
                  : userIdentityLabelsHidden ? 'Search user IDs' : 'Search owners'}
                className="w-full rounded-lg border border-gray-300 py-2 pl-8 pr-3 text-xs focus:outline-none focus:ring-2 focus:ring-brand-primary"
              />
            </label>
          </div>

          {ownersError && visibleOwnerReport && (
            <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700" role="alert">
              <span>
                Could not refresh the {selfScoped ? 'usage breakdown' : 'owner ranking'}. Showing the last successful data.
              </span>
              <button type="button" onClick={() => beginBreakdownRetry('owner')} className="shrink-0 rounded font-semibold hover:text-rose-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500">Retry</button>
            </div>
          )}

          {userIdentityLabelsHidden && (
            <div className="mb-3 flex items-start gap-2 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-500">
              <LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>User names are hidden by your access policy. Usage remains available by user ID.</span>
            </div>
          )}

          {ownersError && !visibleOwnerReport ? (
            <LocalError message={ownersError} onRetry={() => beginBreakdownRetry('owner')} />
          ) : !visibleOwnerReport ? (
            <LoadingRows />
          ) : visibleOwners.length === 0 ? (
            <div className="flex min-h-40 items-center justify-center rounded-xl border border-dashed border-gray-200 px-5 text-center text-sm text-gray-400">
              {search
                ? `No matching ${usageDimensionLabel(activeDimension, true).toLowerCase()} in this period`
                : selfScoped ? 'No usage recorded in this period' : 'No owner usage recorded in this period'}
            </div>
          ) : (
            <div className={`space-y-1.5 transition-opacity ${ownersLoading ? 'opacity-60' : ''}`} aria-busy={ownersLoading}>
              {visibleOwners.map((row) => {
                const rowIdentity = usageGroupIdentity(row);
                const selected = selectedOwnerIdentity === rowIdentity;
                const barWidth = relativeUsageBarWidth(usageMetricValue(row, metric), ownerMaximum, 3);
                const secondary = usageGroupSecondaryLabel(row);
                return (
                  <button
                    key={rowIdentity}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => selectOwner(row)}
                    className={`relative w-full overflow-hidden rounded-lg border px-3 py-2.5 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${
                      selected
                        ? 'border-blue-200 bg-blue-50'
                        : 'border-transparent bg-gray-50 hover:border-gray-200 hover:bg-gray-100'
                    }`}
                  >
                    <span
                      className={`absolute inset-y-0 left-0 ${selected ? 'bg-blue-100/70' : 'bg-gray-200/50'}`}
                      style={{ width: `${barWidth}%` }}
                      aria-hidden="true"
                    />
                    <span className="relative flex items-center justify-between gap-3">
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-gray-900">{usageGroupLabel(activeDimension, row)}</span>
                        <span className="block truncate text-[11px] text-gray-500">
                          {secondary ?? `${fmtCompact(row.request_count)} requests`}
                        </span>
                      </span>
                      <span className="shrink-0 text-xs font-semibold tabular-nums text-gray-800">{metricText(row, metric)}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          {ownerPagination && ownerPagination.total > 0 && (
            <div className="mt-3 flex items-center justify-between gap-3 text-xs text-gray-500">
              <span>
                {ownerPagination.offset + 1}–{Math.min(ownerPagination.offset + ownerPagination.limit, ownerPagination.total)} of {ownerPagination.total}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setOffsetState({ scopeKey: ownerOffsetScopeKey, offset: Math.max(0, offset - OWNER_PAGE_SIZE) })}
                  disabled={offset === 0 || ownersLoading}
                  aria-label="Previous owners page"
                  className="rounded-md p-1.5 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setOffsetState({ scopeKey: ownerOffsetScopeKey, offset: offset + OWNER_PAGE_SIZE })}
                  disabled={!ownerPagination.has_more || ownersLoading}
                  aria-label="Next owners page"
                  className="rounded-md p-1.5 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          )}
        </section>

        <section className="xl:pl-6" aria-labelledby="usage-model-heading">
          <div className="mb-4">
            <h4 id="usage-model-heading" className="text-xs font-semibold uppercase tracking-wide text-gray-500">Model breakdown</h4>
            {activeSelectedOwner ? (
              <p className="mt-1 truncate text-sm font-medium text-gray-900">
                {usageGroupLabel(activeDimension, activeSelectedOwner.row)}
                <span className="font-normal text-gray-400"> · top {MODEL_LIMIT} by {usageMetricLabel(metric)}</span>
              </p>
            ) : (
              <p className="mt-1 text-sm text-gray-400">
                Select {selfScoped ? `a ${usageDimensionLabel(activeDimension).toLowerCase()}` : 'an owner'} to inspect model usage
              </p>
            )}
          </div>

          {modelsError && visibleModelReport && (
            <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700" role="alert">
              <span>Could not refresh the model mix. Showing the last successful data.</span>
              <button type="button" onClick={() => beginBreakdownRetry('model')} className="shrink-0 rounded font-semibold hover:text-rose-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500">Retry</button>
            </div>
          )}

          {modelsError && !visibleModelReport ? (
            <LocalError message={modelsError} onRetry={() => beginBreakdownRetry('model')} />
          ) : modelQueryKey && !visibleModelReport ? (
            <LoadingRows />
          ) : !activeSelectedOwner ? (
            <div className="flex min-h-64 items-center justify-center rounded-xl border border-dashed border-gray-200 px-5 text-center text-sm text-gray-400">
              Choose {selfScoped ? `a ${usageDimensionLabel(activeDimension).toLowerCase()}` : 'an owner'} from the list
            </div>
          ) : modelRows.length === 0 ? (
            <div className="flex min-h-64 items-center justify-center rounded-xl border border-dashed border-gray-200 px-5 text-center text-sm text-gray-400">
              No model usage recorded for this {selfScoped ? usageDimensionLabel(activeDimension).toLowerCase() : 'owner'}
            </div>
          ) : (
            <div className={`space-y-4 transition-opacity ${modelsLoading ? 'opacity-60' : ''}`} aria-busy={modelsLoading}>
              {modelRows.map((row) => {
                const barWidth = relativeUsageBarWidth(usageMetricValue(row, metric), modelMaximum);
                return (
                  <div key={usageGroupIdentity(row)}>
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-gray-800" title={usageModelLabel(row)}>{usageModelLabel(row)}</p>
                        <p className="mt-0.5 text-[11px] text-gray-500">
                          {metric === 'tokens'
                            ? `${fmtCompact(row.prompt_tokens)} input · ${fmtCompact(row.completion_tokens)} output`
                            : `${fmtCompact(row.total_tokens)} tokens · ${fmtCompact(row.request_count)} requests`}
                        </p>
                      </div>
                      <span className="shrink-0 text-xs font-semibold tabular-nums text-gray-900">{metricText(row, metric)}</span>
                    </div>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-gray-100">
                      <div className="h-full rounded-full bg-blue-500" style={{ width: `${barWidth}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </Card>
  );
}
