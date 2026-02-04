import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Trash2, Power, PowerOff, Edit2, Key, Link2, Box, AlertCircle, Eye, EyeOff } from 'lucide-react';
import {
  useDeployments,
  useCreateDeployment,
  useUpdateDeployment,
  useDeleteDeployment,
  useEnableDeployment,
  useDisableDeployment,
} from '@/hooks/useDeployments';
import { useProviders } from '@/hooks/useProviders';
import { useSetDeploymentPricing } from '@/hooks/usePricing';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import { PricingSection } from '@/components/PricingSection';
import type { ModelDeployment, CreateDeploymentRequest, PricingCreateRequest } from '@/types';

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

const PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'azure', label: 'Azure OpenAI' },
  { value: 'bedrock', label: 'AWS Bedrock' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'cohere', label: 'Cohere' },
  { value: 'mistral', label: 'Mistral AI' },
  { value: 'groq', label: 'Groq' },
  { value: 'ollama', label: 'Ollama' },
  { value: 'vllm', label: 'vLLM' },
];

const MODEL_TYPE_OPTIONS = [
  { value: 'chat', label: 'Chat', description: 'Conversational AI models' },
  { value: 'embedding', label: 'Embedding', description: 'Text embedding models' },
  { value: 'image_generation', label: 'Image Generation', description: 'DALL-E, Stable Diffusion, etc.' },
  { value: 'audio_transcription', label: 'Speech to Text', description: 'Whisper, etc.' },
  { value: 'audio_speech', label: 'Text to Speech', description: 'TTS models' },
  { value: 'rerank', label: 'Rerank', description: 'Reranking models' },
  { value: 'moderation', label: 'Moderation', description: 'Content moderation' },
];

const MODEL_TYPE_COLORS: Record<string, string> = {
  chat: 'bg-blue-100 text-blue-800',
  embedding: 'bg-green-100 text-green-800',
  image_generation: 'bg-purple-100 text-purple-800',
  audio_transcription: 'bg-yellow-100 text-yellow-800',
  audio_speech: 'bg-pink-100 text-pink-800',
  rerank: 'bg-orange-100 text-orange-800',
  moderation: 'bg-red-100 text-red-800',
};

type DeploymentMode = 'linked' | 'standalone';

// Pricing conversion helpers: UI uses per-million, backend uses per-token
const MILLION = 1_000_000;

// Convert per-token (backend) to per-million (UI display)
const toPerMillion = (perToken: string | undefined): string => {
  if (!perToken) return '';
  const value = parseFloat(perToken);
  if (isNaN(value)) return '';
  // Remove trailing zeros and unnecessary decimals
  return (value * MILLION).toString();
};

// Convert per-million (UI input) to per-token (backend storage)
// Returns undefined for empty/invalid inputs so fields are omitted from API requests
const toPerToken = (perMillion: string | undefined): string | undefined => {
  if (!perMillion || perMillion.trim() === '') return undefined;
  const value = parseFloat(perMillion);
  if (isNaN(value)) return undefined;
  // Use enough precision for very small numbers
  return (value / MILLION).toFixed(12).replace(/\.?0+$/, '');
};

