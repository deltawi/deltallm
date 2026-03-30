import React from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid
} from 'recharts';
import {
  DollarSign,
  Zap,
  Key,
  Box,
  CheckCircle2,
  Server,
  Database,
  Clock,
  TrendingUp,
  AlertCircle,
  AlertTriangle,
  Activity
} from 'lucide-react';

const summaryStats = {
  total_spend: 47.23,
  total_tokens: 1450200,
  total_requests: 12847,
  failed_requests: 23,
  active_keys: 18,
  models: 12,
  avg_latency: 420
};

const dailyRequests = [
  { date: 'Mar 17', success: 1200, failed: 5 },
  { date: 'Mar 18', success: 1800, failed: 12 },
  { date: 'Mar 19', success: 1400, failed: 2 },
  { date: 'Mar 20', success: 2200, failed: 28 },
  { date: 'Mar 21', success: 1900, failed: 4 },
  { date: 'Mar 22', success: 2500, failed: 10 },
  { date: 'Mar 23', success: 2100, failed: 6 }
];

const providers = [
  { name: 'OpenAI', status: 'healthy', latency: 340, successRate: 99.9, requests: 5842, models: 4 },
  { name: 'Anthropic', status: 'healthy', latency: 480, successRate: 99.7, requests: 3210, models: 3 },
  { name: 'Google Gemini', status: 'degraded', latency: 1250, successRate: 97.2, requests: 1890, models: 2 },
  { name: 'Groq', status: 'healthy', latency: 120, successRate: 100, requests: 1205, models: 2 },
  { name: 'Mistral', status: 'healthy', latency: 390, successRate: 99.8, requests: 700, models: 1 }
];

const activityFeed = [
  { id: 1, timestamp: 'Just now', model: 'gpt-4o', tokens: 452, latency: 340, status: 'success' as const },
  { id: 2, timestamp: '1 min ago', model: 'claude-3.5-sonnet', tokens: 1205, latency: 890, status: 'success' as const },
  { id: 3, timestamp: '3 mins ago', model: 'gemini-1.5-pro', tokens: 0, latency: 120, status: 'error' as const },
  { id: 4, timestamp: '5 mins ago', model: 'gemini-1.5-pro', tokens: 0, latency: 5200, status: 'warning' as const },
  { id: 5, timestamp: '8 mins ago', model: 'gpt-4o', tokens: 89, latency: 210, status: 'success' as const },
  { id: 6, timestamp: '12 mins ago', model: 'mixtral-8x7b', tokens: 2048, latency: 1250, status: 'success' as const },
  { id: 7, timestamp: '15 mins ago', model: 'llama-3-70b', tokens: 156, latency: 450, status: 'success' as const },
  { id: 8, timestamp: '22 mins ago', model: 'gpt-4o', tokens: 310, latency: 280, status: 'success' as const }
];

function fmtDollar(n: number) {
  return `$${n.toFixed(2)}`;
}

function fmtNum(n: number) {
  return n.toLocaleString();
}

