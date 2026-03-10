import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useApi } from '../lib/hooks';
import { spend, type SpendGroupBy, type SpendGroupRow, type SpendLog } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatCard from '../components/StatCard';
import Modal from '../components/Modal';
import { DollarSign, Zap, Hash, Calendar } from 'lucide-react';
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
  const spendPageSize = 5;
  const logsPageSize = 25;

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

  const { data: summary } = useApi(() => spend.summary(startDate, endDate), [startDate, endDate]);
  const { data: dailyReport } = useApi(() => spend.report('day', startDate, endDate), [startDate, endDate]);
  const { data: spendGroupsData, loading: spendGroupsLoading } = useApi(
    () =>
      spend.groupedReport(spendBy, {
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        search: spendSearch || undefined,
        limit: spendPageSize,
        offset: spendOffset,
      }),
    [spendBy, startDate, endDate, spendSearch, spendOffset]
  );
  const { data: logsData, loading: logsLoading } = useApi(
    () => {
      const params: Record<string, string> = {
        limit: String(logsPageSize),
        offset: String(logsOffset),
      };
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      return spend.logs(params);
    },
    [startDate, endDate, logsOffset]
  );

  const daily = (dailyReport?.breakdown || []).map((r: any) => ({ date: r.group_key, total_spend: r.total_spend }));
  const spendGroups = spendGroupsData?.data || [];
  const spendGroupsPagination = spendGroupsData?.pagination;
  const logs = logsData?.logs || [];
  const logsPagination = logsData?.pagination;

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
    { key: 'spend', header: 'Cost', render: (r: SpendLog) => fmt(r.spend) },
    { key: 'prompt_tokens', header: 'Prompt', render: (r: SpendLog) => fmtNum(r.prompt_tokens) },
    { key: 'completion_tokens', header: 'Completion', render: (r: SpendLog) => fmtNum(r.completion_tokens) },
    { key: 'prompt_tokens_cached', header: 'Cached Prompt', render: (r: SpendLog) => fmtNum(r.prompt_tokens_cached) },
    { key: 'team_id', header: 'Team', render: (r: SpendLog) => <span className="text-xs">{r.team_id || '—'}</span> },
    { key: 'cache_hit', header: 'Cache', render: (r: SpendLog) => r.cache_hit ? <span className="text-green-600 text-xs">Hit</span> : <span className="text-gray-400 text-xs">Miss</span> },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Usage & Spend</h1>
          <p className="text-sm text-gray-500 mt-1">Monitor costs, tokens, and request analytics</p>
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="flex-1 sm:flex-none px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          <span className="text-gray-400 shrink-0">to</span>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="flex-1 sm:flex-none px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard title="Total Spend" value={fmt(summary?.total_spend)} icon={<DollarSign className="w-5 h-5" />} />
        <StatCard title="Total Tokens" value={fmtNum(summary?.total_tokens)} icon={<Hash className="w-5 h-5" />} />
        <StatCard title="Total Requests" value={fmtNum(summary?.total_requests)} icon={<Zap className="w-5 h-5" />} />
        <StatCard title="Unique Models" value={fmtNum(summary?.unique_models)} icon={<Calendar className="w-5 h-5" />} />
      </div>

      <div className="flex gap-2 mb-6">
        <button onClick={() => setTab('overview')} className={`px-4 py-2 text-sm rounded-lg transition-colors ${tab === 'overview' ? 'bg-blue-600 text-white' : 'bg-white border text-gray-700 hover:bg-gray-50'}`}>Overview</button>
        <button onClick={() => setTab('logs')} className={`px-4 py-2 text-sm rounded-lg transition-colors ${tab === 'logs' ? 'bg-blue-600 text-white' : 'bg-white border text-gray-700 hover:bg-gray-50'}`}>Request Logs</button>
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

      <Modal open={selectedLog !== null} onClose={() => setSelectedLog(null)} title="Spend Log Details" wide>
        {selectedLog && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <DetailItem label="Time" value={fmtDateTime(selectedLog.start_time)} />
              <DetailItem label="Model" value={selectedLog.model} />
              <DetailItem label="Type" value={selectedLog.call_type} />
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
