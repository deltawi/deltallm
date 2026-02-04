import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Server,
  Key,
  Link2,
  Power,
  PowerOff,
  Edit2,
  Trash2,
  Clock,
  Gauge,
  Settings,
  AlertCircle,
  CheckCircle,
  Eye,
  EyeOff,
  DollarSign,
  Tag,
} from 'lucide-react';
import { useDeployment, useUpdateDeployment, useDeleteDeployment, useEnableDeployment, useDisableDeployment } from '@/hooks/useDeployments';
import { useProviders } from '@/hooks/useProviders';
import { useDeploymentPricing, useSetDeploymentPricing } from '@/hooks/usePricing';
import { Modal } from '@/components/Modal';
import { PricingSection } from '@/components/PricingSection';
import type { UpdateDeploymentRequest, PricingCreateRequest } from '@/types';

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

const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  azure: 'Azure OpenAI',
  bedrock: 'AWS Bedrock',
  gemini: 'Google Gemini',
  cohere: 'Cohere',
  mistral: 'Mistral AI',
  groq: 'Groq',
  ollama: 'Ollama',
  vllm: 'vLLM',
};

// Format per-token price as per-million for display
const formatPricePerMillion = (perToken: string | undefined): string => {
  if (!perToken) return '';
  const value = parseFloat(perToken);
  if (isNaN(value)) return perToken;
  const perMillion = value * 1_000_000;
  // Format nicely: remove unnecessary trailing zeros
  return perMillion.toFixed(2).replace(/\.?0+$/, '');
};

const MILLION = 1_000_000;

// Convert per-token (backend) to per-million (UI display)
const toPerMillion = (perToken: string | undefined): string => {
  if (!perToken) return '';
  const value = parseFloat(perToken);
  if (isNaN(value)) return '';
  return (value * MILLION).toString();
};

// Convert per-million (UI input) to per-token (backend storage)
// Returns undefined for empty/invalid inputs so fields are omitted from API requests
const toPerToken = (perMillion: string | undefined): string | undefined => {
  if (!perMillion || perMillion.trim() === '') return undefined;
  const value = parseFloat(perMillion);
  if (isNaN(value)) return undefined;
  return (value / MILLION).toFixed(12).replace(/\.?0+$/, '');
};

