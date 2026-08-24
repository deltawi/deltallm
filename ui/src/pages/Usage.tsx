import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  spend,
  reportingRequestInit,
  type SpendFeatureStatus,
  type SpendLog,
  type SpendLogsResponse,
  type SpendSummary,
  type SpendTimeSeriesReport,
  type SpendUsageDimension,
  type SpendView,
} from '../lib/api';
import { useApi } from '../lib/hooks';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import ReportingRangeControl from '../components/ReportingRangeControl';
import StatCard from '../components/StatCard';
import UsageBreakdownCard from '../components/UsageBreakdownCard';
import { fmtCompact, fmtSpendAxis, fmtSpendPrecise, fmtSpendValue } from '../lib/format';
import {
  bucketCadence,
  formatBucketLabel,
  formatBucketTick,
  normalizeReportingSeries,
  reportingAutoRefreshOptions,
  resolveCustomReportingRange,
  resolveReportingRange,
  resolveReportingRangeQuery,
  withReportingRangeQuery,
  type ReportingAutoRefreshMs,
  type ReportingRangeKey,
  type ResolvedReportingRange,
} from '../lib/reportingRange';
import { useUtcReportingDay } from '../lib/useUtcReportingDay';
import { resolveUsageView, supportsCursorSpendLogs, verifiedReportingResponse } from '../lib/usageBreakdown';
import {
  beginReportingRefresh,
  beginReportingPartAttempt,
  recordReportingPartOutcome,
  reportingBreakdownReady,
  reportingRefreshStatus,
  type ReportingPartAttempt,
  type ReportingPartOutcome,
} from '../lib/reportingRefresh';
import Modal from '../components/Modal';
import { AlertCircle, Calendar, ChevronLeft, ChevronRight, DollarSign, Hash, Info, LoaderCircle, RefreshCw, Zap } from 'lucide-react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

interface RangeCursorState {
  rangeKey: string;
  cursors: Array<string | null>;
  page: number;
}

interface RangeAutoRefreshState {
  rangeKey: string;
  value: ReportingAutoRefreshMs;
}

interface ReportingRefreshState {
  nonce: number;
  forcedRangeKey: string | null;
}

interface SpendTrendDatum {
  date: string;
  totalSpend: number;
}

interface SpendTrendTooltipEntry {
  payload?: SpendTrendDatum;
}

interface SpendTrendTooltipProps {
  active?: boolean;
  payload?: readonly SpendTrendTooltipEntry[];
  label?: string | number;
  bucket: ResolvedReportingRange['bucket'];
}

const LEGACY_USAGE_DIMENSIONS: SpendUsageDimension[] = ['organization', 'team', 'user'];

function fmtNum(n: number | null | undefined): string {
  if (n == null) return '0';
  return Number(n).toLocaleString();
}

