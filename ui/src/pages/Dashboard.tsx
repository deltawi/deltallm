import { useApi } from '../lib/hooks';
import { spend, models as modelsApi, keys as keysApi, health } from '../lib/api';
import StatCard from '../components/StatCard';
import Card from '../components/Card';
import { DollarSign, Zap, Key, Box, Activity } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

const COLORS = ['#3b82f6', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#6366f1'];

function fmt(n: number | null | undefined): string {
  if (n == null) return '$0.00';
  return `$${Number(n).toFixed(4)}`;
}

function fmtNum(n: number | null | undefined): string {
  if (n == null) return '0';
  return Number(n).toLocaleString();
}

export default function Dashboard() {
  const { data: summary } = useApi(() => spend.summary(), []);
  const { data: dailyReport } = useApi(() => spend.report('day'), []);
  const { data: modelReport } = useApi(() => spend.report('model'), []);
  const { data: modelsList } = useApi(() => modelsApi.list(), []);
  const { data: keysList } = useApi(() => keysApi.list(), []);
  const { data: healthData } = useApi(() => health.check(), []);

  const daily = (dailyReport?.breakdown || []).map((r: any) => ({ date: r.group_key, total_spend: r.total_spend }));
  const perModel = (modelReport?.breakdown || []).map((r: any) => ({ model: r.group_key, total_spend: r.total_spend }));

  const healthStatus = healthData?.readiness?.status || healthData?.liveliness || 'unknown';

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-sm text-gray-500 mt-1">Overview of your LLM proxy</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          title="Total Spend"
          value={fmt(summary?.total_spend)}
          icon={<DollarSign className="w-5 h-5" />}
          subtitle={`${fmtNum(summary?.total_tokens)} tokens used`}
        />
        <StatCard
          title="Total Requests"
          value={fmtNum(summary?.total_requests)}
          icon={<Zap className="w-5 h-5" />}
        />
        <StatCard
          title="Active Keys"
          value={fmtNum(keysList?.length)}
          icon={<Key className="w-5 h-5" />}
        />
        <StatCard
          title="Models"
          value={fmtNum(modelsList?.length)}
          icon={<Box className="w-5 h-5" />}
          subtitle={healthStatus === 'ok' ? 'System healthy' : `Status: ${healthStatus}`}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card title="Daily Spend">
          {daily && daily.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={daily}>
                <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => `$${v}`} />
                <Tooltip formatter={(v: any) => [`$${Number(v).toFixed(4)}`, 'Spend']} />
                <Bar dataKey="total_spend" fill="#3b82f6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-[250px] text-gray-400 text-sm">
              <div className="text-center">
                <Activity className="w-8 h-8 mx-auto mb-2 opacity-50" />
                <p>No spend data yet</p>
              </div>
            </div>
          )}
        </Card>

        <Card title="Spend by Model">
          {perModel && perModel.length > 0 ? (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width="50%" height={250}>
                <PieChart>
                  <Pie
                    data={perModel}
                    dataKey="total_spend"
                    nameKey="model"
                    cx="50%"
                    cy="50%"
                    outerRadius={90}
                    innerRadius={50}
                  >
                    {perModel.map((_, i) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v: any) => `$${Number(v).toFixed(4)}`} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2">
                {perModel.slice(0, 6).map((m: any, i: number) => (
                  <div key={m.model} className="flex items-center gap-2 text-sm">
                    <div className="w-3 h-3 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
                    <span className="text-gray-600 truncate flex-1">{m.model}</span>
                    <span className="font-medium text-gray-900">{fmt(m.total_spend)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center h-[250px] text-gray-400 text-sm">
              <div className="text-center">
                <Box className="w-8 h-8 mx-auto mb-2 opacity-50" />
                <p>No model data yet</p>
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
