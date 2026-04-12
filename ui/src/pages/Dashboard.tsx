import { useApi } from '../lib/hooks';
import { spend, models as modelsApi, keys as keysApi, health, type ProviderHealthStatus } from '../lib/api';
import { fmtCompact } from '../lib/format';
import { providerDisplayName } from '../lib/providers';
import {
  DollarSign,
  Zap,
  Key,
  Box,
  Clock,
  Server,
  Database,
  TrendingUp,
  Activity,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  PieChart,
  Pie,
  Cell,
} from 'recharts';

const COLORS = ['#8b5cf6', '#3b82f6', '#06b6d4', '#10b981', '#f59e0b'];

type SpendReportRow = { group_key: string; total_spend: number; request_count?: number; total_tokens?: number; display_name?: string | null };
type ProviderSpendDatum = { provider: string; label: string; spend: number };

function fmtDollar(n: number | null | undefined): string {
  if (n == null) return '$0.0000';
  return `$${Number(n).toFixed(4)}`;
}

function fmtDollarPrecise(n: number | null | undefined): string {
  if (n == null) return '$0.0000';
  return `$${Number(n).toFixed(4)}`;
}

interface ProviderAgg {
  provider: string;
  status: ProviderHealthStatus;
  models: number;
  healthy_models: number;
  unhealthy_models: number;
}