export function ModelDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: deployment, isLoading, error } = useDeployment(id || '');
  const { data: providersData } = useProviders();
  const { data: pricing, isLoading: isPricingLoading } = useDeploymentPricing(id);
  const updateDeployment = useUpdateDeployment();
  const deleteDeployment = useDeleteDeployment();
  const enableDeployment = useEnableDeployment();
  const disableDeployment = useDisableDeployment();

  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [updateError, setUpdateError] = useState<string | null>(null);

  // Edit form state
  const [editForm, setEditForm] = useState<UpdateDeploymentRequest>({});

  // Pricing edit state
  const setDeploymentPricing = useSetDeploymentPricing();
  const [pricingData, setPricingData] = useState<PricingCreateRequest | null>(null);
  const [existingPricingSource, setExistingPricingSource] = useState<string | null>(null);

  const providers = providersData?.items || [];

  const isStandalone = !deployment?.provider_config_id;

  const openEditModal = () => {
    if (!deployment) return;
    setEditForm({
      model_name: deployment.model_name,
      provider_model: deployment.provider_model,
      provider_config_id: deployment.provider_config_id,
      provider_type: deployment.provider_type || '',
      api_base: deployment.api_base || '',
      is_active: deployment.is_active,
      priority: deployment.priority,
      tpm_limit: deployment.tpm_limit,
      rpm_limit: deployment.rpm_limit,
      timeout: deployment.timeout,
      settings: deployment.settings,
    });
    setUpdateError(null);

    // Populate pricing from already-fetched pricing data
    if (pricing && pricing.source === 'custom') {
      const mode = (deployment.model_type || 'chat') as PricingCreateRequest['mode'];
      setPricingData({
        mode,
        input_cost_per_token: toPerMillion(pricing.input_cost_per_token),
        output_cost_per_token: toPerMillion(pricing.output_cost_per_token),
        cache_creation_input_token_cost: toPerMillion(pricing.cache_creation_input_token_cost),
        cache_read_input_token_cost: toPerMillion(pricing.cache_read_input_token_cost),
        image_cost_per_image: pricing.image_cost_per_image,
        audio_cost_per_character: pricing.audio_cost_per_character,
        audio_cost_per_minute: pricing.audio_cost_per_minute,
        rerank_cost_per_search: pricing.rerank_cost_per_search,
        max_tokens: pricing.max_tokens,
        max_input_tokens: pricing.max_input_tokens,
        max_output_tokens: pricing.max_output_tokens,
      });
      setExistingPricingSource('custom');
    } else {
      setPricingData(null);
      setExistingPricingSource(pricing?.source || null);
    }

    setIsEditModalOpen(true);
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id || !deployment) return;

    try {
      await updateDeployment.mutateAsync({ id, data: editForm });

      // Save pricing if configured
      if (pricingData && id) {
        try {
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
            deploymentId: id,
            data: pricingForApi,
          });
        } catch (pricingError) {
          console.error('Failed to save pricing:', pricingError);
        }
      }

      setIsEditModalOpen(false);
    } catch (err: any) {
      setUpdateError(err.response?.data?.detail || 'Failed to update deployment');
    }
  };

  const handleDelete = async () => {
    if (!id) return;
    try {
      await deleteDeployment.mutateAsync(id);
      navigate('/models');
    } catch (err) {
      console.error('Failed to delete deployment:', err);
    }
  };

  const toggleStatus = async () => {
    if (!id || !deployment) return;
    try {
      if (deployment.is_active) {
        await disableDeployment.mutateAsync(id);
      } else {
        await enableDeployment.mutateAsync(id);
      }
    } catch (err) {
      console.error('Failed to toggle status:', err);
    }
  };

  if (isLoading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (error || !deployment) {
    return (
      <div className="p-8">
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center">
          <AlertCircle className="w-5 h-5 mr-2" />
          Failed to load model details. The deployment may have been deleted.
        </div>
        <button
          onClick={() => navigate('/models')}
          className="mt-4 flex items-center text-primary-600 hover:text-primary-700"
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back to Models
        </button>
      </div>
    );
  }

  const providerType = deployment.provider_type || '';
  const providerColor = PROVIDER_COLORS[providerType.toLowerCase()] || 'bg-gray-100 text-gray-800';
  const providerLabel = PROVIDER_LABELS[providerType.toLowerCase()] || providerType || 'Unknown';

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={() => navigate('/models')}
          className="flex items-center text-gray-500 hover:text-gray-700 mb-4"
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back to Models
        </button>

        <div className="flex items-start justify-between">
          <div className="flex items-center">
            <div className="w-16 h-16 rounded-xl bg-gray-100 flex items-center justify-center mr-4">
              <Server className="w-8 h-8 text-gray-600" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{deployment.model_name}</h1>
              <div className="flex items-center gap-2 mt-1">
                <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${providerColor}`}>
                  {providerLabel}
                </span>
                {isStandalone ? (
                  <span className="inline-flex items-center text-xs text-amber-600">
                    <Key className="w-3 h-3 mr-1" />
                    Standalone
                  </span>
                ) : (
                  <span className="inline-flex items-center text-xs text-blue-600">
                    <Link2 className="w-3 h-3 mr-1" />
                    Linked to {deployment.provider_name}
                  </span>
                )}
                <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                  deployment.is_active
                    ? 'bg-green-100 text-green-800'
                    : 'bg-gray-100 text-gray-600'
                }`}>
                  {deployment.is_active ? (
                    <>
                      <CheckCircle className="w-3 h-3 mr-1" />
                      Active
                    </>
                  ) : (
                    <>
                      <PowerOff className="w-3 h-3 mr-1" />
                      Disabled
                    </>
                  )}
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <button
              onClick={toggleStatus}
              className={`flex items-center px-4 py-2 rounded-lg font-medium transition-colors ${
                deployment.is_active
                  ? 'bg-amber-100 text-amber-700 hover:bg-amber-200'
                  : 'bg-green-100 text-green-700 hover:bg-green-200'
              }`}
            >
              {deployment.is_active ? (
                <>
                  <PowerOff className="w-4 h-4 mr-2" />
                  Disable
                </>
              ) : (
                <>
                  <Power className="w-4 h-4 mr-2" />
                  Enable
                </>
              )}
            </button>
            <button
              onClick={openEditModal}
              className="flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium"
            >
              <Edit2 className="w-4 h-4 mr-2" />
              Edit
            </button>
            <button
              onClick={() => setIsDeleteModalOpen(true)}
              className="flex items-center px-4 py-2 bg-red-100 text-red-700 rounded-lg hover:bg-red-200 transition-colors font-medium"
            >
              <Trash2 className="w-4 h-4 mr-2" />
              Delete
            </button>
          </div>
        </div>
      </div>

      {/* Details Grid */}
      <div className="grid grid-cols-3 gap-6">
        {/* Configuration Card */}
        <div className="col-span-2 bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
            <Settings className="w-5 h-5 mr-2 text-gray-500" />
            Configuration
          </h2>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium text-gray-500">Public Model Name</label>
                <p className="text-gray-900 font-mono">{deployment.model_name}</p>
              </div>
              <div>
                <label className="text-sm font-medium text-gray-500">Provider Model ID</label>
                <p className="text-gray-900 font-mono">{deployment.provider_model}</p>
              </div>
            </div>

            <div className="border-t border-gray-100 pt-4">
              {isStandalone ? (
                <div className="space-y-3">
                  <div className="flex items-center text-amber-700">
                    <Key className="w-4 h-4 mr-2" />
                    <span className="font-medium">Standalone Deployment</span>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="text-sm font-medium text-gray-500">Provider Type</label>
                      <p className="text-gray-900">{providerLabel}</p>
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-500">API Key</label>
                      <p className="text-gray-900 font-mono text-sm">••••••••••••</p>
                    </div>
                  </div>
                  {deployment.api_base && (
                    <div>
                      <label className="text-sm font-medium text-gray-500">API Base URL</label>
                      <p className="text-gray-900 font-mono text-sm">{deployment.api_base}</p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="flex items-center text-blue-700">
                    <Link2 className="w-4 h-4 mr-2" />
                    <span className="font-medium">Linked Provider Configuration</span>
                  </div>
                  <div>
                    <label className="text-sm font-medium text-gray-500">Provider Name</label>
                    <p className="text-gray-900">{deployment.provider_name}</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Stats & Limits Card */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
            <Gauge className="w-5 h-5 mr-2 text-gray-500" />
            Routing & Limits
          </h2>

          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-gray-500">Priority</label>
              <div className="flex items-center mt-1">
                <div className="flex-1 bg-gray-100 rounded-full h-2 mr-2">
                  <div
                    className="bg-primary-600 h-2 rounded-full"
                    style={{ width: `${(deployment.priority / 100) * 100}%` }}
                  />
                </div>
                <span className="text-gray-900 font-medium">{deployment.priority}</span>
              </div>
              <p className="text-xs text-gray-500 mt-1">Higher priority = preferred for routing</p>
            </div>

            <div className="border-t border-gray-100 pt-4">
              <label className="text-sm font-medium text-gray-500">Rate Limits</label>
              <div className="grid grid-cols-2 gap-4 mt-2">
                <div className="bg-gray-50 rounded-lg p-3">
                  <p className="text-xs text-gray-500">TPM Limit</p>
                  <p className="text-lg font-semibold text-gray-900">
                    {deployment.tpm_limit?.toLocaleString() || '∞'}
                  </p>
                  <p className="text-xs text-gray-400">tokens/min</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-3">
                  <p className="text-xs text-gray-500">RPM Limit</p>
                  <p className="text-lg font-semibold text-gray-900">
                    {deployment.rpm_limit?.toLocaleString() || '∞'}
                  </p>
                  <p className="text-xs text-gray-400">requests/min</p>
                </div>
              </div>
            </div>

            {deployment.timeout && (
              <div className="border-t border-gray-100 pt-4">
                <label className="text-sm font-medium text-gray-500">Timeout</label>
                <p className="text-gray-900">{deployment.timeout}s</p>
              </div>
            )}
          </div>
        </div>

        {/* Pricing Card */}
        <div className="col-span-3 bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
            <DollarSign className="w-5 h-5 mr-2 text-gray-500" />
            Pricing
            {pricing?.source === 'custom' && (
              <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">
                <Tag className="w-3 h-3 mr-1" />
                Custom
              </span>
            )}
            {pricing?.source === 'default' && (
              <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">
                Default
              </span>
            )}
          </h2>

          {isPricingLoading ? (
            <div className="flex items-center justify-center py-4">
              <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-primary-600"></div>
            </div>
          ) : pricing ? (
            <div className="grid grid-cols-4 gap-4 text-sm">
              <div>
                <label className="text-gray-500">Mode</label>
                <p className="text-gray-900 capitalize">{pricing.mode}</p>
              </div>
              {pricing.input_cost_per_token && (
                <div>
                  <label className="text-gray-500">Input Cost</label>
                  <p className="text-gray-900 font-mono">${formatPricePerMillion(pricing.input_cost_per_token)}/1M tokens</p>
                </div>
              )}
              {pricing.output_cost_per_token && (
                <div>
                  <label className="text-gray-500">Output Cost</label>
                  <p className="text-gray-900 font-mono">${formatPricePerMillion(pricing.output_cost_per_token)}/1M tokens</p>
                </div>
              )}
              {pricing.image_cost_per_image && (
                <div>
                  <label className="text-gray-500">Image Cost</label>
                  <p className="text-gray-900 font-mono">${pricing.image_cost_per_image}/image</p>
                </div>
              )}
              {pricing.audio_cost_per_minute && (
                <div>
                  <label className="text-gray-500">Audio Cost</label>
                  <p className="text-gray-900 font-mono">${pricing.audio_cost_per_minute}/min</p>
                </div>
              )}
              {pricing.audio_cost_per_character && (
                <div>
                  <label className="text-gray-500">TTS Cost</label>
                  <p className="text-gray-900 font-mono">${pricing.audio_cost_per_character}/1K chars</p>
                </div>
              )}
              {pricing.rerank_cost_per_search && (
                <div>
                  <label className="text-gray-500">Rerank Cost</label>
                  <p className="text-gray-900 font-mono">${pricing.rerank_cost_per_search}/search</p>
                </div>
              )}
              {(pricing.max_tokens || pricing.max_input_tokens || pricing.max_output_tokens) && (
                <>
                  {pricing.max_tokens && (
                    <div>
                      <label className="text-gray-500">Max Tokens</label>
                      <p className="text-gray-900">{pricing.max_tokens.toLocaleString()}</p>
                    </div>
                  )}
                  {pricing.max_input_tokens && (
                    <div>
                      <label className="text-gray-500">Max Input</label>
                      <p className="text-gray-900">{pricing.max_input_tokens.toLocaleString()}</p>
                    </div>
                  )}
                  {pricing.max_output_tokens && (
                    <div>
                      <label className="text-gray-500">Max Output</label>
                      <p className="text-gray-900">{pricing.max_output_tokens.toLocaleString()}</p>
                    </div>
                  )}
                </>
              )}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No pricing information available</p>
          )}
        </div>

        {/* Metadata Card */}
        <div className="col-span-3 bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
            <Clock className="w-5 h-5 mr-2 text-gray-500" />
            Metadata
          </h2>

          <div className="grid grid-cols-4 gap-4 text-sm">
            <div>
              <label className="text-gray-500">Deployment ID</label>
              <p className="font-mono text-gray-900">{deployment.id}</p>
            </div>
            <div>
              <label className="text-gray-500">Created</label>
              <p className="text-gray-900">
                {new Date(deployment.created_at).toLocaleString()}
              </p>
            </div>
            <div>
              <label className="text-gray-500">Last Updated</label>
              <p className="text-gray-900">
                {deployment.updated_at
                  ? new Date(deployment.updated_at).toLocaleString()
                  : 'Never'}
              </p>
            </div>
            <div>
              <label className="text-gray-500">Organization</label>
              <p className="text-gray-900">
                {deployment.org_id || 'Global (all organizations)'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Edit Modal */}
      <Modal
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        title="Edit Model"
        size="lg"
      >
        <form onSubmit={handleUpdate} className="space-y-4">
          {updateError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-center text-red-700 text-sm">
              <AlertCircle className="w-4 h-4 mr-2 flex-shrink-0" />
              {updateError}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Public Model Name
              </label>
              <input
                type="text"
                value={editForm.model_name || ''}
                onChange={(e) => setEditForm({ ...editForm, model_name: e.target.value })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Provider Model ID
              </label>
              <input
                type="text"
                value={editForm.provider_model || ''}
                onChange={(e) => setEditForm({ ...editForm, provider_model: e.target.value })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>
          </div>

          {isStandalone && (
            <div className="space-y-4 border-t border-gray-200 pt-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  New API Key (optional)
                </label>
                <div className="relative">
                  <input
                    type={showApiKey ? 'text' : 'password'}
                    value={editForm.api_key || ''}
                    onChange={(e) => setEditForm({ ...editForm, api_key: e.target.value })}
                    placeholder="Leave blank to keep existing"
                    className="w-full px-4 py-2 pr-10 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                  >
                    {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  API Base URL
                </label>
                <input
                  type="text"
                  value={editForm.api_base || ''}
                  onChange={(e) => setEditForm({ ...editForm, api_base: e.target.value })}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                />
              </div>
            </div>
          )}

          {!isStandalone && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Provider Configuration
              </label>
              <select
                value={editForm.provider_config_id || ''}
                onChange={(e) => setEditForm({ ...editForm, provider_config_id: e.target.value })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              >
                <option value="">Select a provider...</option>
                {providers.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.name} ({provider.provider_type})
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="grid grid-cols-3 gap-4 border-t border-gray-200 pt-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Priority
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={editForm.priority || 1}
                onChange={(e) => setEditForm({ ...editForm, priority: parseInt(e.target.value) || 1 })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                TPM Limit
              </label>
              <input
                type="number"
                min={0}
                value={editForm.tpm_limit || ''}
                onChange={(e) => setEditForm({ ...editForm, tpm_limit: e.target.value ? parseInt(e.target.value) : undefined })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                RPM Limit
              </label>
              <input
                type="number"
                min={0}
                value={editForm.rpm_limit || ''}
                onChange={(e) => setEditForm({ ...editForm, rpm_limit: e.target.value ? parseInt(e.target.value) : undefined })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>
          </div>

          {/* Pricing Configuration */}
          <PricingSection
            modelType={deployment?.model_type || 'chat'}
            pricingData={pricingData}
            onChange={setPricingData}
            hasCustomPricing={existingPricingSource === 'custom'}
          />

          <div className="flex items-center">
            <input
              type="checkbox"
              id="edit_is_active"
              checked={editForm.is_active}
              onChange={(e) => setEditForm({ ...editForm, is_active: e.target.checked })}
              className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
            />
            <label htmlFor="edit_is_active" className="ml-2 text-sm text-gray-700">
              Active
            </label>
          </div>

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={() => setIsEditModalOpen(false)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={updateDeployment.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {updateDeployment.isPending ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={isDeleteModalOpen}
        onClose={() => setIsDeleteModalOpen(false)}
        title="Delete Model"
      >
        <div className="space-y-4">
          <p className="text-gray-600">
            Are you sure you want to delete <strong>{deployment.model_name}</strong>?
          </p>
          <p className="text-sm text-gray-500">
            This action cannot be undone. The model deployment will be permanently removed.
          </p>
          <div className="flex justify-end space-x-3 pt-4">
            <button
              onClick={() => setIsDeleteModalOpen(false)}
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
