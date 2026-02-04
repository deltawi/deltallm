import { useState } from 'react';
import { Plus, Search, Server, Trash2, Play, CheckCircle, XCircle, Loader2 } from 'lucide-react';
import {
  useProviders,
  useCreateProvider,
  useDeleteProvider,
  useTestProviderConnectivity,
} from '@/hooks/useProviders';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import type { ProviderConfig, CreateProviderRequest } from '@/types';

const PROVIDER_TYPES = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'azure', label: 'Azure OpenAI' },
  { value: 'bedrock', label: 'AWS Bedrock' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'cohere', label: 'Cohere' },
  { value: 'mistral', label: 'Mistral' },
  { value: 'groq', label: 'Groq' },
  { value: 'ollama', label: 'Ollama' },
  { value: 'vllm', label: 'vLLM' },
];

const PROVIDER_COLORS: Record<string, string> = {
  openai: 'bg-green-100 text-green-800',
  anthropic: 'bg-orange-100 text-orange-800',
  azure: 'bg-blue-100 text-blue-800',
  bedrock: 'bg-yellow-100 text-yellow-800',
  gemini: 'bg-purple-100 text-purple-800',
  cohere: 'bg-pink-100 text-pink-800',
  mistral: 'bg-indigo-100 text-indigo-800',
  groq: 'bg-red-100 text-red-800',
  ollama: 'bg-gray-100 text-gray-800',
  vllm: 'bg-cyan-100 text-cyan-800',
};

