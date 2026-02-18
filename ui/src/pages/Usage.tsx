import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { spend } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatCard from '../components/StatCard';
import { DollarSign, Zap, Hash, Calendar } from 'lucide-react';
import { XAxis, YAxis, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';

function fmt(n: number | null | undefined): string {
  if (n == null) return '$0.00';
  return `$${Number(n).toFixed(4)}`;
}

function fmtNum(n: number | null | undefined): string {
  if (n == null) return '0';
  return Number(n).toLocaleString();
}

export default function Usage() {
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [tab, setTab] = useState<'overview' | 'logs'>('overview');

  const { data: summary } = useApi(() => spend.summary(startDate, endDate), [startDate, endDate]);
  const { data: dailyReport } = useApi(() => spend.report('day', startDate, endDate), [startDate, endDate]);
  const { data: modelReport } = useApi(() => spend.report('model', startDate, endDate), [startDate, endDate]);
  const { data: userReport } = useApi(() => spend.report('user', startDate, endDate), [startDate, endDate]);
  const { data: teamReport } = useApi(() => spend.report('team', startDate, endDate), [startDate, endDate]);
  const { data: logsData, loading: logsLoading } = useApi(() => spend.logs({ limit: '50' }), []);

  const daily = (dailyReport?.breakdown || []).map((r: any) => ({ date: r.group_key, total_spend: r.total_spend }));
  const perModel = (modelReport?.breakdown || []).map((r: any) => ({ model: r.group_key, total_spend: r.total_spend, total_tokens: r.total_tokens, request_count: r.request_count }));
  const perKey = (userReport?.breakdown || []).map((r: any) => ({ api_key: r.group_key, total_spend: r.total_spend, request_count: r.request_count }));
  const perTeam = (teamReport?.breakdown || []).map((r: any) => ({ team_id: r.group_key, total_spend: r.total_spend, request_count: r.request_count }));
  const logs = logsData?.logs || [];

  const modelColumns = [
    { key: 'model', header: 'Model', render: (r: any) => <span className="font-medium">{r.model}</span> },
    { key: 'total_spend', header: 'Spend', render: (r: any) => fmt(r.total_spend) },
    { key: 'total_tokens', header: 'Tokens', render: (r: any) => fmtNum(r.total_tokens) },
    { key: 'request_count', header: 'Requests', render: (r: any) => fmtNum(r.request_count) },
  ];

  const keyColumns = [
    { key: 'api_key', header: 'API Key', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{(r.api_key || '').substring(0, 12)}...</code> },
    { key: 'key_name', header: 'Name', render: (r: any) => r.key_name || <span className="text-gray-400">—</span> },
    { key: 'total_spend', header: 'Spend', render: (r: any) => fmt(r.total_spend) },
    { key: 'request_count', header: 'Requests', render: (r: any) => fmtNum(r.request_count) },
  ];

  const teamColumns = [
    { key: 'team_id', header: 'Team', render: (r: any) => <span className="font-medium">{r.team_id}</span> },
    { key: 'total_spend', header: 'Spend', render: (r: any) => fmt(r.total_spend) },
    { key: 'request_count', header: 'Requests', render: (r: any) => fmtNum(r.request_count) },
  ];

  const logColumns = [
    { key: 'model', header: 'Model', render: (r: any) => <span className="font-medium text-xs">{r.model}</span> },
    { key: 'spend', header: 'Cost', render: (r: any) => fmt(r.spend) },
    { key: 'total_tokens', header: 'Tokens', render: (r: any) => fmtNum(r.total_tokens) },
    { key: 'cache_hit', header: 'Cache', render: (r: any) => r.cache_hit ? <span className="text-green-600 text-xs">Hit</span> : <span className="text-gray-400 text-xs">Miss</span> },
    { key: 'start_time', header: 'Time', render: (r: any) => <span className="text-xs text-gray-500">{r.start_time ? new Date(r.start_time).toLocaleString() : '—'}</span> },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Usage & Spend</h1>
          <p className="text-sm text-gray-500 mt-1">Monitor costs, tokens, and request analytics</p>
        </div>
        <div className="flex items-center gap-2">
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          <span className="text-gray-400">to</span>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
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

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <Card title="Spend by Model">
              <DataTable columns={modelColumns} data={perModel || []} emptyMessage="No model data" />
            </Card>
            <Card title="Spend by API Key">
              <DataTable columns={keyColumns} data={perKey || []} emptyMessage="No key data" />
            </Card>
          </div>

          <Card title="Spend by Team">
            <DataTable columns={teamColumns} data={perTeam || []} emptyMessage="No team data" />
          </Card>
        </>
      ) : (
        <Card title="Request Logs">
          <DataTable columns={logColumns} data={logs || []} loading={logsLoading} emptyMessage="No request logs yet" />
        </Card>
      )}
    </div>
  );
}
