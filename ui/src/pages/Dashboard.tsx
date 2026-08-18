import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import ReportingRangeControl from '../components/ReportingRangeControl';
import {
  beginDashboardReport,
  completeDashboardReport,
  dashboardReportError,
  dashboardReportPending,
  dashboardReportingRangesMatch,
  failDashboardReport,
  initialDashboardReportState,
} from '../lib/dashboardAnalytics';
import { useApi } from '../lib/hooks';
import {
  spend,
  models as modelsApi,
  keys as keysApi,
  health,
  type ProviderHealthStatus,
  type SpendGroupReport,
  type SpendSummary,
  type SpendTimeSeriesReport,
} from '../lib/api';
import {
  failureRate,
  formatBucketLabel,
  formatBucketTick,
  normalizeReportingSeries,
  parseReportingRangeKey,
  resolveReportingRange,
  type ReportingRangeKey,
  type ResolvedReportingRange,
} from '../lib/reportingRange';
import { fmtCompact, fmtSpendPrecise, fmtSpendValue } from '../lib/format';
import { useUtcReportingDay } from '../lib/useUtcReportingDay';
import { providerDisplayName } from '../lib/providers';
import {
  Activity,
  AlertCircle,
  Box,
  Clock,
  Database,
  DollarSign,
  Key,
  LoaderCircle,
  RefreshCw,
  Server,
  Zap,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const COLORS = ['#8b5cf6', '#3b82f6', '#06b6d4', '#10b981', '#f59e0b', '#94a3b8'];

type ProviderSpendDatum = { provider: string; label: string; spend: number };
type RequestChartDatum = { date: string; success: number; failed: number; total: number };

interface DashboardCoreAnalytics {
  range: ResolvedReportingRange;
  summary: SpendSummary;
  timeSeries: SpendTimeSeriesReport;
}

interface DashboardProviderAnalytics {
  range: ResolvedReportingRange;
  providerReport: SpendGroupReport;
}

interface ProviderAgg {
  provider: string;
  status: ProviderHealthStatus;
  models: number;
  healthy_models: number;
  unhealthy_models: number;
}

interface RequestTooltipEntry {
  payload?: RequestChartDatum;
}

interface RequestTooltipProps {
  active?: boolean;
  payload?: readonly RequestTooltipEntry[];
  label?: string | number;
  bucket: ResolvedReportingRange['bucket'];
}

const statusConfig: Record<ProviderHealthStatus, { dot: string; text: string; bg: string; label: string }> = {
  healthy: { dot: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50', label: 'Healthy' },
  degraded: { dot: 'bg-amber-500', text: 'text-amber-700', bg: 'bg-amber-50', label: 'Degraded' },
  down: { dot: 'bg-rose-500', text: 'text-rose-700', bg: 'bg-rose-50', label: 'Down' },
};

function RequestVolumeTooltip({ active, payload, label, bucket }: RequestTooltipProps) {
  const datum = payload?.[0]?.payload;
  if (!active || !datum || typeof label !== 'string') return null;

  return (
    <div className="min-w-44 rounded-lg border border-gray-200 bg-white p-3 shadow-lg">
      <p className="mb-2 text-xs font-semibold text-gray-700">{formatBucketLabel(label, bucket)}</p>
      <div className="space-y-1.5 text-xs">
        <div className="flex items-center justify-between gap-6 text-gray-600">
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-violet-500" />Successful</span>
          <span className="font-medium tabular-nums text-gray-900">{datum.success.toLocaleString()}</span>
        </div>
        <div className="flex items-center justify-between gap-6 text-gray-600">
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-rose-500" />Failed</span>
          <span className="font-medium tabular-nums text-gray-900">{datum.failed.toLocaleString()}</span>
        </div>
        <div className="flex items-center justify-between gap-6 border-t border-gray-100 pt-1.5 text-gray-600">
          <span>Total</span>
          <span className="font-semibold tabular-nums text-gray-900">{datum.total.toLocaleString()}</span>
        </div>
        <div className="flex items-center justify-between gap-6 text-gray-500">
          <span>Failure rate</span>
          <span className="tabular-nums">{failureRate(datum.failed, datum.total).toFixed(1)}%</span>
        </div>
      </div>
    </div>
  );
}

function MetricValue({ children, ready, loading }: { children: ReactNode; ready: boolean; loading: boolean }) {
  if (!ready && loading) return <span className="mt-2 block h-7 w-24 animate-pulse rounded bg-gray-100" />;
  if (!ready) return <>—</>;
  return <>{children}</>;
}

function requestErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export default function Dashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const utcReportingDay = useUtcReportingDay();
  const selectedRangeKey = parseReportingRangeKey(searchParams.get('range'));
  const rollingRangeDay = selectedRangeKey === 'all' ? null : utcReportingDay;
  const selectedRange = useMemo(
    () => resolveReportingRange(
      selectedRangeKey,
      rollingRangeDay ? new Date(`${rollingRangeDay}T00:00:00Z`) : new Date(),
    ),
    [rollingRangeDay, selectedRangeKey],
  );
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [providerRefreshNonce, setProviderRefreshNonce] = useState(0);
  const requestKey = `${selectedRangeKey}:${selectedRange.startDate ?? ''}:${selectedRange.endDate ?? ''}:${refreshNonce}`;
  const providerRequestKey = `${requestKey}:${providerRefreshNonce}`;
  const [coreState, setCoreState] = useState(
    () => initialDashboardReportState<DashboardCoreAnalytics>(),
  );
  const [providerState, setProviderState] = useState(
    () => initialDashboardReportState<DashboardProviderAnalytics>(),
  );

  const { data: providerHealthSummary } = useApi(() => modelsApi.providerHealthSummary(), []);
  const { data: keysResult } = useApi(() => keysApi.list(), []);
  const { data: healthData } = useApi(() => health.check(), []);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) {
        setCoreState((current) => beginDashboardReport(current, requestKey));
      }
    });

    void Promise.all([
      spend.summary(selectedRange.startDate, selectedRange.endDate, undefined, { signal: controller.signal }),
      spend.timeSeries(
        {
          start_date: selectedRange.startDate,
          end_date: selectedRange.endDate,
          interval: selectedRange.bucket,
        },
        { signal: controller.signal },
      ),
    ])
      .then(([summary, timeSeries]) => {
        if (controller.signal.aborted) return;
        setCoreState((current) => completeDashboardReport(
          current,
          requestKey,
          { range: selectedRange, summary, timeSeries },
        ));
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) return;
        setCoreState((current) => failDashboardReport(
          current,
          requestKey,
          requestErrorMessage(error, 'Unable to load dashboard analytics.'),
        ));
      });

    return () => controller.abort();
  }, [requestKey, selectedRange]);

  const coreForSelectedRequest = coreState.generation === requestKey && coreState.status === 'success'
    ? coreState.data
    : null;

  useEffect(() => {
    if (!coreForSelectedRequest) return;

    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) {
        setProviderState((current) => beginDashboardReport(current, providerRequestKey));
      }
    });
    void spend.providerReport(
      {
        start_date: coreForSelectedRequest.range.startDate,
        end_date: coreForSelectedRequest.range.endDate,
      },
      { signal: controller.signal },
    ).then((providerReport) => {
      if (controller.signal.aborted) return;
      setProviderState((current) => completeDashboardReport(
        current,
        providerRequestKey,
        { range: coreForSelectedRequest.range, providerReport },
      ));
    }).catch((error: unknown) => {
      if (controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) return;
      setProviderState((current) => failDashboardReport(
        current,
        providerRequestKey,
        requestErrorMessage(error, 'Unable to load provider spend.'),
      ));
    });

    return () => controller.abort();
  }, [coreForSelectedRequest, providerRequestKey]);

  const analytics = coreState.data;
  const analyticsLoading = dashboardReportPending(coreState, requestKey);
  const analyticsError = dashboardReportError(coreState, requestKey);
  const appliedRange = analytics?.range ?? selectedRange;
  const summary = analytics?.summary;
  const providerAnalytics = dashboardReportingRangesMatch(providerState.data?.range, appliedRange)
    ? providerState.data
    : null;
  const providerLoading = coreForSelectedRequest !== null
    && dashboardReportPending(providerState, providerRequestKey);
  const providerError = coreForSelectedRequest !== null
    ? dashboardReportError(providerState, providerRequestKey)
    : null;
  const providerWaitingForCore = analytics === null && analyticsLoading;
  const providerPanelLoading = providerWaitingForCore || providerLoading;
  const providerIsUpdating = providerAnalytics !== null && (analyticsLoading || providerLoading);
  const dashboardLoading = analyticsLoading || providerLoading;
  const requestSeries = useMemo(
    () => normalizeReportingSeries(analytics?.timeSeries.breakdown ?? [], appliedRange),
    [analytics?.timeSeries.breakdown, appliedRange],
  );
  const requestData: RequestChartDatum[] = useMemo(
    () => requestSeries.map((row) => ({
      date: row.group_key,
      success: row.successful_requests ?? Math.max(row.request_count - row.failed_requests, 0),
      failed: row.failed_requests,
      total: row.request_count,
    })),
    [requestSeries],
  );
  const providerSpend: ProviderSpendDatum[] = useMemo(
    () => {
      const providers = (providerAnalytics?.providerReport.data ?? [])
        .map((row) => {
          const provider = (row.group_key || 'unknown').trim().toLowerCase() || 'unknown';
          return { provider, label: providerDisplayName(provider), spend: Number(row.total_spend) };
        })
        .filter((provider) => provider.spend > 0);
      if (!providerAnalytics?.providerReport.pagination?.has_more || !analytics) return providers;

      const listedSpend = providers.reduce((total, provider) => total + provider.spend, 0);
      const otherSpend = Math.max(Number(analytics.summary.total_spend) - listedSpend, 0);
      return otherSpend > 0 ? [...providers, { provider: 'other', label: 'Other', spend: otherSpend }] : providers;
    },
    [analytics, providerAnalytics],
  );

  const providerList = (providerHealthSummary?.providers || []) as ProviderAgg[];
  const totalModels = providerHealthSummary?.total_models ?? 0;
  const totalProviders = providerHealthSummary?.summary.total_providers ?? providerList.length;
  const healthStatus = healthData?.readiness?.status || healthData?.liveliness || 'unknown';
  const isHealthy = healthStatus === 'ok' || healthStatus === 'healthy';
  const activeProviders =
    providerHealthSummary?.summary.active_providers ?? providerList.filter((provider) => provider.status !== 'down').length;
  const totalRequests = summary?.total_requests ?? 0;
  const failedRequests = summary?.failed_requests ?? 0;
  const successfulRequests = summary?.successful_requests ?? Math.max(totalRequests - failedRequests, 0);
  const summaryReady = summary !== undefined;
  const isUpdating = analyticsLoading && analytics !== null;

  function handleRangeChange(key: ReportingRangeKey) {
    const next = new URLSearchParams(searchParams);
    next.set('range', key);
    setSearchParams(next);
  }

  return (
    <div className="p-4 sm:p-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-gray-900">Dashboard</h1>
            <p className="mt-1 text-sm text-gray-500">Gateway overview · reporting dates use UTC</p>
          </div>

          <div className="flex items-center gap-2">
            {dashboardLoading && (
              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-500" role="status">
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                <span className="hidden sm:inline">Updating</span>
                <span className="sr-only sm:hidden">Updating dashboard</span>
              </span>
            )}
            <ReportingRangeControl
              value={selectedRangeKey}
              onPresetChange={handleRangeChange}
              ariaLabel="Dashboard period"
            />
          </div>
        </div>

        {analyticsError && (
          <div className="flex flex-col gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 sm:flex-row sm:items-center sm:justify-between" role="alert">
            <div className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                {analyticsError}
                {analytics && ` Showing the last loaded period (${analytics.range.label}).`}
              </span>
            </div>
            <button type="button" onClick={() => setRefreshNonce((value) => value + 1)} className="inline-flex items-center gap-1.5 self-start rounded font-semibold hover:text-rose-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500">
              <RefreshCw className="h-3.5 w-3.5" /> Retry
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-5" aria-busy={analyticsLoading}>
          <div className={`flex items-start gap-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-opacity ${isUpdating ? 'opacity-60' : ''}`}>
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-violet-50">
              <DollarSign className="h-5 w-5 text-brand-secondary-ink" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-500">Total Spend</p>
              <p className="mt-1 whitespace-nowrap text-xl font-bold tabular-nums text-gray-900 lg:text-lg 2xl:text-xl">
                <MetricValue ready={summaryReady} loading={analyticsLoading}>{fmtSpendValue(summary?.total_spend)}</MetricValue>
              </p>
              <p className="mt-1 text-xs text-gray-500">
                {summaryReady
                  ? `${fmtCompact(summary?.total_tokens)} tokens · ${appliedRange.label}`
                  : analyticsLoading ? 'Loading usage' : 'Usage unavailable'}
              </p>
            </div>
          </div>

          <div className={`flex items-start gap-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-opacity ${isUpdating ? 'opacity-60' : ''}`}>
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50">
              <Zap className="h-5 w-5 text-brand-primary-ink" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-500">Total Requests</p>
              <p className="mt-1 text-2xl font-bold tabular-nums text-gray-900">
                <MetricValue ready={summaryReady} loading={analyticsLoading}>{fmtCompact(totalRequests)}</MetricValue>
              </p>
              <p className={`mt-1 text-xs font-medium ${failedRequests > 0 ? 'text-rose-600' : 'text-gray-400'}`}>
                {summaryReady
                  ? `${fmtCompact(failedRequests)} failed · ${failureRate(failedRequests, totalRequests).toFixed(1)}%`
                  : analyticsLoading ? 'Loading requests' : 'Requests unavailable'}
              </p>
            </div>
          </div>

          <div className="flex items-start gap-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-50">
              <Key className="h-5 w-5 text-emerald-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Active Keys</p>
              <p className="mt-1 text-2xl font-bold tabular-nums text-gray-900">{fmtCompact(keysResult?.pagination?.total ?? keysResult?.data?.length)}</p>
              <p className="mt-1 text-xs text-gray-400">Current</p>
            </div>
          </div>

          <div className="flex items-start gap-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-50">
              <Box className="h-5 w-5 text-amber-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Models</p>
              <p className="mt-1 text-2xl font-bold tabular-nums text-gray-900">{fmtCompact(totalModels)}</p>
              <p className="mt-1 text-xs text-gray-400">Current</p>
            </div>
          </div>

          <div className="flex items-start gap-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-cyan-50">
              <Clock className="h-5 w-5 text-cyan-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Providers</p>
              <p className="mt-1 text-2xl font-bold tabular-nums text-gray-900">{totalProviders}</p>
              <p className={`mt-1 text-xs font-medium ${activeProviders === totalProviders ? 'text-emerald-600' : 'text-amber-600'}`}>{activeProviders} active · current</p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div
            className={`rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-opacity ${isUpdating ? 'opacity-60' : ''}`}
            aria-busy={analyticsLoading}
          >
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-gray-900">Request Volume</h2>
                <p className="mt-0.5 text-xs text-gray-400">{appliedRange.label} · {appliedRange.bucket === 'day' ? 'daily' : `${appliedRange.bucket}ly`} buckets</p>
              </div>
              {summaryReady && (
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                  <span className="inline-flex items-center gap-1.5 text-gray-600"><span className="h-2 w-2 rounded-sm bg-violet-500" />{fmtCompact(successfulRequests)} successful</span>
                  <span className="inline-flex items-center gap-1.5 text-gray-600"><span className="h-2 w-2 rounded-sm bg-rose-500" />{fmtCompact(failedRequests)} failed</span>
                </div>
              )}
            </div>
            <div className="h-64">
              {!analytics && analyticsLoading ? (
                <div className="h-full animate-pulse rounded-lg bg-gray-50" />
              ) : !analytics && analyticsError ? (
                <div className="flex h-full items-center justify-center text-sm text-gray-400">
                  <div className="text-center"><AlertCircle className="mx-auto mb-2 h-8 w-8 opacity-50" /><p>Request volume is temporarily unavailable</p></div>
                </div>
              ) : requestData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={requestData} margin={{ top: 8, right: 8, left: 4, bottom: 4 }} accessibilityLayer>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
                    <XAxis
                      dataKey="date"
                      axisLine={false}
                      tickLine={false}
                      tick={{ fontSize: 11, fill: '#6b7280' }}
                      tickFormatter={(value: string) => formatBucketTick(value, appliedRange.bucket, appliedRange.key === 'all')}
                      minTickGap={28}
                      interval="preserveStartEnd"
                      dy={8}
                    />
                    <YAxis
                      axisLine={false}
                      tickLine={false}
                      tick={{ fontSize: 11, fill: '#6b7280' }}
                      tickFormatter={(value: number) => fmtCompact(value)}
                      allowDecimals={false}
                      width={52}
                    />
                    <Tooltip content={<RequestVolumeTooltip bucket={appliedRange.bucket} />} cursor={{ fill: '#f3f4f6' }} />
                    <Bar dataKey="success" name="Successful" stackId="requests" fill="#8b5cf6" radius={[2, 2, 0, 0]} />
                    <Bar dataKey="failed" name="Failed" stackId="requests" fill="#f43f5e" radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full items-center justify-center text-sm text-gray-400">
                  <div className="text-center"><Activity className="mx-auto mb-2 h-8 w-8 opacity-50" /><p>No requests in this period</p></div>
                </div>
              )}
            </div>
          </div>

          <div
            className={`rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-opacity ${providerIsUpdating ? 'opacity-60' : ''}`}
            aria-busy={providerPanelLoading}
          >
            <div className="mb-4">
              <h2 className="text-base font-semibold text-gray-900">Cost by Provider</h2>
              <p className="mt-0.5 text-xs text-gray-400">{appliedRange.label}</p>
            </div>
            {providerError && (
              <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700" role="alert">
                <span>
                  {providerAnalytics
                    ? 'Could not refresh provider spend. Showing the last successful data.'
                    : providerError}
                </span>
                <button
                  type="button"
                  onClick={() => setProviderRefreshNonce((value) => value + 1)}
                  className="shrink-0 rounded font-semibold hover:text-rose-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
                >
                  Retry
                </button>
              </div>
            )}
            <div className="min-h-[256px]">
              {providerPanelLoading && !providerAnalytics ? (
                <div className="h-64 animate-pulse rounded-lg bg-gray-50" />
              ) : !providerAnalytics ? (
                <div className="flex h-64 w-full items-center justify-center text-sm text-gray-400">
                  <div className="text-center"><AlertCircle className="mx-auto mb-2 h-8 w-8 opacity-50" /><p>Provider spend is temporarily unavailable</p></div>
                </div>
              ) : providerSpend.length > 0 ? (
                <div className="flex flex-col items-center gap-4 sm:flex-row">
                  <div className="h-48 w-full sm:h-64 sm:w-1/2">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart accessibilityLayer>
                        <Pie data={providerSpend} dataKey="spend" nameKey="label" cx="50%" cy="50%" outerRadius="70%" innerRadius="45%" paddingAngle={2}>
                          {providerSpend.map((provider, index) => <Cell key={provider.provider} fill={COLORS[index % COLORS.length]} />)}
                        </Pie>
                        <Tooltip
                          formatter={(value: number | string | undefined) => [fmtSpendPrecise(Number(value ?? 0)), 'Spend']}
                          contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.05)', fontSize: '13px' }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="max-h-48 w-full overflow-y-auto sm:max-h-64 sm:w-1/2 sm:pl-2">
                    <div className="space-y-3">
                      {providerSpend.map((provider, index) => (
                        <div key={provider.provider} className="flex items-center justify-between">
                          <div className="flex min-w-0 items-center gap-2">
                            <div className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: COLORS[index % COLORS.length] }} />
                            <span className="truncate text-sm text-gray-600">{provider.label}</span>
                          </div>
                          <span className="ml-2 shrink-0 text-sm font-medium tabular-nums text-gray-900">{fmtSpendValue(provider.spend)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex h-64 w-full items-center justify-center text-sm text-gray-400">
                  <div className="text-center"><Box className="mx-auto mb-2 h-8 w-8 opacity-50" /><p>No provider spend in this period</p></div>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-6 rounded-xl border border-gray-200/80 bg-gray-100/80 p-3 text-sm">
          <div className="flex items-center gap-2">
            <div className="relative flex h-2.5 w-2.5">
              {isHealthy ? (
                <><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" /><span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" /></>
              ) : <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />}
            </div>
            <span className="font-medium text-gray-700">{isHealthy ? 'Healthy' : healthStatus}</span>
          </div>
          <div className="hidden h-4 w-px bg-gray-300 sm:block" />
          <div className="flex items-center gap-1.5 text-gray-600"><Server className="h-4 w-4 text-gray-400" /><span>{activeProviders} Provider{activeProviders !== 1 ? 's' : ''} Active</span></div>
          <div className="hidden h-4 w-px bg-gray-300 sm:block" />
          <div className="flex items-center gap-1.5 text-gray-600"><Database className="h-4 w-4 text-gray-400" /><span>{fmtCompact(totalModels)} Model{totalModels !== 1 ? 's' : ''} Deployed</span></div>
        </div>

        {providerList.length > 0 && (
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
            <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
              <h2 className="text-base font-semibold text-gray-900">Provider Health</h2>
              <span className="text-xs text-gray-400">{totalProviders} provider{totalProviders !== 1 ? 's' : ''} configured</span>
            </div>
            <div className="divide-y divide-gray-100">
              {providerList.map((provider) => {
                const config = statusConfig[provider.status];
                return (
                  <div key={provider.provider} className="flex items-center justify-between px-5 py-3.5">
                    <div className="flex min-w-0 items-center gap-3">
                      <div className={`h-2.5 w-2.5 shrink-0 rounded-full ${config.dot}`} />
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-gray-900">{providerDisplayName(provider.provider)}</p>
                        <p className="text-xs text-gray-400">{provider.models} model{provider.models !== 1 ? 's' : ''}</p>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-5">
                      <div className="text-right"><p className="text-sm tabular-nums text-gray-700">{provider.healthy_models}/{provider.models}</p><p className="text-[11px] text-gray-400">healthy</p></div>
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${config.bg} ${config.text}`}>{config.label}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
