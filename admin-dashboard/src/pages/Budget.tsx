import { useState } from 'react';
import {
  CreditCard,
  TrendingUp,
  Clock,
  CheckCircle,
  XCircle,
  Download,
} from 'lucide-react';
import { useOrganizations } from '@/hooks/useOrganizations';
import { useSpendSummary, useSpendLogs } from '@/hooks/useBudget';
import { DataTable } from '@/components/DataTable';
import type { SpendLog } from '@/types';

export function Budget() {
  const [selectedOrg, setSelectedOrg] = useState<string>('');
  const [days, setDays] = useState(30);

  const { data: organizations } = useOrganizations();
  const { data: summary } = useSpendSummary({
    org_id: selectedOrg || undefined,
    days,
  });
  const { data: logsData } = useSpendLogs({
    org_id: selectedOrg || undefined,
    days,
    limit: 100,
  });

  const stats = [
    {
      label: 'Total Spend',
      value: summary ? `$${summary.total_spend.toFixed(2)}` : '$0.00',
      icon: CreditCard,
      color: 'text-blue-600',
    },
    {
      label: 'Total Requests',
      value: summary?.total_requests || 0,
      icon: TrendingUp,
      color: 'text-green-600',
    },
    {
      label: 'Total Tokens',
      value: summary?.total_tokens.toLocaleString() || '0',
      icon: Clock,
      color: 'text-purple-600',
    },
    {
      label: 'Success Rate',
      value: summary
        ? `${((summary.successful_requests / summary.total_requests) * 100).toFixed(1)}%`
        : '0%',
      icon: CheckCircle,
      color: 'text-orange-600',
    },
  ];

  const logColumns = [
    {
      key: 'model',
      header: 'Model',
      render: (log: SpendLog) => (
        <div>
          <p className="font-medium text-gray-900">{log.model}</p>
          <p className="text-xs text-gray-500">{log.provider}</p>
        </div>
      ),
    },
    {
      key: 'tokens',
      header: 'Tokens',
      render: (log: SpendLog) => (
        <div className="text-sm text-gray-600">
          <span className="text-green-600">{log.prompt_tokens}</span> /{' '}
          <span className="text-blue-600">{log.completion_tokens}</span>
        </div>
      ),
    },
    {
      key: 'spend',
      header: 'Cost',
      render: (log: SpendLog) => (
        <span className="font-medium text-gray-900">
          ${log.spend.toFixed(4)}
        </span>
      ),
    },
    {
      key: 'latency',
      header: 'Latency',
      render: (log: SpendLog) => (
        <span className="text-sm text-gray-600">
          {log.latency_ms ? `${log.latency_ms.toFixed(0)}ms` : 'N/A'}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (log: SpendLog) =>
        log.status === 'success' ? (
          <span className="flex items-center text-green-600 text-sm">
            <CheckCircle className="w-4 h-4 mr-1" />
            Success
          </span>
        ) : (
          <span className="flex items-center text-red-600 text-sm">
            <XCircle className="w-4 h-4 mr-1" />
            Failed
          </span>
        ),
    },
    {
      key: 'time',
      header: 'Time',
      render: (log: SpendLog) => (
        <span className="text-sm text-gray-500">
          {new Date(log.created_at).toLocaleString()}
        </span>
      ),
    },
  ];

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Budget & Usage</h1>
          <p className="text-gray-600 mt-1">
            Track spending and usage across your organizations
          </p>
        </div>
        <button className="flex items-center px-4 py-2 text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50">
          <Download className="w-4 h-4 mr-2" />
          Export Report
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-8">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Organization
          </label>
          <select
            value={selectedOrg}
            onChange={(e) => setSelectedOrg(e.target.value)}
            className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          >
            <option value="">All Organizations</option>
            {organizations?.items.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Time Period
          </label>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          >
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <div
              key={stat.label}
              className="bg-white rounded-xl p-6 border border-gray-200"
            >
              <div className="flex items-center justify-between mb-4">
                <Icon className={`w-8 h-8 ${stat.color}`} />
              </div>
              <p className="text-2xl font-bold text-gray-900">{stat.value}</p>
              <p className="text-sm text-gray-600">{stat.label}</p>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Top Models */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Top Models</h3>
          <div className="space-y-4">
            {summary?.top_models?.map((model, index) => (
              <div key={model.model} className="flex items-center justify-between">
                <div className="flex items-center">
                  <span className="w-6 h-6 rounded-full bg-gray-100 flex items-center justify-center text-xs font-medium text-gray-600 mr-3">
                    {index + 1}
                  </span>
                  <div>
                    <p className="font-medium text-gray-900">{model.model}</p>
                    <p className="text-sm text-gray-500">{model.requests} requests</p>
                  </div>
                </div>
                <span className="font-medium text-gray-900">
                  ${model.spend.toFixed(2)}
                </span>
              </div>
            ))}
            {(!summary?.top_models || summary.top_models.length === 0) && (
              <p className="text-gray-500 text-center py-4">No data available</p>
            )}
          </div>
        </div>

        {/* Daily Breakdown Chart */}
        <div className="lg:col-span-2 bg-white rounded-xl border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Daily Spend</h3>
          <div className="h-64 flex items-end space-x-2">
            {summary?.daily_breakdown?.map((day) => {
              const maxSpend = Math.max(
                ...summary.daily_breakdown.map((d) => d.spend),
                0.01
              );
              const height = (day.spend / maxSpend) * 100;
              return (
                <div
                  key={day.date}
                  className="flex-1 flex flex-col items-center"
                >
                  <div
                    className="w-full bg-primary-500 rounded-t hover:bg-primary-600 transition-colors relative group"
                    style={{ height: `${Math.max(height, 2)}%` }}
                  >
                    <div className="absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 px-2 py-1 bg-gray-900 text-white text-xs rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">
                      {day.date}: ${day.spend.toFixed(2)}
                    </div>
                  </div>
                  <span className="text-xs text-gray-500 mt-2 rotate-45 origin-left">
                    {new Date(day.date).toLocaleDateString(undefined, {
                      month: 'short',
                      day: 'numeric',
                    })}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Spend Logs */}
      <div className="mt-8">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Recent Activity</h3>
        <DataTable
          columns={logColumns}
          data={logsData?.logs || []}
          keyExtractor={(log) => log.id}
        />
      </div>
    </div>
  );
}
