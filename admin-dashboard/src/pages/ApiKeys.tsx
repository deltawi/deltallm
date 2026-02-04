import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Key, Trash2, Copy, Check, Eye, EyeOff } from 'lucide-react';
import { useApiKeys, useCreateApiKey, useDeleteApiKey } from '@/hooks/useApiKeys';
import { useOrganizations } from '@/hooks/useOrganizations';
import { useTeams } from '@/hooks/useTeams';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import { BudgetProgress } from '@/components/BudgetProgress';
import type { ApiKey, CreateApiKeyRequest } from '@/types';

export function ApiKeys() {
  const navigate = useNavigate();
  const { data: apiKeys, isLoading } = useApiKeys();
  const { data: organizations } = useOrganizations();
  const { data: teams } = useTeams();
  const createApiKey = useCreateApiKey();
  const deleteApiKey = useDeleteApiKey();

  const [searchQuery, setSearchQuery] = useState('');
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isSuccessModalOpen, setIsSuccessModalOpen] = useState(false);
  const [generatedKey, setGeneratedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [formData, setFormData] = useState<CreateApiKeyRequest>({
    key_alias: '',
    org_id: '',
    team_id: '',
    models: [],
    max_budget: undefined,
    tpm_limit: undefined,
    rpm_limit: undefined,
  });
  const [modelsInput, setModelsInput] = useState('');

  const filteredKeys = apiKeys?.items.filter(
    (key) =>
      key.key_alias?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      key.key_hash.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const dataToSend = {
        ...formData,
        org_id: formData.org_id || undefined,
        team_id: formData.team_id || undefined,
        models: modelsInput ? modelsInput.split(',').map((m) => m.trim()) : undefined,
        max_budget: formData.max_budget || undefined,
        tpm_limit: formData.tpm_limit || undefined,
        rpm_limit: formData.rpm_limit || undefined,
      };
      const result = await createApiKey.mutateAsync(dataToSend);
      setGeneratedKey(result.key);
      setIsCreateModalOpen(false);
      setIsSuccessModalOpen(true);
      setFormData({
        key_alias: '',
        org_id: '',
        team_id: '',
        models: [],
        max_budget: undefined,
        tpm_limit: undefined,
        rpm_limit: undefined,
      });
      setModelsInput('');
    } catch (error) {
      // Error handled by mutation
    }
  };

  const handleCopyKey = async () => {
    if (generatedKey) {
      await navigator.clipboard.writeText(generatedKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleCloseSuccessModal = () => {
    setIsSuccessModalOpen(false);
    setGeneratedKey(null);
    setCopied(false);
    setShowKey(false);
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  const maskKeyHash = (hash: string) => {
    if (hash.length <= 8) return hash;
    return `${hash.slice(0, 4)}...${hash.slice(-4)}`;
  };

  const columns = [
    {
      key: 'alias',
      header: 'API Key',
      render: (key: ApiKey) => (
        <div className="flex items-center">
          <div className="w-10 h-10 rounded-lg bg-amber-100 flex items-center justify-center mr-3">
            <Key className="w-5 h-5 text-amber-600" />
          </div>
          <div>
            <p className="font-medium text-gray-900">{key.key_alias || 'Unnamed Key'}</p>
            <p className="text-sm text-gray-500 font-mono">{maskKeyHash(key.key_hash)}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'models',
      header: 'Models',
      render: (key: ApiKey) => (
        <div className="flex flex-wrap gap-1">
          {key.models && key.models.length > 0 ? (
            key.models.slice(0, 2).map((model) => (
              <span
                key={model}
                className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800"
              >
                {model}
              </span>
            ))
          ) : (
            <span className="text-sm text-gray-500">All models</span>
          )}
          {key.models && key.models.length > 2 && (
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">
              +{key.models.length - 2} more
            </span>
          )}
        </div>
      ),
    },
    {
      key: 'budget',
      header: 'Budget',
      render: (key: ApiKey) =>
        key.max_budget !== undefined ? (
          <BudgetProgress
            budget={{
              entity_type: 'key',
              entity_id: key.id,
              max_budget: key.max_budget,
              current_spend: key.spend,
              remaining_budget: key.max_budget - key.spend,
              budget_utilization_percent: (key.spend / key.max_budget) * 100,
              is_exceeded: key.spend >= key.max_budget,
            }}
            size="sm"
          />
        ) : (
          <span className="text-sm text-gray-500">No limit</span>
        ),
    },
    {
      key: 'spend',
      header: 'Spend',
      render: (key: ApiKey) => (
        <span className="text-sm font-medium text-gray-900">
          ${key.spend.toFixed(2)}
        </span>
      ),
    },
    {
      key: 'limits',
      header: 'Rate Limits',
      render: (key: ApiKey) => (
        <div className="text-sm text-gray-600">
          {key.tpm_limit || key.rpm_limit ? (
            <div className="space-y-0.5">
              {key.tpm_limit && <div>TPM: {key.tpm_limit.toLocaleString()}</div>}
              {key.rpm_limit && <div>RPM: {key.rpm_limit.toLocaleString()}</div>}
            </div>
          ) : (
            <span className="text-gray-500">No limits</span>
          )}
        </div>
      ),
    },
    {
      key: 'expires',
      header: 'Expires',
      render: (key: ApiKey) => (
        <span className="text-sm text-gray-600">
          {key.expires_at ? formatDate(key.expires_at) : 'Never'}
        </span>
      ),
    },
    {
      key: 'created',
      header: 'Created',
      render: (key: ApiKey) => (
        <span className="text-sm text-gray-600">{formatDate(key.created_at)}</span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (key: ApiKey) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            if (confirm(`Are you sure you want to delete this API key? This action cannot be undone.`)) {
              deleteApiKey.mutate(key.key_hash);
            }
          }}
          className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
          title="Delete API key"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      ),
    },
  ];

  if (isLoading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">API Keys</h1>
          <p className="text-gray-600 mt-1">
            Manage API keys for programmatic access
          </p>
        </div>
        <button
          onClick={() => setIsCreateModalOpen(true)}
          className="flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium"
        >
          <Plus className="w-4 h-4 mr-2" />
          Generate Key
        </button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search by alias or key hash..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
        </div>
      </div>

      {/* API Keys Table */}
      <DataTable
        columns={columns}
        data={filteredKeys || []}
        keyExtractor={(key) => key.id}
        onRowClick={(key) => navigate(`/api-keys/${key.id}`)}
      />

      {/* Create Modal */}
      <Modal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        title="Generate API Key"
        size="lg"
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Key Alias
            </label>
            <input
              type="text"
              value={formData.key_alias || ''}
              onChange={(e) =>
                setFormData({ ...formData, key_alias: e.target.value })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              placeholder="My API Key"
            />
            <p className="text-xs text-gray-500 mt-1">
              A friendly name to identify this key
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Organization
              </label>
              <select
                value={formData.org_id || ''}
                onChange={(e) =>
                  setFormData({ ...formData, org_id: e.target.value, team_id: '' })
                }
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              >
                <option value="">None</option>
                {organizations?.items.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Team
              </label>
              <select
                value={formData.team_id || ''}
                onChange={(e) =>
                  setFormData({ ...formData, team_id: e.target.value })
                }
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                disabled={!formData.org_id}
              >
                <option value="">None</option>
                {teams?.items
                  .filter((team) => team.org_id === formData.org_id)
                  .map((team) => (
                    <option key={team.id} value={team.id}>
                      {team.name}
                    </option>
                  ))}
              </select>
              {!formData.org_id && (
                <p className="text-xs text-gray-500 mt-1">
                  Select an organization first
                </p>
              )}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Allowed Models
            </label>
            <input
              type="text"
              value={modelsInput}
              onChange={(e) => setModelsInput(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              placeholder="gpt-4, claude-3-opus, gemini-pro"
            />
            <p className="text-xs text-gray-500 mt-1">
              Comma-separated list of allowed models. Leave empty for all models.
            </p>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Budget ($)
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={formData.max_budget || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    max_budget: e.target.value ? parseFloat(e.target.value) : undefined,
                  })
                }
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                placeholder="100.00"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                TPM Limit
              </label>
              <input
                type="number"
                min="0"
                value={formData.tpm_limit || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    tpm_limit: e.target.value ? parseInt(e.target.value) : undefined,
                  })
                }
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                placeholder="100000"
              />
              <p className="text-xs text-gray-500 mt-1">Tokens per minute</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                RPM Limit
              </label>
              <input
                type="number"
                min="0"
                value={formData.rpm_limit || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    rpm_limit: e.target.value ? parseInt(e.target.value) : undefined,
                  })
                }
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                placeholder="60"
              />
              <p className="text-xs text-gray-500 mt-1">Requests per minute</p>
            </div>
          </div>

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={() => setIsCreateModalOpen(false)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createApiKey.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {createApiKey.isPending ? 'Generating...' : 'Generate Key'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Success Modal - Show Generated Key */}
      <Modal
        isOpen={isSuccessModalOpen}
        onClose={handleCloseSuccessModal}
        title="API Key Generated"
      >
        <div className="space-y-4">
          <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
            <div className="flex items-center mb-2">
              <Check className="w-5 h-5 text-green-600 mr-2" />
              <span className="font-medium text-green-800">Key created successfully!</span>
            </div>
            <p className="text-sm text-green-700">
              Make sure to copy your API key now. You won't be able to see it again!
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Your API Key
            </label>
            <div className="flex items-center space-x-2">
              <div className="flex-1 relative">
                <input
                  type={showKey ? 'text' : 'password'}
                  readOnly
                  value={generatedKey || ''}
                  className="w-full px-4 py-3 pr-10 border border-gray-300 rounded-lg bg-gray-50 font-mono text-sm"
                />
                <button
                  type="button"
                  onClick={() => setShowKey(!showKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700"
                >
                  {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <button
                type="button"
                onClick={handleCopyKey}
                className={`px-4 py-3 rounded-lg transition-colors flex items-center ${
                  copied
                    ? 'bg-green-600 text-white'
                    : 'bg-primary-600 text-white hover:bg-primary-700'
                }`}
              >
                {copied ? (
                  <>
                    <Check className="w-4 h-4 mr-2" />
                    Copied!
                  </>
                ) : (
                  <>
                    <Copy className="w-4 h-4 mr-2" />
                    Copy
                  </>
                )}
              </button>
            </div>
          </div>

          <div className="flex justify-end pt-4">
            <button
              type="button"
              onClick={handleCloseSuccessModal}
              className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors"
            >
              Done
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