export function Providers() {
  const { data: providersData, isLoading } = useProviders();
  const createProvider = useCreateProvider();
  const deleteProvider = useDeleteProvider();
  const testConnectivity = useTestProviderConnectivity();

  const [searchQuery, setSearchQuery] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [testingProviderId, setTestingProviderId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message?: string }>>({});

  const [formData, setFormData] = useState<CreateProviderRequest>({
    name: '',
    provider_type: 'openai',
    api_key: '',
    api_base: '',
    is_active: true,
  });

  const filteredProviders = providersData?.items.filter(
    (provider) =>
      provider.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      provider.provider_type.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createProvider.mutateAsync(formData);
      setIsModalOpen(false);
      setFormData({
        name: '',
        provider_type: 'openai',
        api_key: '',
        api_base: '',
        is_active: true,
      });
    } catch (error) {
      // Error handled by mutation
    }
  };

  const handleTestConnectivity = async (providerId: string) => {
    setTestingProviderId(providerId);
    try {
      const result = await testConnectivity.mutateAsync(providerId);
      setTestResults((prev) => ({
        ...prev,
        [providerId]: {
          success: result.success,
          message: result.success ? 'Connection successful' : result.error_message,
        },
      }));
    } catch (error: any) {
      setTestResults((prev) => ({
        ...prev,
        [providerId]: {
          success: false,
          message: error.message || 'Connection test failed',
        },
      }));
    } finally {
      setTestingProviderId(null);
    }
  };

  const columns = [
    {
      key: 'name',
      header: 'Provider',
      render: (provider: ProviderConfig) => {
        const colorClass = PROVIDER_COLORS[provider.provider_type] || 'bg-gray-100 text-gray-800';
        return (
          <div className="flex items-center">
            <div className="w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center mr-3">
              <Server className="w-5 h-5 text-gray-600" />
            </div>
            <div>
              <p className="font-medium text-gray-900">{provider.name}</p>
              <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorClass}`}>
                {provider.provider_type}
              </span>
            </div>
          </div>
        );
      },
    },
    {
      key: 'api_base',
      header: 'Endpoint',
      render: (provider: ProviderConfig) => (
        <span className="text-sm text-gray-600">
          {provider.api_base || 'Default'}
        </span>
      ),
    },
    {
      key: 'limits',
      header: 'Rate Limits',
      render: (provider: ProviderConfig) => (
        <div className="text-sm">
          {provider.tpm_limit || provider.rpm_limit ? (
            <div className="space-y-1">
              {provider.tpm_limit && (
                <div>
                  <span className="text-gray-500">TPM:</span>{' '}
                  <span className="font-medium">{provider.tpm_limit.toLocaleString()}</span>
                </div>
              )}
              {provider.rpm_limit && (
                <div>
                  <span className="text-gray-500">RPM:</span>{' '}
                  <span className="font-medium">{provider.rpm_limit.toLocaleString()}</span>
                </div>
              )}
            </div>
          ) : (
            <span className="text-gray-400">No limits</span>
          )}
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (provider: ProviderConfig) => {
        const testResult = testResults[provider.id];
        return (
          <div className="flex items-center space-x-2">
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                provider.is_active
                  ? 'bg-green-100 text-green-800'
                  : 'bg-gray-100 text-gray-800'
              }`}
            >
              {provider.is_active ? 'Active' : 'Inactive'}
            </span>
            {testResult && (
              <span
                className={`inline-flex items-center ${
                  testResult.success ? 'text-green-600' : 'text-red-600'
                }`}
                title={testResult.message}
              >
                {testResult.success ? (
                  <CheckCircle className="w-4 h-4" />
                ) : (
                  <XCircle className="w-4 h-4" />
                )}
              </span>
            )}
          </div>
        );
      },
    },
    {
      key: 'actions',
      header: '',
      render: (provider: ProviderConfig) => (
        <div className="flex items-center space-x-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              handleTestConnectivity(provider.id);
            }}
            disabled={testingProviderId === provider.id}
            className="p-2 text-blue-600 hover:bg-blue-50 rounded-lg transition-colors disabled:opacity-50"
            title="Test connectivity"
          >
            {testingProviderId === provider.id ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4" />
            )}
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (confirm(`Are you sure you want to delete provider "${provider.name}"?`)) {
                deleteProvider.mutate({ id: provider.id });
              }
            }}
            className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
            title="Delete provider"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Providers</h1>
          <p className="text-gray-600 mt-1">
            Manage LLM provider configurations and API connections
          </p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium"
        >
          <Plus className="w-4 h-4 mr-2" />
          Add Provider
        </button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search providers..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
        </div>
      </div>

      {/* Providers Table */}
      {isLoading ? (
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={filteredProviders || []}
          keyExtractor={(provider) => provider.id}
        />
      )}

      {/* Create Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title="Add Provider"
        size="lg"
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Provider Name
            </label>
            <input
              type="text"
              required
              value={formData.name}
              onChange={(e) =>
                setFormData({ ...formData, name: e.target.value })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              placeholder="My OpenAI Provider"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Provider Type
            </label>
            <select
              value={formData.provider_type}
              onChange={(e) =>
                setFormData({ ...formData, provider_type: e.target.value })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            >
              {PROVIDER_TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              API Key
            </label>
            <input
              type="password"
              value={formData.api_key}
              onChange={(e) =>
                setFormData({ ...formData, api_key: e.target.value })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              placeholder="sk-..."
            />
            <p className="text-xs text-gray-500 mt-1">
              The API key will be encrypted and stored securely
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              API Base URL (Optional)
            </label>
            <input
              type="url"
              value={formData.api_base}
              onChange={(e) =>
                setFormData({ ...formData, api_base: e.target.value })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              placeholder="https://api.openai.com/v1"
            />
            <p className="text-xs text-gray-500 mt-1">
              Leave empty to use the provider's default endpoint
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                TPM Limit (Optional)
              </label>
              <input
                type="number"
                value={formData.tpm_limit || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    tpm_limit: e.target.value ? parseInt(e.target.value) : undefined,
                  })
                }
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                placeholder="Tokens per minute"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                RPM Limit (Optional)
              </label>
              <input
                type="number"
                value={formData.rpm_limit || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    rpm_limit: e.target.value ? parseInt(e.target.value) : undefined,
                  })
                }
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                placeholder="Requests per minute"
              />
            </div>
          </div>

          <div className="flex items-center">
            <input
              type="checkbox"
              id="is_active"
              checked={formData.is_active}
              onChange={(e) =>
                setFormData({ ...formData, is_active: e.target.checked })
              }
              className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded"
            />
            <label htmlFor="is_active" className="ml-2 text-sm text-gray-700">
              Enable this provider
            </label>
          </div>

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={() => setIsModalOpen(false)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createProvider.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {createProvider.isPending ? 'Adding...' : 'Add Provider'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
