import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Key,
  CreditCard,
  Activity,
  Loader2,
  Trash2,
  Clock,
  Zap,
} from 'lucide-react';
import { useApiKey, useDeleteApiKey, useKeySpendLogs } from '@/hooks/useApiKeys';
import { BudgetProgress } from '@/components/BudgetProgress';
import { DataTable } from '@/components/DataTable';
import type { SpendLog } from '@/types';

export function ApiKeyDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const keyHash = id!;

  const { data: apiKey, isLoading: keyLoading } = useApiKey(keyHash);
  const { data: spendLogsData, isLoading: logsLoading } = useKeySpendLogs(apiKey?.id, 30);
  const deleteApiKey = useDeleteApiKey();

  const [activeTab, setActiveTab] = useState<'overview' | 'usage' | 'activity'>('overview');

  if (keyLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    );
  }

  if (!apiKey) {
    return (
      <div className="p-8">
        <button
          onClick={() => navigate('/api-keys')}
          className="flex items-center text-gray-600 hover:text-gray-900 mb-4"
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back to API Keys
        </button>
        <p className="text-gray-600">API key not found</p>
      </div>
    );
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const maskKeyHash = (hash: string) => {
    if (hash.length <= 12) return hash;
    return `${hash.slice(0, 8)}...${hash.slice(-8)}`;
  };

  const handleDelete = () => {
    if (confirm('Are you sure you want to delete this API key? This action cannot be undone.')) {
      deleteApiKey.mutate(apiKey.key_hash, {
        onSuccess: () => navigate('/api-keys'),
      });
    }
  };

  const spendLogs = spendLogsData?.logs || [];

  const logColumns = [
    {
      key: 'timestamp',
      header: 'Time',
      render: (log: SpendLog) => (
        <span className="text-sm text-gray-600">{formatDate(log.created_at)}</span>
      ),
    },
    {
      key: 'model',
      header: 'Model',
      render: (log: SpendLog) => (
        <span className="text-sm font-medium text-gray-900">{log.model}</span>
      ),
    },
    {
      key: 'tokens',
      header: 'Tokens',
      render: (log: SpendLog) => (
        <div className="text-sm text-gray-600">
          <span className="text-green-600">{log.prompt_tokens}</span>
          {' / '}
          <span className="text-blue-600">{log.completion_tokens}</span>
          <span className="text-gray-400 ml-1">({log.total_tokens})</span>
        </div>
      ),
    },
    {
      key: 'spend',
      header: 'Cost',
      render: (log: SpendLog) => (
        <span className="text-sm font-medium text-gray-900">
          ${log.spend.toFixed(4)}
        </span>
      ),
    },
    {
      key: 'latency',
      header: 'Latency',
      render: (log: SpendLog) => (
        <span className="text-sm text-gray-600">
          {log.latency_ms ? `${log.latency_ms}ms` : '-'}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (log: SpendLog) => (
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
            log.status === 'success'
              ? 'bg-green-100 text-green-800'
              : 'bg-red-100 text-red-800'
          }`}
        >
          {log.status}
        </span>
      ),
    },
  ];

  // Calculate usage statistics
  const totalRequests = spendLogs.length;
  const totalSpend = spendLogs.reduce((acc, log) => acc + log.spend, 0);
  const totalTokens = spendLogs.reduce((acc, log) => acc + log.total_tokens, 0);
  const avgLatency = spendLogs.length > 0
    ? spendLogs.reduce((acc, log) => acc + (log.latency_ms || 0), 0) / spendLogs.length
    : 0;

  return (
    <div className="p-8">
      {/* Back button */}
      <button
        onClick={() => navigate('/api-keys')}
        className="flex items-center text-gray-600 hover:text-gray-900 mb-4"
      >
        <ArrowLeft className="w-4 h-4 mr-1" />
        Back to API Keys
      </button>

      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div className="flex items-center">
          <div className="w-12 h-12 rounded-lg bg-amber-100 flex items-center justify-center mr-4">
            <Key className="w-6 h-6 text-amber-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">
              {apiKey.key_alias || 'Unnamed Key'}
            </h1>
            <p className="text-gray-500 font-mono text-sm mt-1">
              {maskKeyHash(apiKey.key_hash)}
            </p>
          </div>
        </div>
        <button
          onClick={handleDelete}
          disabled={deleteApiKey.isPending}
          className="flex items-center px-4 py-2 text-red-600 border border-red-300 rounded-lg hover:bg-red-50 disabled:opacity-50"
        >
          <Trash2 className="w-4 h-4 mr-2" />
          {deleteApiKey.isPending ? 'Deleting...' : 'Delete Key'}
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="flex space-x-8">
          {[
            { key: 'overview', label: 'Overview', icon: Key },
            { key: 'usage', label: 'Usage Stats', icon: Activity },
            { key: 'activity', label: 'Recent Activity', icon: Clock },
          ].map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key as typeof activeTab)}
                className={`flex items-center pb-4 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab.key
                    ? 'border-primary-500 text-primary-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <Icon className="w-4 h-4 mr-2" />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab Content */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Key Details */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Key Details</h3>
            <dl className="space-y-4">
              <div className="flex justify-between">
                <dt className="text-sm text-gray-500">Key Hash</dt>
                <dd className="text-sm font-mono text-gray-900">{maskKeyHash(apiKey.key_hash)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-sm text-gray-500">Alias</dt>
                <dd className="text-sm text-gray-900">{apiKey.key_alias || '-'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-sm text-gray-500">Created</dt>
                <dd className="text-sm text-gray-900">{formatDate(apiKey.created_at)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-sm text-gray-500">Expires</dt>
                <dd className="text-sm text-gray-900">
                  {apiKey.expires_at ? formatDate(apiKey.expires_at) : 'Never'}
                </dd>
              </div>
              {apiKey.org_id && (
                <div className="flex justify-between">
                  <dt className="text-sm text-gray-500">Organization ID</dt>
                  <dd className="text-sm font-mono text-gray-900">{apiKey.org_id}</dd>
                </div>
              )}
              {apiKey.team_id && (
                <div className="flex justify-between">
                  <dt className="text-sm text-gray-500">Team ID</dt>
                  <dd className="text-sm font-mono text-gray-900">{apiKey.team_id}</dd>
                </div>
              )}
            </dl>
          </div>

          {/* Budget */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Budget & Spend</h3>
            {apiKey.max_budget !== undefined ? (
              <BudgetProgress
                budget={{
                  entity_type: 'key',
                  entity_id: apiKey.id,
                  max_budget: apiKey.max_budget,
                  current_spend: apiKey.spend,
                  remaining_budget: apiKey.max_budget - apiKey.spend,
                  budget_utilization_percent: (apiKey.spend / apiKey.max_budget) * 100,
                  is_exceeded: apiKey.spend >= apiKey.max_budget,
                }}
                size="lg"
              />
            ) : (
              <div className="text-center py-4">
                <CreditCard className="w-8 h-8 text-gray-400 mx-auto mb-2" />
                <p className="text-gray-600">No budget limit set</p>
                <p className="text-2xl font-bold text-gray-900 mt-2">
                  ${apiKey.spend.toFixed(2)} <span className="text-sm font-normal text-gray-500">spent</span>
                </p>
              </div>
            )}
          </div>

          {/* Rate Limits */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Rate Limits</h3>
            <div className="grid grid-cols-2 gap-4">
              <div className="text-center p-4 bg-gray-50 rounded-lg">
                <Zap className="w-6 h-6 text-amber-500 mx-auto mb-2" />
                <p className="text-2xl font-bold text-gray-900">
                  {apiKey.tpm_limit ? apiKey.tpm_limit.toLocaleString() : '∞'}
                </p>
                <p className="text-sm text-gray-500">Tokens per minute</p>
              </div>
              <div className="text-center p-4 bg-gray-50 rounded-lg">
                <Activity className="w-6 h-6 text-blue-500 mx-auto mb-2" />
                <p className="text-2xl font-bold text-gray-900">
                  {apiKey.rpm_limit ? apiKey.rpm_limit.toLocaleString() : '∞'}
                </p>
                <p className="text-sm text-gray-500">Requests per minute</p>
              </div>
            </div>
          </div>

          {/* Allowed Models */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Allowed Models</h3>
            {apiKey.models && apiKey.models.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {apiKey.models.map((model) => (
                  <span
                    key={model}
                    className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-primary-100 text-primary-800"
                  >
                    {model}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-gray-500">All models allowed</p>
            )}
          </div>
        </div>
      )}

      {activeTab === 'usage' && (
        <div className="space-y-6">
          {/* Stats Cards */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <p className="text-sm text-gray-500">Total Requests (30d)</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">{totalRequests}</p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <p className="text-sm text-gray-500">Total Spend (30d)</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">${totalSpend.toFixed(2)}</p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <p className="text-sm text-gray-500">Total Tokens (30d)</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">{totalTokens.toLocaleString()}</p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <p className="text-sm text-gray-500">Avg Latency</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">{avgLatency.toFixed(0)}ms</p>
            </div>
          </div>

          {/* Lifetime Stats */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Lifetime Statistics</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-sm text-gray-500">Total Spend</p>
                <p className="text-xl font-bold text-gray-900">${apiKey.spend.toFixed(2)}</p>
              </div>
              {apiKey.max_budget && (
                <>
                  <div>
                    <p className="text-sm text-gray-500">Budget Limit</p>
                    <p className="text-xl font-bold text-gray-900">${apiKey.max_budget.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Remaining</p>
                    <p className="text-xl font-bold text-gray-900">
                      ${(apiKey.max_budget - apiKey.spend).toFixed(2)}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Utilization</p>
                    <p className="text-xl font-bold text-gray-900">
                      {((apiKey.spend / apiKey.max_budget) * 100).toFixed(1)}%
                    </p>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'activity' && (
        <div className="bg-white rounded-xl border border-gray-200">
          <div className="p-4 border-b border-gray-200">
            <h3 className="text-lg font-semibold text-gray-900">Recent Requests</h3>
            <p className="text-sm text-gray-500">Last 30 days of activity</p>
          </div>
          {logsLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            </div>
          ) : spendLogs.length > 0 ? (
            <DataTable
              columns={logColumns}
              data={spendLogs}
              keyExtractor={(log) => log.id}
            />
          ) : (
            <div className="text-center py-12">
              <Activity className="w-8 h-8 text-gray-400 mx-auto mb-2" />
              <p className="text-gray-600">No recent activity</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
