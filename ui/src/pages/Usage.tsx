import { useEffect, useEffectEvent, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  spend,
  type Pagination,
  type SpendGroupBy,
  type SpendGroupReport,
  type SpendGroupRow,
  type SpendLog,
  type SpendSummary,
} from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatCard from '../components/StatCard';
import Modal from '../components/Modal';
import { DollarSign, LoaderCircle, Zap, Hash, Calendar } from 'lucide-react';
import { XAxis, YAxis, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';

const SPEND_GROUP_OPTIONS: Array<{ value: SpendGroupBy; label: string }> = [
  { value: 'model', label: 'Model' },
  { value: 'organization', label: 'Organization' },
  { value: 'team', label: 'Team' },
  { value: 'api_key', label: 'API Key' },
];

const SPEND_GROUP_LABELS: Record<SpendGroupBy, string> = {
  model: 'Model',
  organization: 'Organization',
  team: 'Team',
  api_key: 'API Key',
};

const SPEND_SEARCH_PLACEHOLDERS: Record<SpendGroupBy, string> = {
  model: 'Filter models...',
  organization: 'Filter organizations...',
  team: 'Filter teams...',
  api_key: 'Filter API keys...',
};

const AUTO_REFRESH_OPTIONS = [
  { value: 0, label: 'Off' },
  { value: 5000, label: '5 seconds' },
  { value: 10000, label: '10 seconds' },
  { value: 30000, label: '30 seconds' },
] as const;

type AutoRefreshMs = (typeof AUTO_REFRESH_OPTIONS)[number]['value'];

interface SpendDayReportRow {
  group_key: string;
  total_spend: number;
}

interface SpendDayReport {
  breakdown: SpendDayReportRow[];
}

interface SpendLogsResponse {
  logs: SpendLog[];
  pagination: Pagination;
}

function fmt(n: number | null | undefined): string {
  if (n == null) return '$0.00';
  return `$${Number(n).toFixed(4)}`;
}

function fmtNum(n: number | null | undefined): string {
  if (n == null) return '0';
  return Number(n).toLocaleString();
}

function fmtDateTime(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : '—';
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

function renderSpendGroupValue(groupBy: SpendGroupBy, row: SpendGroupRow) {
  if (groupBy === 'model') {
    return <span className="font-medium">{row.group_key}</span>;
  }
  if (groupBy === 'api_key') {
    return (
      <div>
        <div className="font-medium">{row.display_name || 'Unnamed key'}</div>
        <code className="text-xs text-gray-500">
          {row.group_key.length > 18 ? `${row.group_key.slice(0, 18)}...` : row.group_key}
        </code>
      </div>
    );
  }
  return (
    <div>
      <div className="font-medium">{row.display_name || row.group_key}</div>
      {row.display_name && <div className="text-xs text-gray-500">{row.group_key}</div>}
    </div>
  );
}

export default function Usage() {
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [tab, setTab] = useState<'overview' | 'logs'>('overview');
  const [selectedLog, setSelectedLog] = useState<SpendLog | null>(null);
  const [spendBy, setSpendBy] = useState<SpendGroupBy>('model');
  const [spendSearchInput, setSpendSearchInput] = useState('');
  const [spendSearch, setSpendSearch] = useState('');
  const [spendOffset, setSpendOffset] = useState(0);
  const [logsOffset, setLogsOffset] = useState(0);
  const [summary, setSummary] = useState<SpendSummary | null>(null);
  const [dailyReport, setDailyReport] = useState<SpendDayReport | null>(null);
  const [spendGroupsData, setSpendGroupsData] = useState<SpendGroupReport | null>(null);
  const [logsData, setLogsData] = useState<SpendLogsResponse | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [backgroundRefreshing, setBackgroundRefreshing] = useState(false);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [autoRefreshMs, setAutoRefreshMs] = useState<AutoRefreshMs>(0);
  const [pageVisible, setPageVisible] = useState(() => (typeof document === 'undefined' ? true : document.visibilityState === 'visible'));
  const spendPageSize = 5;
  const logsPageSize = 25;
  const refreshInFlightRef = useRef(false);
  const pendingRefreshRef = useRef(false);
  const pendingRefreshBackgroundRef = useRef(false);
  const requestSequenceRef = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSpendSearch(spendSearchInput.trim());
      setSpendOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [spendSearchInput]);

  useEffect(() => {
    setSpendOffset(0);
  }, [spendBy, startDate, endDate]);

  useEffect(() => {
    setLogsOffset(0);
  }, [startDate, endDate]);

  const refreshUsageData = useEffectEvent(async (background: boolean) => {
    if (refreshInFlightRef.current) {
      pendingRefreshRef.current = true;
      pendingRefreshBackgroundRef.current = pendingRefreshBackgroundRef.current || background;
      return;
    }

    refreshInFlightRef.current = true;
    const requestId = ++requestSequenceRef.current;
    const hasData =
      summary !== null || dailyReport !== null || spendGroupsData !== null || logsData !== null;

    if (hasData) {
      setBackgroundRefreshing(true);
    } else {
      setInitialLoading(true);
    }
    setRefreshError(null);

    try {
      const logsParams: Record<string, string> = {
        limit: String(logsPageSize),
        offset: String(logsOffset),
      };
      if (startDate) logsParams.start_date = startDate;
      if (endDate) logsParams.end_date = endDate;

      const [nextSummary, nextDailyReport, nextSpendGroupsData, nextLogsData] = await Promise.all([
        spend.summary(startDate, endDate),
        spend.report('day', startDate, endDate) as Promise<SpendDayReport>,
        spend.groupedReport(spendBy, {
          start_date: startDate || undefined,
          end_date: endDate || undefined,
          search: spendSearch || undefined,
          limit: spendPageSize,
          offset: spendOffset,
        }),
        spend.logs(logsParams),
      ]);

      if (requestId !== requestSequenceRef.current) {
        return;
      }

      setSummary(nextSummary);
      setDailyReport(nextDailyReport);
      setSpendGroupsData(nextSpendGroupsData);
      setLogsData(nextLogsData);
      setLastRefreshedAt(Date.now());
    } catch (error) {
      if (requestId === requestSequenceRef.current) {
        setRefreshError(error instanceof Error ? error.message : 'Refresh failed');
      }
    } finally {
      if (requestId === requestSequenceRef.current) {
        setInitialLoading(false);
        setBackgroundRefreshing(false);
      }

      refreshInFlightRef.current = false;

      if (pendingRefreshRef.current) {
        const pendingBackground = pendingRefreshBackgroundRef.current;
        pendingRefreshRef.current = false;
        pendingRefreshBackgroundRef.current = false;
        void refreshUsageData(pendingBackground);
      }
    }
  });

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
    void refreshUsageData(false);
  }, [startDate, endDate, spendBy, spendSearch, spendOffset, logsOffset]);

  useEffect(() => {
    if (!pageVisible || autoRefreshMs === 0) {
      return;
    }
    void refreshUsageData(true);
  }, [pageVisible, autoRefreshMs]);

  useEffect(() => {
    if (!pageVisible || autoRefreshMs === 0) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshUsageData(true);
    }, autoRefreshMs);
    return () => {
      window.clearInterval(timer);
    };
  }, [pageVisible, autoRefreshMs]);

  const spendGroupsLoading = initialLoading && spendGroupsData === null;
  const logsLoading = initialLoading && logsData === null;
  const refreshControlDisabled = initialLoading || backgroundRefreshing;
  const daily = (dailyReport?.breakdown || []).map((row) => ({ date: row.group_key, total_spend: row.total_spend }));
  const spendGroups = spendGroupsData?.data || [];
  const spendGroupsPagination = spendGroupsData?.pagination;
  const logs = logsData?.logs || [];
  const logsPagination = logsData?.pagination;
  const refreshStatusLabel = backgroundRefreshing
    ? 'Refreshing now'
    : refreshError
      ? 'Refresh failed'
      : autoRefreshMs > 0
        ? pageVisible
          ? `Every ${autoRefreshMs / 1000}s`
          : 'Paused in background'
        : 'Manual refresh';
  const refreshStatusTone = backgroundRefreshing
    ? 'bg-blue-50 text-blue-700 ring-blue-200'
    : refreshError
      ? 'bg-rose-50 text-rose-700 ring-rose-200'
      : autoRefreshMs > 0
        ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
        : 'bg-gray-100 text-gray-600 ring-gray-200';

  const spendGroupColumns = [
    {
      key: 'group_key',
      header: SPEND_GROUP_LABELS[spendBy],
      render: (row: SpendGroupRow) => renderSpendGroupValue(spendBy, row),
    },
    { key: 'total_spend', header: 'Spend', render: (r: any) => fmt(r.total_spend) },
    { key: 'total_tokens', header: 'Tokens', render: (r: any) => fmtNum(r.total_tokens) },
    { key: 'request_count', header: 'Requests', render: (r: any) => fmtNum(r.request_count) },
  ];

  const logColumns = [
    { key: 'start_time', header: 'Time', render: (r: SpendLog) => <span className="text-xs text-gray-500 whitespace-nowrap">{fmtDateTime(r.start_time)}</span> },
    { key: 'model', header: 'Model', render: (r: SpendLog) => <span className="font-medium text-xs">{r.model}</span> },
    { key: 'call_type', header: 'Type', render: (r: SpendLog) => <span className="text-xs capitalize">{r.call_type}</span> },
    { key: 'status', header: 'Status', render: (r: SpendLog) => <StatusBadge status={logStatus(r)} /> },
    { key: 'spend', header: 'Cost', render: (r: SpendLog) => fmt(r.spend) },
    { key: 'prompt_tokens', header: 'Prompt', render: (r: SpendLog) => fmtNum(r.prompt_tokens) },
    { key: 'completion_tokens', header: 'Completion', render: (r: SpendLog) => fmtNum(r.completion_tokens) },
    { key: 'prompt_tokens_cached', header: 'Cached Prompt', render: (r: SpendLog) => fmtNum(r.prompt_tokens_cached) },
    { key: 'team_id', header: 'Team', render: (r: SpendLog) => <span className="text-xs">{r.team_id || '—'}</span> },
    { key: 'cache_hit', header: 'Cache', render: (r: SpendLog) => r.cache_hit ? <span className="text-green-600 text-xs">Hit</span> : <span className="text-gray-400 text-xs">Miss</span> },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Usage & Spend</h1>
        <p className="mt-1 text-sm text-gray-500">Monitor costs, tokens, and request analytics</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard title="Total Spend" value={fmt(summary?.total_spend)} icon={<DollarSign className="w-5 h-5" />} />
        <StatCard title="Total Tokens" value={fmtNum(summary?.total_tokens)} icon={<Hash className="w-5 h-5" />} />
        <StatCard title="Total Requests" value={fmtNum(summary?.total_requests)} icon={<Zap className="w-5 h-5" />} />
        <StatCard title="Unique Models" value={fmtNum(summary?.unique_models)} icon={<Calendar className="w-5 h-5" />} />
      </div>

      <div className="mb-6 flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="flex gap-2">
          <button onClick={() => setTab('overview')} className={`px-4 py-2 text-sm rounded-lg transition-colors ${tab === 'overview' ? 'bg-blue-600 text-white' : 'bg-white border text-gray-700 hover:bg-gray-50'}`}>Overview</button>
          <button onClick={() => setTab('logs')} className={`px-4 py-2 text-sm rounded-lg transition-colors ${tab === 'logs' ? 'bg-blue-600 text-white' : 'bg-white border text-gray-700 hover:bg-gray-50'}`}>Request Logs</button>
        </div>

        <div className="flex flex-col gap-2 xl:items-end">
          <div className="flex flex-wrap items-center gap-2 xl:justify-end">
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="Start date"
            />
            <span className="text-sm text-gray-400">to</span>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="End date"
            />
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${refreshStatusTone}`}>
              {backgroundRefreshing ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
              <span>{refreshStatusLabel}</span>
            </span>
            <select
              value={String(autoRefreshMs)}
              onChange={(e) => setAutoRefreshMs(Number(e.target.value) as AutoRefreshMs)}
              disabled={refreshControlDisabled}
              className="rounded-full border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400"
              aria-label="Auto refresh interval"
            >
              {AUTO_REFRESH_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500 xl:justify-end">
            {refreshError ? (
              <span className="text-rose-600">{refreshError}</span>
            ) : lastRefreshedAt !== null ? (
              <span>Last updated {new Date(lastRefreshedAt).toLocaleTimeString()}</span>
            ) : (
              <span>Waiting for first refresh</span>
            )}
          </div>
        </div>
      </div>

      {tab === 'overview' ? (
        <>
          <Card title="Daily Spend Trend" className="mb-6">
            {daily && daily.length > 0 ? (
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={daily}>
                  <defs>
                    <linearGradient id="spendGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.1} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => `$${v}`} />
                  <Tooltip formatter={(v: any) => [`$${Number(v).toFixed(4)}`, 'Spend']} />
                  <Area type="monotone" dataKey="total_spend" stroke="#3b82f6" fill="url(#spendGrad)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-[280px] text-gray-400 text-sm">No data available</div>
            )}
          </Card>

          <Card title="Spend by">
            <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="inline-flex w-full flex-wrap rounded-lg border border-gray-200 bg-gray-50 p-1 lg:w-auto">
                {SPEND_GROUP_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    onClick={() => setSpendBy(option.value)}
                    className={`rounded-md px-3 py-2 text-sm transition-colors ${
                      spendBy === option.value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <input
                value={spendSearchInput}
                onChange={(e) => setSpendSearchInput(e.target.value)}
                placeholder={SPEND_SEARCH_PLACEHOLDERS[spendBy]}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 lg:w-72"
              />
            </div>
            <DataTable
              columns={spendGroupColumns}
              data={spendGroups}
              loading={spendGroupsLoading}
              emptyMessage={`No ${SPEND_GROUP_LABELS[spendBy].toLowerCase()} spend data`}
              pagination={spendGroupsPagination}
              onPageChange={setSpendOffset}
            />
          </Card>
        </>
      ) : (
        <Card
          title="Request Logs"
          action={<span className="text-xs text-gray-500">Click a row for details</span>}
        >
          <DataTable
            columns={logColumns}
            data={logs || []}
            loading={logsLoading}
            emptyMessage="No request logs yet"
            onRowClick={setSelectedLog}
            pagination={logsPagination}
            onPageChange={setLogsOffset}
          />
        </Card>
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
              <DetailItem label="Cost" value={fmt(selectedLog.spend)} />
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
