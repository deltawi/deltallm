import { useState, useMemo } from 'react';
import { Plus, Search, Server, Trash2, Power, PowerOff, Edit2 } from 'lucide-react';
import {
  useDeployments,
  useCreateDeployment,
  useUpdateDeployment,
  useDeleteDeployment,
  useEnableDeployment,
  useDisableDeployment,
} from '@/hooks/useDeployments';
import { useProviders } from '@/hooks/useProviders';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import type { ModelDeployment, CreateDeploymentRequest } from '@/types';

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

export function Deployments() {
  const { data: deploymentsData, isLoading } = useDeployments();
  const { data: providersData } = useProviders();
  const createDeployment = useCreateDeployment();
  const updateDeployment = useUpdateDeployment();
  const deleteDeployment = useDeleteDeployment();
  const enableDeployment = useEnableDeployment();
  const disableDeployment = useDisableDeployment();

  const [searchQuery, setSearchQuery] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingDeployment, setEditingDeployment] = useState<ModelDeployment | null>(null);
  const [deletingDeployment, setDeletingDeployment] = useState<ModelDeployment | null>(null);

  const [formData, setFormData] = useState<CreateDeploymentRequest>({
    model_name: '',
    provider_model: '',
    provider_config_id: '',
    is_active: true,
    priority: 1,
    tpm_limit: undefined,
    rpm_limit: undefined,
    timeout: undefined,
    settings: {},
  });

  const deployments = deploymentsData?.items || [];
  const providers = providersData?.items || [];

  const filteredDeployments = useMemo(() => {
    if (!searchQuery) return deployments;
    const query = searchQuery.toLowerCase();
    return deployments.filter(
      (d) =>
        d.model_name.toLowerCase().includes(query) ||
        d.provider_model.toLowerCase().includes(query) ||
        (d.provider_name?.toLowerCase().includes(query) ?? false)
    );
  }, [deployments, searchQuery]);

  const resetForm = () => {
    setFormData({
      model_name: '',
      provider_model: '',
      provider_config_id: '',
      is_active: true,
      priority: 1,
      tpm_limit: undefined,
      rpm_limit: undefined,
      timeout: undefined,
      settings: {},
    });
    setEditingDeployment(null);
  };

  const openCreateModal = () => {
    resetForm();
    setIsModalOpen(true);
  };

  const openEditModal = (deployment: ModelDeployment) => {
    setEditingDeployment(deployment);
    setFormData({
      model_name: deployment.model_name,
      provider_model: deployment.provider_model,
      provider_config_id: deployment.provider_config_id,
      is_active: deployment.is_active,
      priority: deployment.priority,
      tpm_limit: deployment.tpm_limit,
      rpm_limit: deployment.rpm_limit,
      timeout: deployment.timeout,
      settings: deployment.settings,
    });
    setIsModalOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (editingDeployment) {
        await updateDeployment.mutateAsync({
          id: editingDeployment.id,
          data: formData,
        });
      } else {
        await createDeployment.mutateAsync(formData);
      }
      setIsModalOpen(false);
      resetForm();
    } catch (error) {
      // Error handled by mutation
    }
  };

  const handleDelete = async () => {
    if (!deletingDeployment) return;
    try {
      await deleteDeployment.mutateAsync(deletingDeployment.id);
      setDeletingDeployment(null);
    } catch (error) {
      // Error handled by mutation
    }
  };

  const handleToggleActive = async (deployment: ModelDeployment) => {
    if (deployment.is_active) {
      await disableDeployment.mutateAsync(deployment.id);
    } else {
      await enableDeployment.mutateAsync(deployment.id);
    }
  };

  const columns = [
    {
      key: 'model_name',
      header: 'Model',
      render: (deployment: ModelDeployment) => (
        <div className="flex items-center">
          <div className="w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center mr-3">
            <Server className="w-5 h-5 text-gray-600" />
          </div>
          <div>
            <p className="font-medium text-gray-900">{deployment.model_name}</p>
            <p className="text-sm text-gray-500">{deployment.provider_model}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'provider',
      header: 'Provider',
      render: (deployment: ModelDeployment) => {
        const colorClass = PROVIDER_COLORS[deployment.provider_type || ''] || 'bg-gray-100 text-gray-800';
        return (
          <div>
            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorClass}`}>
              {deployment.provider_type || 'Unknown'}
            </span>
            <p className="text-sm text-gray-600 mt-1">{deployment.provider_name}</p>
          </div>
        );
      },
    },
    {
      key: 'status',
      header: 'Status',
      render: (deployment: ModelDeployment) => (
        <span
          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
            deployment.is_active
              ? 'bg-green-100 text-green-800'
              : 'bg-gray-100 text-gray-800'
          }`}
        >
          {deployment.is_active ? 'Active' : 'Inactive'}
        </span>
      ),
    },
    {
      key: 'priority',
      header: 'Priority',
      render: (deployment: ModelDeployment) => (
        <span className="text-sm text-gray-600">{deployment.priority}</span>
      ),
    },
    {
      key: 'limits',
      header: 'Limits',
      render: (deployment: ModelDeployment) => (
        <div className="text-sm text-gray-600">
          {deployment.tpm_limit && <div>TPM: {deployment.tpm_limit.toLocaleString()}</div>}
          {deployment.rpm_limit && <div>RPM: {deployment.rpm_limit.toLocaleString()}</div>}
          {!deployment.tpm_limit && !deployment.rpm_limit && <span className="text-gray-400">-</span>}
        </div>
      ),
    },
    {
      key: 'actions',
      header: 'Actions',
      render: (deployment: ModelDeployment) => (
        <div className="flex items-center space-x-2">
          <button
            onClick={() => handleToggleActive(deployment)}
            className={`p-1.5 rounded-md transition-colors ${
              deployment.is_active
                ? 'text-green-600 hover:bg-green-50'
                : 'text-gray-400 hover:bg-gray-100'
            }`}
            title={deployment.is_active ? 'Disable' : 'Enable'}
          >
            {deployment.is_active ? <Power className="w-4 h-4" /> : <PowerOff className="w-4 h-4" />}
          </button>
          <button
            onClick={() => openEditModal(deployment)}
            className="p-1.5 text-blue-600 hover:bg-blue-50 rounded-md transition-colors"
            title="Edit"
          >
            <Edit2 className="w-4 h-4" />
          </button>
          <button
            onClick={() => setDeletingDeployment(deployment)}
            className="p-1.5 text-red-600 hover:bg-red-50 rounded-md transition-colors"
            title="Delete"
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
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Model Deployments</h1>
        <p className="text-gray-600 mt-1">
          Manage model deployments and their provider configurations
        </p>
      </div>

      {/* Actions Bar */}
      <div className="mb-6 flex flex-col sm:flex-row gap-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search deployments..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
        </div>
        <button
          onClick={openCreateModal}
          className="inline-flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
        >
          <Plus className="w-5 h-5 mr-2" />
          Add Deployment
        </button>
      </div>

      {/* Stats */}
      <div className="mb-6 flex items-center gap-4 text-sm text-gray-600">
        <span>
          <strong>{filteredDeployments.length}</strong> deployment
          {filteredDeployments.length !== 1 ? 's' : ''}
        </span>
        <span>
          <strong>{deployments.filter((d) => d.is_active).length}</strong> active
        </span>
      </div>

      {/* Deployments Table */}
      {isLoading ? (
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={filteredDeployments}
          keyExtractor={(deployment) => deployment.id}
        />
      )}

      {/* Create/Edit Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          resetForm();
        }}
        title={editingDeployment ? 'Edit Deployment' : 'Add Deployment'}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Model Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              required
              placeholder="e.g., gpt-4o"
              value={formData.model_name}
              onChange={(e) => setFormData({ ...formData, model_name: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
            <p className="text-xs text-gray-500 mt-1">
              Public name exposed to API users
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Provider Model <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              required
              placeholder="e.g., gpt-4o-2024-08-06"
              value={formData.provider_model}
              onChange={(e) => setFormData({ ...formData, provider_model: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
            <p className="text-xs text-gray-500 mt-1">
              Actual model identifier at the provider
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Provider <span className="text-red-500">*</span>
            </label>
            <select
              required
              value={formData.provider_config_id || ''}
              onChange={(e) => setFormData({ ...formData, provider_config_id: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            >
              <option value="">Select a provider</option>
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.name} ({provider.provider_type})
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Priority
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={formData.priority}
                onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) || 1 })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Status
              </label>
              <select
                value={formData.is_active ? 'active' : 'inactive'}
                onChange={(e) => setFormData({ ...formData, is_active: e.target.value === 'active' })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                TPM Limit
              </label>
              <input
                type="number"
                min={0}
                placeholder="Unlimited"
                value={formData.tpm_limit || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    tpm_limit: e.target.value ? parseInt(e.target.value) : undefined,
                  })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                RPM Limit
              </label>
              <input
                type="number"
                min={0}
                placeholder="Unlimited"
                value={formData.rpm_limit || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    rpm_limit: e.target.value ? parseInt(e.target.value) : undefined,
                  })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Timeout (s)
              </label>
              <input
                type="number"
                min={1}
                max={600}
                placeholder="Default"
                value={formData.timeout || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    timeout: e.target.value ? parseFloat(e.target.value) : undefined,
                  })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>
          </div>

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={() => {
                setIsModalOpen(false);
                resetForm();
              }}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createDeployment.isPending || updateDeployment.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors disabled:opacity-50"
            >
              {createDeployment.isPending || updateDeployment.isPending
                ? 'Saving...'
                : editingDeployment
                ? 'Save Changes'
                : 'Create Deployment'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={!!deletingDeployment}
        onClose={() => setDeletingDeployment(null)}
        title="Delete Deployment"
      >
        <div className="space-y-4">
          <p className="text-gray-600">
            Are you sure you want to delete the deployment{' '}
            <strong>{deletingDeployment?.model_name}</strong>? This action cannot be undone.
          </p>
          <div className="flex justify-end space-x-3">
            <button
              onClick={() => setDeletingDeployment(null)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleDelete}
              disabled={deleteDeployment.isPending}
              className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors disabled:opacity-50"
            >
              {deleteDeployment.isPending ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