const statusConfig: Record<ProviderHealthStatus, { dot: string; text: string; bg: string; label: string }> = {
  healthy: { dot: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50', label: 'Healthy' },
  degraded: { dot: 'bg-amber-500', text: 'text-amber-700', bg: 'bg-amber-50', label: 'Degraded' },
  down: { dot: 'bg-rose-500', text: 'text-rose-700', bg: 'bg-rose-50', label: 'Down' },
};

export default function Dashboard() {
  const { data: summary } = useApi(() => spend.summary(), []);
  const { data: dailyReport } = useApi(() => spend.report('day'), []);
  const { data: providerReport } = useApi(() => spend.report('provider'), []);
  const { data: providerHealthSummary } = useApi(() => modelsApi.providerHealthSummary(), []);
  const { data: keysResult } = useApi(() => keysApi.list(), []);
  const { data: healthData } = useApi(() => health.check(), []);

  const daily = (dailyReport?.breakdown || dailyReport?.data || []).map((r: any) => ({
    date: r.group_key,
    success: r.successful_requests ?? r.request_count ?? 0,
    failed: r.failed_requests ?? 0,
  }));

  const providerSpend: ProviderSpendDatum[] = ((providerReport?.breakdown || providerReport?.data || []) as SpendReportRow[]).map((r) => {
    const provider = (r.group_key || 'unknown').trim().toLowerCase() || 'unknown';
    return {
      provider,
      label: providerDisplayName(provider),
      spend: r.total_spend,
    };
  });

  const providerList = (providerHealthSummary?.providers || []) as ProviderAgg[];
  const totalModels = providerHealthSummary?.total_models ?? 0;
  const totalProviders = providerHealthSummary?.summary.total_providers ?? providerList.length;

  const healthStatus = healthData?.readiness?.status || healthData?.liveliness || 'unknown';
  const isHealthy = healthStatus === 'ok' || healthStatus === 'healthy';
  const activeProviders =
    providerHealthSummary?.summary.active_providers ?? providerList.filter((p) => p.status !== 'down').length;

  const totalRequests = summary?.total_requests ?? 0;
  const failedRequests = summary?.failed_requests ?? 0;

  return (
    <div className="p-4 sm:p-6">
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
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{fmtDollar(summary?.total_spend)}</p>
              <p className="mt-1 text-xs text-gray-500">{fmtCompact(summary?.total_tokens)} tokens used</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50">
              <Zap className="h-5 w-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Total Requests</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{fmtCompact(totalRequests)}</p>
              <p className={`mt-1 text-xs font-medium ${failedRequests > 0 ? 'text-red-500' : 'text-gray-400'}`}>{fmtCompact(failedRequests)} failed</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-50">
              <Key className="h-5 w-5 text-emerald-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Active Keys</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{fmtCompact(keysResult?.pagination?.total ?? keysResult?.data?.length)}</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-50">
              <Box className="h-5 w-5 text-amber-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Models</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{fmtCompact(totalModels)}</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-cyan-50">
              <Clock className="h-5 w-5 text-cyan-600" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Providers</p>
              <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{totalProviders}</p>
              <p className={`mt-1 text-xs font-medium ${activeProviders === totalProviders ? 'text-emerald-600' : 'text-amber-600'}`}>
                {activeProviders} active
              </p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
            <h2 className="text-base font-semibold text-gray-900 mb-4">Request Volume</h2>
            <div className="h-64">
              {daily.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={daily} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorRequests" x1="0" y1="0" x2="0" y2="1">
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
                    <Area type="monotone" dataKey="success" stackId="1" stroke="#8b5cf6" strokeWidth={2} fillOpacity={1} fill="url(#colorRequests)" name="Successful" />
                    <Area type="monotone" dataKey="failed" stackId="1" stroke="#f43f5e" strokeWidth={2} fill="#fecdd3" name="Failed" />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-full text-gray-400 text-sm">
                  <div className="text-center">
                    <Activity className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    <p>No request data yet</p>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
            <h2 className="text-base font-semibold text-gray-900 mb-4">Cost by Provider</h2>
            <div className="min-h-[256px]">
              {providerSpend.length > 0 ? (
                <div className="flex flex-col sm:flex-row items-center gap-4">
                  <div className="w-full sm:w-1/2 h-48 sm:h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={providerSpend}
                          dataKey="spend"
                          nameKey="label"
                          cx="50%"
                          cy="50%"
                          outerRadius="70%"
                          innerRadius="45%"
                          paddingAngle={2}
                        >
                          {providerSpend.map((_, index) => (
                            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip
                          formatter={(value: any) => [fmtDollarPrecise(value), 'Spend']}
                          contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.05)', fontSize: '13px' }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="w-full sm:w-1/2 sm:pl-2 overflow-y-auto max-h-48 sm:max-h-64">
                    <div className="space-y-3">
                      {providerSpend.map((p, idx) => (
                        <div key={p.provider} className="flex items-center justify-between">
                          <div className="flex items-center gap-2 overflow-hidden">
                            <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: COLORS[idx % COLORS.length] }} />
                            <span className="text-sm text-gray-600 truncate">{p.label}</span>
                          </div>
                          <span className="text-sm font-medium text-gray-900 tabular-nums shrink-0 ml-2">{fmtDollarPrecise(p.spend)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-center w-full h-64 text-gray-400 text-sm">
                  <div className="text-center">
                    <Box className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    <p>No provider data yet</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="bg-gray-100/80 border border-gray-200/80 rounded-xl p-3 flex flex-wrap items-center gap-6 text-sm">
          <div className="flex items-center gap-2">
            <div className="relative flex h-2.5 w-2.5">
              {isHealthy ? (
                <>
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
                </>
              ) : (
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-amber-500"></span>
              )}
            </div>
            <span className="font-medium text-gray-700">{isHealthy ? 'Healthy' : healthStatus}</span>
          </div>

          <div className="h-4 w-px bg-gray-300 hidden sm:block"></div>

          <div className="flex items-center gap-1.5 text-gray-600">
            <Server className="h-4 w-4 text-gray-400" />
            <span>{activeProviders} Provider{activeProviders !== 1 ? 's' : ''} Active</span>
          </div>

          <div className="h-4 w-px bg-gray-300 hidden sm:block"></div>

          <div className="flex items-center gap-1.5 text-gray-600">
            <Database className="h-4 w-4 text-gray-400" />
            <span>{fmtCompact(totalModels)} Model{totalModels !== 1 ? 's' : ''} Deployed</span>
          </div>

          <div className="h-4 w-px bg-gray-300 hidden sm:block"></div>

          <div className="flex items-center gap-1.5 text-gray-600">
            <TrendingUp className="h-4 w-4 text-gray-400" />
            <span>{fmtCompact(totalRequests)} Total Requests</span>
          </div>
        </div>

        {providerList.length > 0 && (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
              <h2 className="text-base font-semibold text-gray-900">Provider Health</h2>
              <span className="text-xs text-gray-400">{totalProviders} provider{totalProviders !== 1 ? 's' : ''} configured</span>
            </div>
            <div className="divide-y divide-gray-100">
              {providerList.map((p) => {
                const cfg = statusConfig[p.status];
                return (
                  <div key={p.provider} className="flex items-center justify-between px-5 py-3.5">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className={`h-2.5 w-2.5 rounded-full shrink-0 ${cfg.dot}`} />
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">{providerDisplayName(p.provider)}</p>
                        <p className="text-xs text-gray-400">{p.models} model{p.models > 1 ? 's' : ''}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-5 shrink-0">
                      <div className="text-right">
                        <p className="text-sm tabular-nums text-gray-700">{p.healthy_models}/{p.models}</p>
                        <p className="text-[11px] text-gray-400">healthy</p>
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
        )}

      </div>
    </div>
  );
}