const statusConfig = {
  healthy: { dot: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50', label: 'Healthy' },
  degraded: { dot: 'bg-amber-500', text: 'text-amber-700', bg: 'bg-amber-50', label: 'Degraded' },
  down: { dot: 'bg-rose-500', text: 'text-rose-700', bg: 'bg-rose-50', label: 'Down' }
};

const feedBorder = {
  success: 'border-l-emerald-400',
  error: 'border-l-rose-400',
  warning: 'border-l-amber-400'
};

const feedIcon = {
  success: <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />,
  error: <AlertCircle className="h-3.5 w-3.5 text-rose-500" />,
  warning: <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
};

const feedLabel = {
  success: { text: 'Success', cls: 'bg-emerald-50 text-emerald-700' },
  error: { text: 'Error', cls: 'bg-rose-50 text-rose-700' },
  warning: { text: 'Timeout', cls: 'bg-amber-50 text-amber-700' }
};

export function DashboardHealth() {
  return (
    <div className="min-h-screen bg-gray-50/50 p-6 font-sans">
      <div className="max-w-7xl mx-auto space-y-6">

        <div>
          <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">Gateway overview</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-violet-50">
              <DollarSign className="h-5 w-5 text-violet-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Total Spend</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{fmtDollar(summaryStats.total_spend)}</p>
              <p className="mt-1 text-xs text-gray-500">{fmtNum(summaryStats.total_tokens)} tokens used</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50">
              <Zap className="h-5 w-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Total Requests</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{fmtNum(summaryStats.total_requests)}</p>
              <p className="mt-1 text-xs text-red-500 font-medium">{summaryStats.failed_requests} failed</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-50">
              <Key className="h-5 w-5 text-emerald-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Active Keys</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{summaryStats.active_keys}</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-50">
              <Box className="h-5 w-5 text-amber-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Models</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{summaryStats.models}</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-cyan-50">
              <Clock className="h-5 w-5 text-cyan-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Avg Latency</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{summaryStats.avg_latency}<span className="text-sm font-medium text-gray-500 ml-1">ms</span></p>
              <p className="mt-1 text-xs text-emerald-600 font-medium">99.8% Success Rate</p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
            <h2 className="text-base font-semibold text-gray-900 mb-4">Request Volume</h2>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={dailyRequests} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorSuccessHealth" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
                  <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#6b7280' }} dy={10} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#6b7280' }} />
                  <Tooltip
                    contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.05)' }}
                    itemStyle={{ fontSize: '13px' }}
                    labelStyle={{ fontSize: '13px', fontWeight: 600, color: '#374151', marginBottom: '4px' }}
                  />
                  <Area type="monotone" dataKey="success" stackId="1" stroke="#8b5cf6" strokeWidth={2} fillOpacity={1} fill="url(#colorSuccessHealth)" name="Successful" />
                  <Area type="monotone" dataKey="failed" stackId="1" stroke="#f43f5e" strokeWidth={2} fill="#fecdd3" name="Failed" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-gray-900">Provider Health</h2>
              <span className="text-xs text-gray-400">Last checked 12s ago</span>
            </div>
            <div className="space-y-0 divide-y divide-gray-100">
              {providers.map((p) => {
                const cfg = statusConfig[p.status as keyof typeof statusConfig];
                return (
                  <div key={p.name} className="flex items-center justify-between py-3 first:pt-0 last:pb-0">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className={`h-2.5 w-2.5 rounded-full shrink-0 ${cfg.dot}`} />
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">{p.name}</p>
                        <p className="text-xs text-gray-400">{p.models} model{p.models > 1 ? 's' : ''}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-5 shrink-0">
                      <div className="text-right">
                        <p className="text-sm tabular-nums text-gray-700">{p.latency}ms</p>
                        <p className="text-[11px] text-gray-400">latency</p>
                      </div>
                      <div className="text-right">
                        <p className={`text-sm tabular-nums ${p.successRate < 99 ? 'text-amber-600 font-medium' : 'text-gray-700'}`}>{p.successRate}%</p>
                        <p className="text-[11px] text-gray-400">success</p>
                      </div>
                      <div className="text-right hidden sm:block">
                        <p className="text-sm tabular-nums text-gray-700">{fmtNum(p.requests)}</p>
                        <p className="text-[11px] text-gray-400">requests</p>
                      </div>
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${cfg.bg} ${cfg.text}`}>
                        {cfg.label}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="bg-gray-100/80 border border-gray-200/80 rounded-xl p-3 flex flex-wrap items-center gap-6 text-sm">
          <div className="flex items-center gap-2">
            <div className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
            </div>
            <span className="font-medium text-gray-700">Healthy</span>
          </div>

          <div className="h-4 w-px bg-gray-300 hidden sm:block"></div>

          <div className="flex items-center gap-1.5 text-gray-600">
            <Server className="h-4 w-4 text-gray-400" />
            <span>5 Providers Active</span>
          </div>

          <div className="h-4 w-px bg-gray-300 hidden sm:block"></div>

          <div className="flex items-center gap-1.5 text-gray-600">
            <Database className="h-4 w-4 text-gray-400" />
            <span>87% Cache Hit</span>
          </div>

          <div className="h-4 w-px bg-gray-300 hidden sm:block"></div>

          <div className="flex items-center gap-1.5 text-gray-600">
            <TrendingUp className="h-4 w-4 text-gray-400" />
            <span>99.9% Uptime</span>
          </div>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-gray-400" />
              <h2 className="text-base font-semibold text-gray-900">Recent Activity</h2>
            </div>
            <button className="text-sm text-violet-600 hover:text-violet-700 font-medium">View all</button>
          </div>
          <div className="divide-y divide-gray-50">
            {activityFeed.map((item) => (
              <div
                key={item.id}
                className={`flex items-center gap-4 px-5 py-3 border-l-[3px] ${feedBorder[item.status]} hover:bg-gray-50/50 transition-colors`}
              >
                <div className="shrink-0">{feedIcon[item.status]}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center py-0.5 px-1.5 rounded bg-gray-100 text-gray-700 text-xs font-medium">{item.model}</span>
                    {item.tokens > 0 && <span className="text-xs text-gray-500">{fmtNum(item.tokens)} tokens</span>}
                    <span className="text-xs text-gray-400">{item.latency}ms</span>
                  </div>
                </div>
                <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium shrink-0 ${feedLabel[item.status].cls}`}>
                  {feedLabel[item.status].text}
                </span>
                <span className="text-xs text-gray-400 shrink-0 w-20 text-right">{item.timestamp}</span>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}