export function Models() {
  const navigate = useNavigate();
  const { data: deploymentsData, isLoading } = useDeployments();
  const { data: providersData } = useProviders();
  const createDeployment = useCreateDeployment();
  const updateDeployment = useUpdateDeployment();
  const deleteDeployment = useDeleteDeployment();
  const enableDeployment = useEnableDeployment();
  const disableDeployment = useDisableDeployment();

  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingDeployment, setEditingDeployment] = useState<ModelDeployment | null>(null);
  const [deletingDeployment, setDeletingDeployment] = useState<ModelDeployment | null>(null);
  const [showApiKey, setShowApiKey] = useState(false);
  const [pricingData, setPricingData] = useState<PricingCreateRequest | null>(null);
  const [existingPricingSource, setExistingPricingSource] = useState<string | null>(null);
  const setDeploymentPricing = useSetDeploymentPricing();

  // Form state
  const [deploymentMode, setDeploymentMode] = useState<DeploymentMode>('linked');
  const [formData, setFormData] = useState<CreateDeploymentRequest>({
    model_name: '',
    provider_model: '',
    provider_config_id: '',
    provider_type: '',
    model_type: 'chat',
    api_key: '',
    api_base: '',
    is_active: true,
    priority: 1,
    tpm_limit: undefined,
    rpm_limit: undefined,
    timeout: undefined,
    settings: {},
  });
  const [formError, setFormError] = useState<string | null>(null);

  const deployments = deploymentsData?.items || [];
  const providers = providersData?.items || [];

  const filteredDeployments = useMemo(() => {
    let result = deployments;

    // Filter by type
    if (typeFilter) {
      result = result.filter((d) => d.model_type === typeFilter);
    }

    // Filter by search query
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (d) =>
          d.model_name.toLowerCase().includes(query) ||
          d.provider_model.toLowerCase().includes(query) ||
          (d.provider_name?.toLowerCase().includes(query) ?? false) ||
          (d.provider_type?.toLowerCase().includes(query) ?? false)
      );
    }

    return result;
  }, [deployments, searchQuery, typeFilter]);

  const resetForm = () => {
    setDeploymentMode('linked');
    setFormData({
      model_name: '',
      provider_model: '',
      provider_config_id: '',
      provider_type: '',
      model_type: 'chat',
      api_key: '',
      api_base: '',
      is_active: true,
      priority: 1,
      tpm_limit: undefined,
      rpm_limit: undefined,
      timeout: undefined,
      settings: {},
    });
    setFormError(null);
    setShowApiKey(false);
    setEditingDeployment(null);
    setPricingData(null);
    setExistingPricingSource(null);
  };

  const openCreateModal = () => {
    resetForm();
    setIsModalOpen(true);
  };

  const openEditModal = async (deployment: ModelDeployment) => {
    setEditingDeployment(deployment);
    // Determine mode based on deployment data
    const isStandalone = !deployment.provider_config_id;
    setDeploymentMode(isStandalone ? 'standalone' : 'linked');

    setFormData({
      model_name: deployment.model_name,
      provider_model: deployment.provider_model,
      provider_config_id: deployment.provider_config_id || '',
      provider_type: deployment.provider_type || '',
      model_type: deployment.model_type || 'chat',
      api_key: '', // Don't populate API key for security
      api_base: deployment.api_base || '',
      is_active: deployment.is_active,
      priority: deployment.priority,
      tpm_limit: deployment.tpm_limit,
      rpm_limit: deployment.rpm_limit,
      timeout: deployment.timeout,
      settings: deployment.settings,
    });
    setFormError(null);

    // Fetch existing pricing by deployment ID
    try {
      const { api } = await import('@/services/api');
      const pricing = await api.getDeploymentPricing(deployment.id);
      if (pricing && pricing.source === 'custom') {
        const mode = (deployment.model_type || 'chat') as PricingCreateRequest['mode'];
        // Convert per-token (backend) to per-million (UI)
        setPricingData({
          mode,
          input_cost_per_token: toPerMillion(pricing.input_cost_per_token),
          output_cost_per_token: toPerMillion(pricing.output_cost_per_token),
          cache_creation_input_token_cost: toPerMillion(pricing.cache_creation_input_token_cost),
          cache_read_input_token_cost: toPerMillion(pricing.cache_read_input_token_cost),
          image_cost_per_image: pricing.image_cost_per_image, // Not per-token, keep as-is
          audio_cost_per_character: pricing.audio_cost_per_character, // Keep as-is (per 1K chars handled in UI)
          audio_cost_per_minute: pricing.audio_cost_per_minute, // Keep as-is
          rerank_cost_per_search: pricing.rerank_cost_per_search, // Keep as-is
          max_tokens: pricing.max_tokens,
          max_input_tokens: pricing.max_input_tokens,
          max_output_tokens: pricing.max_output_tokens,
        });
        setExistingPricingSource('custom');
      } else {
        setPricingData(null);
        setExistingPricingSource(pricing?.source || null);
      }
    } catch {
      // Pricing not found, use defaults
      setPricingData(null);
      setExistingPricingSource(null);
    }

    setIsModalOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    // Validate based on mode
    if (deploymentMode === 'linked') {
      if (!formData.provider_config_id) {
        setFormError('Please select a provider configuration');
        return;
      }
    } else {
      if (!formData.provider_type) {
        setFormError('Please select a provider type');
        return;
      }
      // Only require API key for new standalone deployments
      if (!editingDeployment && !formData.api_key) {
        setFormError('API key is required for standalone deployments');
        return;
      }
    }

    try {
      const dataToSubmit = { ...formData };
      
      // Clean up fields based on mode
      if (deploymentMode === 'linked') {
        // Clear standalone fields
        dataToSubmit.provider_type = undefined;
        dataToSubmit.api_key = undefined;
        dataToSubmit.api_base = undefined;
      } else {
        // Clear linked field
        dataToSubmit.provider_config_id = null;
        // Don't send empty API key on edit (keep existing)
        if (editingDeployment && !dataToSubmit.api_key) {
          dataToSubmit.api_key = undefined;
        }
      }

      let deploymentId: string;

      if (editingDeployment) {
        await updateDeployment.mutateAsync({
          id: editingDeployment.id,
          data: dataToSubmit,
        });
        deploymentId = editingDeployment.id;
      } else {
        const newDeployment = await createDeployment.mutateAsync(dataToSubmit);
        deploymentId = newDeployment.id;
      }

      // Save pricing if configured
      if (pricingData && deploymentId) {
        try {
          // Convert per-million (UI) to per-token (backend)
          // Build object excluding undefined values to avoid sending empty strings
          const pricingForApi: PricingCreateRequest = {
            mode: pricingData.mode,
            input_cost_per_token: toPerToken(pricingData.input_cost_per_token),
            output_cost_per_token: toPerToken(pricingData.output_cost_per_token),
          };
          
          // Only add optional fields if they have values
          const cacheCreation = toPerToken(pricingData.cache_creation_input_token_cost);
          if (cacheCreation !== undefined) pricingForApi.cache_creation_input_token_cost = cacheCreation;
          
          const cacheRead = toPerToken(pricingData.cache_read_input_token_cost);
          if (cacheRead !== undefined) pricingForApi.cache_read_input_token_cost = cacheRead;
          
          if (pricingData.image_cost_per_image) pricingForApi.image_cost_per_image = pricingData.image_cost_per_image;
          if (pricingData.audio_cost_per_character) pricingForApi.audio_cost_per_character = pricingData.audio_cost_per_character;
          if (pricingData.audio_cost_per_minute) pricingForApi.audio_cost_per_minute = pricingData.audio_cost_per_minute;
          if (pricingData.rerank_cost_per_search) pricingForApi.rerank_cost_per_search = pricingData.rerank_cost_per_search;
          if (pricingData.max_tokens) pricingForApi.max_tokens = pricingData.max_tokens;
          if (pricingData.max_input_tokens) pricingForApi.max_input_tokens = pricingData.max_input_tokens;
          if (pricingData.max_output_tokens) pricingForApi.max_output_tokens = pricingData.max_output_tokens;
          
          await setDeploymentPricing.mutateAsync({
            deploymentId,
            data: pricingForApi,
          });
        } catch (pricingError) {
          console.error('Failed to save pricing:', pricingError);
          // Don't fail the whole operation if pricing fails
        }
      }

      setIsModalOpen(false);
      resetForm();
    } catch (error: any) {
      setFormError(error.response?.data?.detail || 'Failed to save deployment');
    }
  };

  const handleDelete = async () => {
    if (!deletingDeployment) return;
    try {
      await deleteDeployment.mutateAsync(deletingDeployment.id);
      setDeletingDeployment(null);
    } catch (error) {
      console.error('Failed to delete deployment:', error);
    }
  };

  const toggleStatus = async (deployment: ModelDeployment) => {
    try {
      if (deployment.is_active) {
        await disableDeployment.mutateAsync(deployment.id);
      } else {
        await enableDeployment.mutateAsync(deployment.id);
      }
    } catch (error) {
      console.error('Failed to toggle status:', error);
    }
  };

  const getProviderDisplay = (deployment: ModelDeployment) => {
    if (deployment.provider_config_id && deployment.provider_name) {
      return { name: deployment.provider_name, type: 'linked' as const };
    }
    if (deployment.provider_type) {
      return { 
        name: PROVIDER_OPTIONS.find(p => p.value === deployment.provider_type)?.label || deployment.provider_type,
        type: 'standalone' as const 
      };
    }
    return { name: 'Unknown', type: 'linked' as const };
  };

  const columns = [
    {
      key: 'model',
      header: 'Model',
      render: (deployment: ModelDeployment) => {
        const provider = getProviderDisplay(deployment);
        const colorClass = PROVIDER_COLORS[deployment.provider_type || provider.name.toLowerCase()] || 'bg-gray-100 text-gray-800';
        const typeColorClass = MODEL_TYPE_COLORS[deployment.model_type] || 'bg-gray-100 text-gray-800';
        const typeLabel = MODEL_TYPE_OPTIONS.find(o => o.value === deployment.model_type)?.label || deployment.model_type;
        return (
          <div className="flex items-center">
            <div className="w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center mr-3">
              <Box className="w-5 h-5 text-gray-600" />
            </div>
            <div>
              <p className="font-medium text-gray-900">{deployment.model_name}</p>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorClass}`}>
                  {provider.name}
                </span>
                <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${typeColorClass}`}>
                  {typeLabel}
                </span>
                {provider.type === 'standalone' && (
                  <span className="inline-flex items-center text-xs text-amber-600">
                    <Key className="w-3 h-3 mr-1" />
                    Standalone
                  </span>
                )}
              </div>
            </div>
          </div>
        );
      },
    },
    {
      key: 'provider_model',
      header: 'Provider Model',
      render: (deployment: ModelDeployment) => (
        <span className="text-sm text-gray-600 font-mono">{deployment.provider_model}</span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (deployment: ModelDeployment) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            toggleStatus(deployment);
          }}
          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
            deployment.is_active
              ? 'bg-green-100 text-green-800'
              : 'bg-gray-100 text-gray-600'
          }`}
        >
          {deployment.is_active ? (
            <>
              <Power className="w-3 h-3 mr-1" />
              Active
            </>
          ) : (
            <>
              <PowerOff className="w-3 h-3 mr-1" />
              Disabled
            </>
          )}
        </button>
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
          {deployment.tpm_limit || deployment.rpm_limit ? (
            <div className="space-y-0.5">
              {deployment.tpm_limit && <div>TPM: {deployment.tpm_limit.toLocaleString()}</div>}
              {deployment.rpm_limit && <div>RPM: {deployment.rpm_limit.toLocaleString()}</div>}
            </div>
          ) : (
            <span className="text-gray-400">-</span>
          )}
        </div>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (deployment: ModelDeployment) => (
        <div className="flex items-center space-x-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              openEditModal(deployment);
            }}
            className="p-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
            title="Edit"
          >
            <Edit2 className="w-4 h-4" />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setDeletingDeployment(deployment);
            }}
            className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
            title="Delete"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
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
          <h1 className="text-2xl font-bold text-gray-900">Models</h1>
          <p className="text-gray-600 mt-1">
            Manage LLM model endpoints and their configurations
          </p>
        </div>
        <button
          onClick={openCreateModal}
          className="flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium"
        >
          <Plus className="w-4 h-4 mr-2" />
          Add Model
        </button>
      </div>

      {/* Search and Filter */}
      <div className="mb-6 flex items-center gap-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search models..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none bg-white"
        >
          <option value="">All Types</option>
          {MODEL_TYPE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Deployments Table */}
      <DataTable
        columns={columns}
        data={filteredDeployments}
        keyExtractor={(deployment) => deployment.id}
        onRowClick={(deployment) => navigate(`/models/${deployment.id}`)}
      />

      {/* Create/Edit Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title={editingDeployment ? 'Edit Model' : 'Add Model'}
        size="lg"
      >
        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Error Message */}
          {formError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-center text-red-700 text-sm">
              <AlertCircle className="w-4 h-4 mr-2 flex-shrink-0" />
              {formError}
            </div>
          )}

          {/* Deployment Mode Selection */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Deployment Mode
            </label>
            <div className="grid grid-cols-2 gap-4">
              <button
                type="button"
                onClick={() => setDeploymentMode('linked')}
                className={`flex items-center p-4 border rounded-lg transition-colors ${
                  deploymentMode === 'linked'
                    ? 'border-primary-500 bg-primary-50'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <Link2 className={`w-5 h-5 mr-3 ${deploymentMode === 'linked' ? 'text-primary-600' : 'text-gray-400'}`} />
                <div className="text-left">
                  <p className={`font-medium ${deploymentMode === 'linked' ? 'text-primary-900' : 'text-gray-900'}`}>
                    Use Existing Provider
                  </p>
                  <p className="text-xs text-gray-500">
                    Link to a configured provider
                  </p>
                </div>
              </button>
              <button
                type="button"
                onClick={() => setDeploymentMode('standalone')}
                className={`flex items-center p-4 border rounded-lg transition-colors ${
                  deploymentMode === 'standalone'
                    ? 'border-primary-500 bg-primary-50'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <Key className={`w-5 h-5 mr-3 ${deploymentMode === 'standalone' ? 'text-primary-600' : 'text-gray-400'}`} />
                <div className="text-left">
                  <p className={`font-medium ${deploymentMode === 'standalone' ? 'text-primary-900' : 'text-gray-900'}`}>
                    Standalone (API Key)
                  </p>
                  <p className="text-xs text-gray-500">
                    Configure with direct API key
                  </p>
                </div>
              </button>
            </div>
          </div>

          {/* Model Configuration */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Public Model Name *
              </label>
              <input
                type="text"
                value={formData.model_name}
                onChange={(e) => setFormData({ ...formData, model_name: e.target.value })}
                placeholder="e.g., gpt-4o"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                required
              />
              <p className="text-xs text-gray-500 mt-1">
                Name users will see and use in API calls
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Provider Model ID *
              </label>
              <input
                type="text"
                value={formData.provider_model}
                onChange={(e) => setFormData({ ...formData, provider_model: e.target.value })}
                placeholder="e.g., gpt-4o-2024-08-06"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                required
              />
              <p className="text-xs text-gray-500 mt-1">
                Actual model identifier at the provider
              </p>
            </div>
          </div>

          {/* Model Type */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Model Type *
            </label>
            <select
              value={formData.model_type}
              onChange={(e) => setFormData({ ...formData, model_type: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              required
            >
              {MODEL_TYPE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-gray-500 mt-1">
              {MODEL_TYPE_OPTIONS.find(o => o.value === formData.model_type)?.description || 'Select the type of model'}
            </p>
          </div>

          {/* Provider Configuration - Linked Mode */}
          {deploymentMode === 'linked' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Provider Configuration *
              </label>
              <select
                value={formData.provider_config_id || ''}
                onChange={(e) => setFormData({ ...formData, provider_config_id: e.target.value })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                required={deploymentMode === 'linked'}
              >
                <option value="">Select a provider...</option>
                {providers.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.name} ({provider.provider_type})
                  </option>
                ))}
              </select>
              <p className="text-xs text-gray-500 mt-1">
                Uses the API key and settings from the selected provider
              </p>
            </div>
          )}

          {/* Provider Configuration - Standalone Mode */}
          {deploymentMode === 'standalone' && (
            <div className="space-y-4 border-t border-gray-200 pt-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Provider Type *
                </label>
                <select
                  value={formData.provider_type}
                  onChange={(e) => setFormData({ ...formData, provider_type: e.target.value })}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                  required={deploymentMode === 'standalone'}
                >
                  <option value="">Select provider type...</option>
                  {PROVIDER_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  API Key {!editingDeployment && '*'}
                </label>
                <div className="relative">
                  <input
                    type={showApiKey ? 'text' : 'password'}
                    value={formData.api_key}
                    onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
                    placeholder={editingDeployment ? 'Leave blank to keep existing' : 'sk-...'}
                    className="w-full px-4 py-2 pr-10 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                    required={deploymentMode === 'standalone' && !editingDeployment}
                  />
                  <button
                    type="button"
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                  >
                    {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  {editingDeployment 
                    ? 'Leave blank to keep the existing API key' 
                    : 'Your API key will be encrypted at rest'}
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  API Base URL (Optional)
                </label>
                <input
                  type="text"
                  value={formData.api_base}
                  onChange={(e) => setFormData({ ...formData, api_base: e.target.value })}
                  placeholder="https://api.example.com/v1"
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Custom endpoint URL (defaults to provider's standard URL)
                </p>
              </div>
            </div>
          )}

          {/* Advanced Settings */}
          <div className="border-t border-gray-200 pt-4">
            <h4 className="text-sm font-medium text-gray-700 mb-3">Advanced Settings</h4>
            <div className="grid grid-cols-3 gap-4">
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
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                />
                <p className="text-xs text-gray-500 mt-1">Higher = preferred</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  TPM Limit
                </label>
                <input
                  type="number"
                  min={0}
                  value={formData.tpm_limit || ''}
                  onChange={(e) => setFormData({ ...formData, tpm_limit: e.target.value ? parseInt(e.target.value) : undefined })}
                  placeholder="Unlimited"
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                />
                <p className="text-xs text-gray-500 mt-1">Tokens per minute</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  RPM Limit
                </label>
                <input
                  type="number"
                  min={0}
                  value={formData.rpm_limit || ''}
                  onChange={(e) => setFormData({ ...formData, rpm_limit: e.target.value ? parseInt(e.target.value) : undefined })}
                  placeholder="Unlimited"
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                />
                <p className="text-xs text-gray-500 mt-1">Requests per minute</p>
              </div>
            </div>
          </div>

          {/* Pricing Configuration */}
          <PricingSection
            modelType={formData.model_type || 'chat'}
            pricingData={pricingData}
            onChange={setPricingData}
            hasCustomPricing={existingPricingSource === 'custom'}
          />

          {/* Active Toggle */}
          <div className="flex items-center">
            <input
              type="checkbox"
              id="is_active"
              checked={formData.is_active}
              onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
            />
            <label htmlFor="is_active" className="ml-2 text-sm text-gray-700">
              Active (available for routing)
            </label>
          </div>

          {/* Actions */}
          <div className="flex justify-end space-x-3 pt-4 border-t border-gray-200">
            <button
              type="button"
              onClick={() => setIsModalOpen(false)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createDeployment.isPending || updateDeployment.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {createDeployment.isPending || updateDeployment.isPending
                ? 'Saving...'
                : editingDeployment
                ? 'Save Changes'
                : 'Add Model'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={!!deletingDeployment}
        onClose={() => setDeletingDeployment(null)}
        title="Delete Model"
      >
        <div className="space-y-4">
          <p className="text-gray-600">
            Are you sure you want to delete <strong>{deletingDeployment?.model_name}</strong>?
          </p>
          <p className="text-sm text-gray-500">
            This action cannot be undone. The model deployment will be permanently removed.
          </p>
          <div className="flex justify-end space-x-3 pt-4">
            <button
              onClick={() => setDeletingDeployment(null)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleDelete}
              disabled={deleteDeployment.isPending}
              className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
            >
              {deleteDeployment.isPending ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