function fmtDateTime(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : '—';
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function requestErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function reportingRangesMatch(
  first: ResolvedReportingRange | null,
  second: ResolvedReportingRange,
): boolean {
  return first !== null
    && first.key === second.key
    && first.startDate === second.startDate
    && first.endDate === second.endDate
    && first.bucket === second.bucket;
}

function reportingRangeRequestKey(range: ResolvedReportingRange, view: SpendView): string {
  return `${view}:${range.key}:${range.startDate ?? ''}:${range.endDate ?? ''}:${range.bucket}`;
}

function isSpendView(value: string | null): value is SpendView {
  return value === 'platform' || value === 'organization' || value === 'team' || value === 'self';
}

function spendViewLabel(view: SpendView): string {
  return {
    platform: 'Platform',
    organization: 'Organizations',
    team: 'Teams',
    self: 'My usage',
  }[view];
}

function prettyJson(value: Record<string, unknown> | null | undefined): string {
  if (!value || Object.keys(value).length === 0) return '—';
  return JSON.stringify(value, null, 2);
}

function logStatus(value: SpendLog): 'success' | 'error' {
  return value.status === 'error' ? 'error' : 'success';
}

function errorMessage(value: SpendLog): string {
  const metadataError = value.metadata?.['error'];
  const errorValue = metadataError && typeof metadataError === 'object' ? metadataError as Record<string, unknown> : null;
  const message = typeof errorValue?.['message'] === 'string' ? errorValue['message'] : null;
  if (message && message.trim()) {
    return message;
  }
  return '—';
}

function SpendTrendTooltip({ active, payload, label, bucket }: SpendTrendTooltipProps) {
  const datum = payload?.[0]?.payload;
  if (!active || !datum || typeof label !== 'string') return null;

  return (
    <div className="min-w-40 rounded-lg border border-gray-200 bg-white p-3 shadow-lg">
      <p className="text-xs font-semibold text-gray-700">{formatBucketLabel(label, bucket)}</p>
      <div className="mt-2 flex items-center justify-between gap-6 text-xs text-gray-600">
        <span>Spend</span>
        <span className="font-semibold tabular-nums text-gray-900">{fmtSpendPrecise(datum.totalSpend)}</span>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: 'success' | 'error' }) {
  const classes =
    status === 'error'
      ? 'bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200'
      : 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200';
  return <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${classes}`}>{status}</span>;
}

function DetailItem({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`mt-1 text-sm text-gray-900 ${mono ? 'font-mono break-all' : ''}`}>{value}</div>
    </div>
  );
}

export default function Usage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const utcReportingDay = useUtcReportingDay();
  const rangeParam = searchParams.get('range');
  const rangeStartParam = searchParams.get('start');
  const rangeEndParam = searchParams.get('end');
  const rollingRangeDay = rangeParam === 'custom' || rangeParam === 'all' ? null : utcReportingDay;
  const selectedRange = useMemo(
    () => resolveReportingRangeQuery(
      rangeParam,
      rangeStartParam,
      rangeEndParam,
      rollingRangeDay ? new Date(`${rollingRangeDay}T00:00:00Z`) : new Date(),
    ),
    [rangeParam, rangeStartParam, rangeEndParam, rollingRangeDay],
  );
  const requestedView = isSpendView(searchParams.get('view')) ? searchParams.get('view') as SpendView : null;
  const {
    data: spendFeatureStatus,
    error: spendFeatureStatusError,
  } = useApi<SpendFeatureStatus>((signal) => spend.featureStatus({ signal }), []);
  const featureCapabilities = spendFeatureStatus?.capabilities;
  const availableViews = featureCapabilities?.available_views ?? [];
  const activeView = resolveUsageView(featureCapabilities, requestedView);
  const legacyReportingApi = spendFeatureStatus !== null && featureCapabilities === undefined;
  const reportingV2Enabled = supportsCursorSpendLogs(spendFeatureStatus?.reporting_api_version);
  const supportsCursorLogs = reportingV2Enabled;
  const usageScopeReady = spendFeatureStatus !== null || spendFeatureStatusError !== null;
  const rangeRequestKey = reportingRangeRequestKey(selectedRange, activeView);
  const [tab, setTab] = useState<'overview' | 'logs'>('overview');
  const [selectedLog, setSelectedLog] = useState<SpendLog | null>(null);
  const [logsNavigation, setLogsNavigation] = useState<RangeCursorState>({
    rangeKey: rangeRequestKey,
    cursors: [null],
    page: 0,
  });
  const logsPage = logsNavigation.rangeKey === rangeRequestKey ? logsNavigation.page : 0;
  const logsCursor = logsNavigation.rangeKey === rangeRequestKey
    ? logsNavigation.cursors[logsPage] ?? null
    : null;
  const [summary, setSummary] = useState<SpendSummary | null>(null);
  const [timeSeries, setTimeSeries] = useState<SpendTimeSeriesReport | null>(null);
  const [loadedRange, setLoadedRange] = useState<ResolvedReportingRange | null>(null);
  const [trendLoadedRange, setTrendLoadedRange] = useState<ResolvedReportingRange | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendError, setTrendError] = useState<string | null>(null);
  const [logsData, setLogsData] = useState<SpendLogsResponse | null>(null);
  const [logsDataKey, setLogsDataKey] = useState('');
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [backgroundRefreshing, setBackgroundRefreshing] = useState(false);
  const [refreshState, setRefreshState] = useState<ReportingRefreshState>({
    nonce: 0,
    forcedRangeKey: null,
  });
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [autoRefreshState, setAutoRefreshState] = useState<RangeAutoRefreshState>({
    rangeKey: rangeRequestKey,
    value: 0,
  });
  const [pageVisible, setPageVisible] = useState(() => (typeof document === 'undefined' ? true : document.visibilityState === 'visible'));
  const logsPageSize = 25;
  const refreshNonce = refreshState.nonce;
  const reportingGeneration = `${rangeRequestKey}:${tab}:${refreshNonce}`;
  const forceReportingRefresh = refreshState.forcedRangeKey === rangeRequestKey;
  const hasLoadedUsageDataRef = useRef(false);
  const hasLoadedUsageData = loadedRange !== null;
  const logsRequestKey = `${rangeRequestKey}:${logsPage}:${logsCursor ?? ''}`;
  const autoRefreshOptions = useMemo(() => reportingAutoRefreshOptions(selectedRange), [selectedRange]);
  const autoRefreshMs = autoRefreshState.rangeKey === rangeRequestKey ? autoRefreshState.value : 0;
  const autoRefreshAllowed = autoRefreshOptions.some((option) => option.value === autoRefreshMs);
  const effectiveAutoRefreshMs = autoRefreshAllowed ? autoRefreshMs : 0;
  const [reportingOutcome, setReportingOutcome] = useState(() => (
    beginReportingRefresh(reportingGeneration, tab)
  ));
  const visibleReportingOutcome = reportingOutcome.generation === reportingGeneration
    ? reportingOutcome
    : beginReportingRefresh(reportingGeneration, tab);
  const currentReportingStatus = reportingRefreshStatus(visibleReportingOutcome);
  const refreshBusy = currentReportingStatus === 'pending';
  const refreshFailed = currentReportingStatus === 'error';
  const refreshBusyRef = useRef(true);
  const summaryRefreshNonceRef = useRef(refreshNonce);
  const trendRefreshNonceRef = useRef(refreshNonce);
  const loadedRangeRef = useRef<ResolvedReportingRange | null>(null);
  const trendLoadedRangeRef = useRef<ResolvedReportingRange | null>(null);
  const beginReportingAttempt = useCallback((attempt: ReportingPartAttempt) => {
    setReportingOutcome((current) => beginReportingPartAttempt(current, attempt));
  }, []);
  const markReportingOutcome = useCallback((outcome: ReportingPartOutcome) => {
    setReportingOutcome((current) => recordReportingPartOutcome(current, outcome));
  }, []);

  useEffect(() => {
    setReportingOutcome(beginReportingRefresh(reportingGeneration, tab));
  }, [reportingGeneration, tab]);

  useEffect(() => {
    if (currentReportingStatus === 'success') {
      setLastRefreshedAt(Date.now());
    }
  }, [currentReportingStatus, reportingGeneration]);

  useEffect(() => {
    const canonical = withReportingRangeQuery(searchParams, selectedRange);
    if (spendFeatureStatus) {
      if (featureCapabilities && availableViews.length > 1) canonical.set('view', activeView);
      else canonical.delete('view');
    }
    if (canonical.toString() !== searchParams.toString()) {
      setSearchParams(canonical, { replace: true });
    }
  }, [activeView, availableViews.length, featureCapabilities, searchParams, selectedRange, setSearchParams, spendFeatureStatus]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setSummary(null);
      setTimeSeries(null);
      setLoadedRange(null);
      setTrendLoadedRange(null);
      setLogsData(null);
      setLogsDataKey('');
      setLogsNavigation({ rangeKey: '', cursors: [null], page: 0 });
      loadedRangeRef.current = null;
      trendLoadedRangeRef.current = null;
      hasLoadedUsageDataRef.current = false;
      setInitialLoading(true);
      setRefreshError(null);
      setTrendError(null);
    });
    return () => { cancelled = true; };
  }, [activeView]);

  useEffect(() => {
    if (tab === 'logs' && activeView !== 'platform' && activeView !== 'organization') {
      queueMicrotask(() => setTab('overview'));
    }
  }, [activeView, tab]);

  useEffect(() => {
    if (!usageScopeReady || !pageVisible || tab !== 'overview') return;

    const controller = new AbortController();
    const requestGeneration = reportingGeneration;
    const summaryRefreshTriggered = summaryRefreshNonceRef.current !== refreshNonce;
    const trendRefreshTriggered = trendRefreshNonceRef.current !== refreshNonce;
    const isRangeTransition = !reportingRangesMatch(loadedRangeRef.current, selectedRange)
      || !reportingRangesMatch(trendLoadedRangeRef.current, selectedRange);
    summaryRefreshNonceRef.current = refreshNonce;
    trendRefreshNonceRef.current = refreshNonce;
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      if (hasLoadedUsageDataRef.current) setBackgroundRefreshing(true);
      else setInitialLoading(true);
      setTrendLoading(true);
      setRefreshError(null);
      setTrendError(null);
    });

    const summaryRequest = spend.summary(
      selectedRange.startDate,
      selectedRange.endDate,
      activeView,
      reportingRequestInit(
        controller.signal,
        forceReportingRefresh && summaryRefreshTriggered,
      ),
    ).then((response) => (
      reportingV2Enabled ? verifiedReportingResponse(response, activeView) : response
    ));
    const trendRequest = spend.timeSeries({
      start_date: selectedRange.startDate,
      end_date: selectedRange.endDate,
      interval: selectedRange.bucket,
      view: activeView,
    }, reportingRequestInit(
      controller.signal,
      forceReportingRefresh && trendRefreshTriggered,
    )).then((response) => (
      reportingV2Enabled ? verifiedReportingResponse(response, activeView) : response
    ));

    void Promise.allSettled([summaryRequest, trendRequest]).then(([summaryResult, trendResult]) => {
      if (controller.signal.aborted) return;

      const summarySucceeded = summaryResult.status === 'fulfilled';
      const trendSucceeded = trendResult.status === 'fulfilled';
      markReportingOutcome({
        generation: requestGeneration,
        part: 'summary',
        status: summarySucceeded ? 'success' : 'error',
      });
      markReportingOutcome({
        generation: requestGeneration,
        part: 'trend',
        status: trendSucceeded ? 'success' : 'error',
      });

      if (summarySucceeded && trendSucceeded) {
        setSummary(summaryResult.value);
        setTimeSeries(trendResult.value);
        setLoadedRange(selectedRange);
        setTrendLoadedRange(selectedRange);
        loadedRangeRef.current = selectedRange;
        trendLoadedRangeRef.current = selectedRange;
        hasLoadedUsageDataRef.current = true;
      } else if (!isRangeTransition) {
        if (summarySucceeded) {
          setSummary(summaryResult.value);
          setLoadedRange(selectedRange);
          loadedRangeRef.current = selectedRange;
          hasLoadedUsageDataRef.current = true;
        }
        if (trendSucceeded) {
          setTimeSeries(trendResult.value);
          setTrendLoadedRange(selectedRange);
          trendLoadedRangeRef.current = selectedRange;
        }
      } else {
        markReportingOutcome({
          generation: requestGeneration,
          part: 'breakdown',
          status: 'skipped',
        });
      }

      if (!summarySucceeded) {
        setRefreshError(requestErrorMessage(summaryResult.reason, 'Refresh failed'));
      }
      if (!trendSucceeded) {
        setTrendError(requestErrorMessage(trendResult.reason, 'Spend trend request failed'));
      }
      setInitialLoading(false);
      setBackgroundRefreshing(false);
      setTrendLoading(false);
    });

    return () => controller.abort();
  }, [
    forceReportingRefresh,
    activeView,
    markReportingOutcome,
    pageVisible,
    rangeRequestKey,
    refreshNonce,
    reportingGeneration,
    reportingV2Enabled,
    selectedRange,
    tab,
    usageScopeReady,
  ]);

  useEffect(() => {
    if (
      !usageScopeReady
      || !pageVisible
      || tab !== 'logs'
      || (activeView !== 'platform' && activeView !== 'organization')
    ) return;

    const controller = new AbortController();
    const requestGeneration = reportingGeneration;
    const refreshTriggered = summaryRefreshNonceRef.current !== refreshNonce;
    const isRangeTransition = !reportingRangesMatch(loadedRangeRef.current, selectedRange);
    summaryRefreshNonceRef.current = refreshNonce;
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      if (hasLoadedUsageDataRef.current) setBackgroundRefreshing(true);
      else setInitialLoading(true);
      if (isRangeTransition) {
        setLogsLoading(true);
        setLogsError(null);
      }
      setRefreshError(null);
    });

    void spend.summary(
      selectedRange.startDate,
      selectedRange.endDate,
      activeView,
      reportingRequestInit(controller.signal, forceReportingRefresh && refreshTriggered),
    ).then((response) => (
      reportingV2Enabled ? verifiedReportingResponse(response, activeView) : response
    )).then((nextSummary) => {
      if (controller.signal.aborted) return;
      setSummary(nextSummary);
      setLoadedRange(selectedRange);
      loadedRangeRef.current = selectedRange;
      hasLoadedUsageDataRef.current = true;
      markReportingOutcome({
        generation: requestGeneration,
        part: 'summary',
        status: 'success',
      });
    }).catch((error: unknown) => {
      if (controller.signal.aborted || isAbortError(error)) return;
      setRefreshError(requestErrorMessage(error, 'Refresh failed'));
      markReportingOutcome({
        generation: requestGeneration,
        part: 'summary',
        status: 'error',
      });
      if (isRangeTransition) {
        setLogsLoading(false);
        setLogsError('Request logs were not loaded because the usage summary failed.');
        markReportingOutcome({
          generation: requestGeneration,
          part: 'logs',
          status: 'skipped',
        });
      }
    }).finally(() => {
      if (controller.signal.aborted) return;
      setInitialLoading(false);
      setBackgroundRefreshing(false);
    });

    return () => controller.abort();
  }, [activeView, forceReportingRefresh, markReportingOutcome, pageVisible, rangeRequestKey, refreshNonce, reportingGeneration, reportingV2Enabled, selectedRange, tab, usageScopeReady]);

  useEffect(() => {
    if (
      !pageVisible
      || tab !== 'logs'
      || (activeView !== 'platform' && activeView !== 'organization')
    ) return;
    if (!reportingRangesMatch(loadedRange, selectedRange)) return;

    const controller = new AbortController();
    const requestGeneration = reportingGeneration;
    const logsParams: Record<string, string> = {
      limit: String(logsPageSize),
    };
    if (supportsCursorLogs) {
      logsParams.pagination_mode = 'cursor';
      if (logsCursor) logsParams.cursor = logsCursor;
    } else {
      logsParams.offset = String(logsPage * logsPageSize);
    }
    if (selectedRange.startDate) logsParams.start_date = selectedRange.startDate;
    if (selectedRange.endDate) logsParams.end_date = selectedRange.endDate;
    if (activeView !== 'platform') logsParams.view = activeView;
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setLogsLoading(true);
      setLogsError(null);
    });

    void spend.logs(logsParams, { signal: controller.signal }).then((response) => (
      reportingV2Enabled ? verifiedReportingResponse(response, activeView) : response
    )).then((nextLogs) => {
      if (controller.signal.aborted) return;
      setLogsData(nextLogs);
      setLogsDataKey(logsRequestKey);
      markReportingOutcome({
        generation: requestGeneration,
        part: 'logs',
        status: 'success',
      });
    }).catch((error: unknown) => {
      if (controller.signal.aborted || isAbortError(error)) return;
      setLogsError(requestErrorMessage(error, 'Request logs failed'));
      markReportingOutcome({
        generation: requestGeneration,
        part: 'logs',
        status: 'error',
      });
    }).finally(() => {
      if (!controller.signal.aborted) setLogsLoading(false);
    });

    return () => controller.abort();
  }, [activeView, pageVisible, tab, logsCursor, logsPage, logsRequestKey, loadedRange, markReportingOutcome, rangeRequestKey, refreshNonce, reportingGeneration, reportingV2Enabled, selectedRange, supportsCursorLogs]);

  const handlePresetRangeChange = (key: ReportingRangeKey) => {
    setSearchParams(withReportingRangeQuery(searchParams, resolveReportingRange(key)));
    setLogsNavigation({ rangeKey: '', cursors: [null], page: 0 });
  };

  const handleCustomRangeApply = (startDate: string, endDate: string) => {
    const nextRange = resolveCustomReportingRange(startDate, endDate);
    if (!nextRange) return;
    setSearchParams(withReportingRangeQuery(searchParams, nextRange));
    setLogsNavigation({ rangeKey: '', cursors: [null], page: 0 });
  };

  const handleViewChange = (view: SpendView) => {
    const next = new URLSearchParams(searchParams);
    next.set('view', view);
    setSearchParams(next);
  };

  const handlePreviousLogsPage = () => {
    setLogsNavigation((current) => {
      const currentPage = current.rangeKey === rangeRequestKey ? current.page : 0;
      return {
        rangeKey: rangeRequestKey,
        cursors: current.rangeKey === rangeRequestKey ? current.cursors : [null],
        page: Math.max(0, currentPage - 1),
      };
    });
  };

  const handleNextLogsPage = () => {
    const nextCursor = logsPagination?.next_cursor;
    if (supportsCursorLogs && !nextCursor) return;
    setLogsNavigation((current) => {
      const navigation = current.rangeKey === rangeRequestKey
        ? current
        : { rangeKey: rangeRequestKey, cursors: [null], page: 0 };
      const nextPage = navigation.page + 1;
      return {
        rangeKey: rangeRequestKey,
        cursors: supportsCursorLogs
          ? [...navigation.cursors.slice(0, nextPage), nextCursor ?? null]
          : navigation.cursors,
        page: nextPage,
      };
    });
  };

  const triggerRefresh = useCallback((forceRefresh: boolean) => {
    setRefreshState((current) => ({
      nonce: current.nonce + 1,
      forcedRangeKey: forceRefresh ? rangeRequestKey : null,
    }));
  }, [rangeRequestKey]);

  useEffect(() => {
    refreshBusyRef.current = refreshBusy;
  }, [refreshBusy]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      setPageVisible(document.visibilityState === 'visible');
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  useEffect(() => {
    if (!pageVisible || effectiveAutoRefreshMs === 0) {
      return;
    }
    const timer = window.setInterval(() => {
      if (refreshBusyRef.current) return;
      triggerRefresh(false);
    }, effectiveAutoRefreshMs);
    return () => {
      window.clearInterval(timer);
    };
  }, [pageVisible, effectiveAutoRefreshMs, triggerRefresh]);

  const refreshControlDisabled = refreshBusy;
  const refreshActive = refreshBusy;
  const trendRange = trendLoadedRange ?? selectedRange;
  const trendData: SpendTrendDatum[] = useMemo(
    () => normalizeReportingSeries(timeSeries?.breakdown ?? [], trendRange).map((row) => ({
      date: row.group_key,
      totalSpend: Number(row.total_spend),
    })),
    [timeSeries?.breakdown, trendRange],
  );
  const loadedRangeMatchesSelection = reportingRangesMatch(loadedRange, selectedRange);
  const rangeIsUpdating = backgroundRefreshing && loadedRange !== null && !loadedRangeMatchesSelection;
  const staleRangeAfterError = refreshError !== null && loadedRange !== null && !loadedRangeMatchesSelection;
  const usageDataUnavailable = refreshError !== null && !hasLoadedUsageData && !initialLoading;
  const trendRangeMatchesSelection = reportingRangesMatch(trendLoadedRange, selectedRange);
  const trendIsUpdating = trendLoading && trendLoadedRange !== null && !trendRangeMatchesSelection;
  const staleTrendAfterError = trendError !== null && trendLoadedRange !== null && !trendRangeMatchesSelection;
  const displayedBreakdownRange = loadedRange ?? selectedRange;
  const displayedBreakdownRangeKey = reportingRangeRequestKey(displayedBreakdownRange, activeView);
  const visibleLogsData = logsDataKey === logsRequestKey ? logsData : null;
  const logs = visibleLogsData?.logs ?? [];
  const logsPagination = visibleLogsData?.pagination;
  const logsTableLoading = logsLoading && visibleLogsData === null;
  const usageCapabilities = summary?.capabilities;
  const selfScoped = usageCapabilities?.self_scoped ?? activeView === 'self';
  const canViewLogs = usageCapabilities?.request_logs
    ?? (activeView === 'platform' || activeView === 'organization');
  const allowedDimensions = usageCapabilities?.allowed_dimensions
    ?? featureCapabilities?.allowed_dimensions
    ?? (legacyReportingApi ? LEGACY_USAGE_DIMENSIONS : []);
  const selectedAutoRefreshOption = autoRefreshOptions.find((option) => option.value === effectiveAutoRefreshMs);
  const refreshStatusLabel = refreshActive
    ? 'Refreshing now'
    : refreshFailed
      ? 'Completed with errors'
      : effectiveAutoRefreshMs > 0
        ? pageVisible
          ? selectedAutoRefreshOption?.statusLabel ?? 'Auto refresh on'
          : 'Paused in background'
        : selectedAutoRefreshOption?.statusLabel ?? 'Manual refresh';
  const refreshStatusTone = refreshActive
    ? 'bg-blue-50 text-blue-700 ring-blue-200'
    : refreshFailed
      ? 'bg-rose-50 text-rose-700 ring-rose-200'
      : effectiveAutoRefreshMs > 0
        ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
        : 'bg-gray-100 text-gray-600 ring-gray-200';

  const logColumns = [
    { key: 'start_time', header: 'Time', render: (r: SpendLog) => <span className="text-xs text-gray-500 whitespace-nowrap">{fmtDateTime(r.start_time)}</span> },
    { key: 'model', header: 'Model', render: (r: SpendLog) => <span className="font-medium text-xs">{r.model}</span> },
    { key: 'call_type', header: 'Type', render: (r: SpendLog) => <span className="text-xs capitalize">{r.call_type}</span> },
    { key: 'status', header: 'Status', render: (r: SpendLog) => <StatusBadge status={logStatus(r)} /> },
    { key: 'spend', header: 'Cost', render: (r: SpendLog) => fmtSpendValue(r.spend) },
    { key: 'prompt_tokens', header: 'Prompt', render: (r: SpendLog) => fmtCompact(r.prompt_tokens) },
    { key: 'completion_tokens', header: 'Completion', render: (r: SpendLog) => fmtCompact(r.completion_tokens) },
    { key: 'prompt_tokens_cached', header: 'Cached Prompt', render: (r: SpendLog) => fmtCompact(r.prompt_tokens_cached) },
    { key: 'team_id', header: 'Team', render: (r: SpendLog) => <span className="text-xs">{r.team_id || '—'}</span> },
    { key: 'cache_hit', header: 'Cache', render: (r: SpendLog) => r.cache_hit ? <span className="text-green-600 text-xs">Hit</span> : <span className="text-gray-400 text-xs">Miss</span> },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{selfScoped ? 'Your usage' : 'Usage & Spend'}</h1>
          <p className="mt-1 text-sm text-gray-500">
            {selfScoped
              ? 'Track costs, tokens, and requests from API keys owned by your account'
              : 'Monitor costs, tokens, and request analytics'}
            {' '}· reporting dates use UTC
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          {availableViews.length > 1 && (
            <label className="flex flex-col gap-1.5 text-xs font-medium text-gray-500">
              View
              <select
                value={activeView}
                onChange={(event) => handleViewChange(event.target.value as SpendView)}
                className="h-10 rounded-lg border border-gray-300 bg-white px-3 text-sm font-medium text-gray-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                aria-label="Usage reporting scope"
              >
                {availableViews.map((view) => (
                  <option key={view} value={view}>{spendViewLabel(view)}</option>
                ))}
              </select>
            </label>
          )}
          <ReportingRangeControl
            value={selectedRange.key}
            startDate={selectedRange.startDate}
            endDate={selectedRange.endDate}
            customLabel={selectedRange.key === 'custom' ? selectedRange.label : undefined}
            onPresetChange={handlePresetRangeChange}
            onCustomApply={handleCustomRangeApply}
            allowCustom
            ariaLabel="Usage reporting period"
            desktopBreakpoint="xl"
          />
        </div>
      </div>

      {refreshError && (
        <div className="mb-6 flex flex-col gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 sm:flex-row sm:items-center sm:justify-between" role="alert">
          <div className="flex min-w-0 items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="min-w-0">
              <p className="font-medium">
                {staleRangeAfterError && loadedRange
                  ? `Unable to load ${selectedRange.label}. Showing ${loadedRange.label}.`
                  : hasLoadedUsageData
                    ? `Unable to refresh ${selectedRange.label}. Showing the last successful data.`
                    : `Unable to load usage data for ${selectedRange.label}.`}
              </p>
              <p className="mt-1 break-words text-xs text-rose-600">{refreshError}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => triggerRefresh(false)}
            className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-lg px-3 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500 sm:self-center"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Retry
          </button>
        </div>
      )}

      {refreshFailed && !refreshError && (
        <div className="mb-6 flex flex-col gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 sm:flex-row sm:items-center sm:justify-between" role="alert">
          <div className="flex min-w-0 items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <p className="font-medium">Some usage panels could not be refreshed. Review the affected panel for details.</p>
          </div>
          <button
            type="button"
            onClick={() => triggerRefresh(false)}
            className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-lg px-3 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500 sm:self-center"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Retry all
          </button>
        </div>
      )}

      {!usageDataUnavailable && (
        <>
          <div
            className={`mb-6 grid grid-cols-2 gap-4 transition-opacity lg:grid-cols-4 ${rangeIsUpdating ? 'opacity-60' : ''}`}
            aria-busy={initialLoading || rangeIsUpdating}
          >
            <StatCard title="Total Spend" value={summary === null ? '—' : fmtSpendValue(summary.total_spend)} icon={<DollarSign className="w-5 h-5" />} />
            <StatCard title="Total Tokens" value={summary === null ? '—' : fmtCompact(summary.total_tokens)} icon={<Hash className="w-5 h-5" />} />
            <StatCard title="Total Requests" value={summary === null ? '—' : fmtCompact(summary.total_requests)} icon={<Zap className="w-5 h-5" />} />
            <StatCard title="Unique Models" value={summary === null ? '—' : fmtCompact(summary.unique_models)} icon={<Calendar className="w-5 h-5" />} />
          </div>

          <div className="mb-6 flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
            {canViewLogs ? (
              <div className="flex gap-2">
                <button type="button" aria-pressed={tab === 'overview'} onClick={() => setTab('overview')} className={`rounded-lg px-4 py-2 text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${tab === 'overview' ? 'bg-brand-primary text-brand-on-primary' : 'border bg-white text-gray-700 hover:bg-gray-50'}`}>Overview</button>
                <button type="button" aria-pressed={tab === 'logs'} onClick={() => setTab('logs')} className={`rounded-lg px-4 py-2 text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${tab === 'logs' ? 'bg-brand-primary text-brand-on-primary' : 'border bg-white text-gray-700 hover:bg-gray-50'}`}>Request Logs</button>
              </div>
            ) : <div />}

            <div className="flex flex-col gap-2 xl:items-end">
              <div className="flex flex-wrap items-center gap-2 xl:justify-end">
                <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${refreshStatusTone}`}>
                  {refreshActive ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
                  <span>{refreshStatusLabel}</span>
                </span>
                <select
                  value={String(effectiveAutoRefreshMs)}
                  onChange={(e) => setAutoRefreshState({
                    rangeKey: rangeRequestKey,
                    value: Number(e.target.value) as ReportingAutoRefreshMs,
                  })}
                  disabled={refreshControlDisabled}
                  className="rounded-full border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400"
                  aria-label="Auto refresh interval"
                >
                  {autoRefreshOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => triggerRefresh(true)}
                  disabled={refreshControlDisabled}
                  aria-label="Refresh usage now"
                  className="rounded-full border border-gray-300 bg-white p-2 text-gray-600 shadow-sm hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-300"
                >
                  <RefreshCw className={`h-4 w-4 ${refreshActive ? 'animate-spin' : ''}`} />
                </button>
              </div>

              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500 xl:justify-end">
                {lastRefreshedAt !== null ? (
                  <span>{refreshFailed ? 'Last successful refresh' : 'Last refreshed'} {new Date(lastRefreshedAt).toLocaleTimeString()}</span>
                ) : (
                  <span>Waiting for first refresh</span>
                )}
              </div>
            </div>
          </div>

          <div>
            {tab === 'overview' ? (
              <>
                <Card
                  title={selfScoped ? 'Your spend trend' : 'Spend Trend'}
                  className="mb-6"
                  action={(
                    <span className="ml-4 text-right text-xs text-gray-400">
                      {trendRange.label} · {bucketCadence(trendRange.bucket)} buckets
                    </span>
                  )}
                >
                  {trendError && (
                    <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700" role="alert">
                      <span>
                        {staleTrendAfterError && trendLoadedRange
                          ? `Unable to load ${selectedRange.label}. Showing ${trendLoadedRange.label}.`
                          : 'Unable to load the spend trend.'}
                        {' '}{trendError}
                      </span>
                      <button type="button" onClick={() => triggerRefresh(false)} className="shrink-0 rounded font-semibold hover:text-rose-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500">Retry</button>
                    </div>
                  )}
                  <div className={`transition-opacity ${trendIsUpdating ? 'opacity-60' : ''}`} aria-busy={trendLoading}>
                    {trendLoading && timeSeries === null ? (
                      <div className="h-[280px] animate-pulse rounded-lg bg-gray-50" />
                    ) : trendError && timeSeries === null ? (
                      <div className="flex h-[280px] items-center justify-center text-sm text-gray-400">Spend trend is temporarily unavailable</div>
                    ) : trendData.length > 0 ? (
                      <ResponsiveContainer width="100%" height={280}>
                        <AreaChart data={trendData} margin={{ top: 8, right: 8, left: 4, bottom: 4 }} accessibilityLayer>
                          <defs>
                            <linearGradient id="spendGrad" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.14} />
                              <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
                          <XAxis
                            dataKey="date"
                            axisLine={false}
                            tickLine={false}
                            tick={{ fontSize: 11, fill: '#6b7280' }}
                            tickFormatter={(value: string) => formatBucketTick(
                              value,
                              trendRange.bucket,
                              trendRange.key === 'all' || trendRange.startDate?.slice(0, 4) !== trendRange.endDate?.slice(0, 4),
                            )}
                            minTickGap={28}
                            interval="preserveStartEnd"
                            dy={8}
                          />
                          <YAxis
                            axisLine={false}
                            tickLine={false}
                            tick={{ fontSize: 11, fill: '#6b7280' }}
                            tickFormatter={(value: number) => fmtSpendAxis(value)}
                            domain={[0, 'auto']}
                            width={76}
                          />
                          <Tooltip content={<SpendTrendTooltip bucket={trendRange.bucket} />} cursor={{ stroke: '#d1d5db' }} />
                          <Area
                            type="linear"
                            dataKey="totalSpend"
                            name="Spend"
                            stroke="#3b82f6"
                            fill="url(#spendGrad)"
                            strokeWidth={2}
                            dot={trendData.length === 1 ? { r: 3 } : false}
                            activeDot={{ r: 4 }}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    ) : (
                      <div className="flex h-[280px] items-center justify-center text-sm text-gray-400">No spend recorded in this period</div>
                    )}
                  </div>
                </Card>

                <UsageBreakdownCard
                  key={activeView}
                  view={activeView}
                  dimensions={allowedDimensions}
                  selfScoped={selfScoped}
                  active={tab === 'overview'
                    && reportingBreakdownReady(
                      visibleReportingOutcome,
                      loadedRangeMatchesSelection && trendRangeMatchesSelection,
                    )}
                  pageVisible={pageVisible}
                  range={displayedBreakdownRange}
                  rangeRequestKey={displayedBreakdownRangeKey}
                  refreshNonce={refreshNonce}
                  reportingGeneration={reportingGeneration}
                  reportingV2Enabled={reportingV2Enabled}
                  forceRefresh={forceReportingRefresh}
                  onReportingAttempt={beginReportingAttempt}
                  onReportingOutcome={markReportingOutcome}
                />
              </>
            ) : (
              <Card
                title="Request Logs"
                action={<span className="text-xs text-gray-500">Click a row for details</span>}
              >
                {spendFeatureStatus?.cache_enabled === false && (
                  <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                    <div className="flex items-start gap-3">
                      <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                      <div>
                        <p className="font-medium">Cache is disabled.</p>
                        <p className="mt-1 text-amber-800">
                          New requests are expected to appear as <code className="rounded bg-amber-100 px-1 py-0.5 text-xs">Miss</code> in the{' '}
                          <code className="rounded bg-amber-100 px-1 py-0.5 text-xs">Cache</code> column while caching is off.
                        </p>
                      </div>
                    </div>
                  </div>
                )}
                {logsError && (
                  <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700" role="alert">
                    <span>Unable to load request logs. {logsError}</span>
                    <button type="button" onClick={() => triggerRefresh(false)} className="shrink-0 rounded font-semibold hover:text-rose-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500">Retry</button>
                  </div>
                )}
                <DataTable
                  columns={logColumns}
                  data={logs}
                  loading={logsTableLoading}
                  emptyMessage={logsError ? 'Request logs are temporarily unavailable' : 'No request logs yet'}
                  onRowClick={setSelectedLog}
                />
                {(logsPage > 0 || Boolean(logsPagination?.has_more)) && (
                  <div className="flex items-center justify-between border-t border-gray-100 px-4 py-3">
                    <span className="text-xs text-gray-500">
                      {logs.length > 0
                        ? `Showing ${logsPage * logsPageSize + 1}–${logsPage * logsPageSize + logs.length}`
                        : `Page ${logsPage + 1}`}
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={handlePreviousLogsPage}
                        disabled={logsPage === 0 || logsLoading}
                        aria-label="Previous request logs page"
                        className="rounded-lg p-1.5 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed disabled:opacity-30"
                      >
                        <ChevronLeft className="h-4 w-4" />
                      </button>
                      <span className="px-2 text-xs text-gray-600">Page {logsPage + 1}</span>
                      <button
                        type="button"
                        onClick={handleNextLogsPage}
                        disabled={!logsPagination?.has_more || (supportsCursorLogs && !logsPagination.next_cursor) || logsLoading}
                        aria-label="Next request logs page"
                        className="rounded-lg p-1.5 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed disabled:opacity-30"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                )}
              </Card>
            )}
          </div>
        </>
      )}

      <Modal open={selectedLog !== null} onClose={() => setSelectedLog(null)} title="Request Log Details" wide>
        {selectedLog && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <DetailItem label="Time" value={fmtDateTime(selectedLog.start_time)} />
              <DetailItem label="Model" value={selectedLog.model} />
              <DetailItem label="Type" value={selectedLog.call_type} />
              <DetailItem label="Status" value={<StatusBadge status={logStatus(selectedLog)} />} />
              <DetailItem label="HTTP Status" value={selectedLog.http_status_code ?? '—'} />
              <DetailItem label="Error Type" value={selectedLog.error_type || '—'} />
              <DetailItem label="Cost" value={fmtSpendValue(selectedLog.spend)} />
              <DetailItem label="Total Tokens" value={fmtNum(selectedLog.total_tokens)} />
              <DetailItem label="Cache" value={selectedLog.cache_hit ? 'Hit' : 'Miss'} />
              <DetailItem label="Prompt Tokens" value={fmtNum(selectedLog.prompt_tokens)} />
              <DetailItem label="Completion Tokens" value={fmtNum(selectedLog.completion_tokens)} />
              <DetailItem label="Cached Prompt Tokens" value={fmtNum(selectedLog.prompt_tokens_cached)} />
              <DetailItem label="Cached Completion Tokens" value={fmtNum(selectedLog.completion_tokens_cached)} />
              <DetailItem label="Team" value={selectedLog.team_id || '—'} mono />
              <DetailItem label="User" value={selectedLog.user || '—'} mono />
              <DetailItem label="End User" value={selectedLog.end_user || '—'} mono />
              <DetailItem label="API Base" value={selectedLog.api_base || '—'} mono />
              <DetailItem label="Request ID" value={selectedLog.request_id} mono />
              <DetailItem label="API Key" value={selectedLog.api_key} mono />
              <DetailItem label="Error Message" value={errorMessage(selectedLog)} />
              <DetailItem label="Cache Key" value={selectedLog.cache_key || '—'} mono />
              <DetailItem label="Tags" value={selectedLog.request_tags && selectedLog.request_tags.length > 0 ? selectedLog.request_tags.join(', ') : '—'} />
              <DetailItem label="End Time" value={fmtDateTime(selectedLog.end_time)} />
            </div>

            <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
              <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Metadata</div>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words text-xs text-gray-700">
                {prettyJson(selectedLog.metadata)}
              </pre>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
