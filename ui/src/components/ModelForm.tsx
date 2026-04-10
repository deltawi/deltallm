import { useId, useState } from 'react';
import Card from './Card';
import { ChevronDown, Plus, X } from 'lucide-react';
import ProviderBadge from './ProviderBadge';
import { useApi } from '../lib/hooks';
import { models, namedCredentials, type NamedCredential, type ProviderModelDiscoveryPayload, type ProviderModelOption } from '../lib/api';
import {
  canonicalNamedCredentialProvider,
  DEFAULT_CUSTOM_AUTH_HEADER_FORMAT,
  DEFAULT_CUSTOM_AUTH_HEADER_NAME,
  providerDisplayName,
  supportsCustomUpstreamAuthProvider,
} from '../lib/providers';
import {
  EMPTY_FORM,
  MODE_OPTIONS,
  buildModelPayload,
  type ModelFormValues,
  type ModelMode,
  type ModelPayload,
} from './modelFormShared';

let collapsibleIdCounter = 0;

function CollapsibleCard({ title, defaultOpen = false, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  const [id] = useState(() => `collapsible-${++collapsibleIdCounter}`);
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={id}
        className="flex items-center justify-between w-full px-5 py-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset rounded-xl"
      >
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div id={id} role="region" aria-label={title} className="px-5 pb-5">{children}</div>}
    </div>
  );
}

function upstreamModelPlaceholder(mode: ModelMode): string {
  if (mode === 'image_generation') return 'gpt-image-1.5';
  if (mode === 'audio_speech') return 'gpt-4o-mini-tts';
  if (mode === 'audio_transcription') return 'gpt-4o-transcribe';
  if (mode === 'embedding') return 'text-embedding-3-large';
  if (mode === 'rerank') return 'rerank-english-v3.0';
  return 'gpt-5.4';
}

type MetadataAutofillField =
  | 'input_cost_per_token'
  | 'output_cost_per_token'
  | 'input_cost_per_token_cache_hit'
  | 'output_cost_per_token_cache_hit'
  | 'max_context_window'
  | 'max_input_tokens'
  | 'max_output_tokens'
  | 'output_vector_size'
  | 'input_cost_per_image'
  | 'input_cost_per_character'
  | 'output_cost_per_character'
  | 'input_cost_per_second'
  | 'output_cost_per_second'
  | 'input_cost_per_audio_token'
  | 'output_cost_per_audio_token'
  | 'batch_price_multiplier'
  | 'batch_input_cost_per_token'
  | 'batch_output_cost_per_token';

const MODEL_METADATA_FIELDS: Record<string, MetadataAutofillField> = {
  input_cost_per_token: 'input_cost_per_token',
  output_cost_per_token: 'output_cost_per_token',
  input_cost_per_token_cache_hit: 'input_cost_per_token_cache_hit',
  output_cost_per_token_cache_hit: 'output_cost_per_token_cache_hit',
  max_tokens: 'max_context_window',
  max_input_tokens: 'max_input_tokens',
  max_output_tokens: 'max_output_tokens',
  output_vector_size: 'output_vector_size',
  input_cost_per_image: 'input_cost_per_image',
  input_cost_per_character: 'input_cost_per_character',
  output_cost_per_character: 'output_cost_per_character',
  input_cost_per_second: 'input_cost_per_second',
  output_cost_per_second: 'output_cost_per_second',
  input_cost_per_audio_token: 'input_cost_per_audio_token',
  output_cost_per_audio_token: 'output_cost_per_audio_token',
  batch_price_multiplier: 'batch_price_multiplier',
  batch_input_cost_per_token: 'batch_input_cost_per_token',
  batch_output_cost_per_token: 'batch_output_cost_per_token',
};

function applyKnownMetadataToForm(
  current: ModelFormValues,
  metadata: ProviderModelOption['known_metadata'],
): ModelFormValues {
  if (!metadata) return current;

  let changed = false;
  const next = { ...current };
  for (const metadataKey of Object.keys(MODEL_METADATA_FIELDS) as Array<keyof typeof MODEL_METADATA_FIELDS>) {
    const formField = MODEL_METADATA_FIELDS[metadataKey];
    const rawValue = metadata[metadataKey];
    if (rawValue == null) continue;
    if (next[formField]) continue;
    next[formField] = String(rawValue);
    changed = true;
  }
  return changed ? next : current;
}

function findProviderModelOption(options: ProviderModelOption[], modelId: string): ProviderModelOption | null {
  const normalizedId = modelId.trim().toLowerCase();
  if (!normalizedId) return null;
  return options.find((option) => option.id.trim().toLowerCase() === normalizedId) || null;
}

function emptyDiscoveryResult(): { data: ProviderModelOption[]; warnings: string[] } {
  return { data: [], warnings: [] };
}

function buildLiveDiscoveryRequestKey(payload: ProviderModelDiscoveryPayload): string {
  return [
    payload.provider || '',
    payload.mode || '',
    payload.named_credential_id || '',
    payload.api_key || '',
    payload.api_base || '',
    payload.api_version || '',
    payload.auth_header_name || '',
    payload.auth_header_format || '',
  ].join('::');
}

interface ModelFormProps {
  initialValues?: ModelFormValues;
  initialDefaultParams?: { key: string; value: string }[];
  onSubmit: (payload: ModelPayload) => Promise<void>;
  onCancel: () => void;
  submitLabel?: string;
  saving?: boolean;
  error?: string | null;
}

const inputClass = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";
type RequiredField = 'model_name' | 'provider' | 'model' | 'api_base' | 'named_credential_id';

function inputClasses(hasError = false): string {
  return `${inputClass} ${hasError ? 'border-red-300 bg-red-50/40 focus:ring-red-500' : ''}`;
}

function FieldLabel({ label, required = false }: { label: string; required?: boolean }) {
  return (
    <label className="mb-1 block text-sm font-medium text-gray-700">
      {label}
      {required ? <span className="ml-1 text-red-500">*</span> : null}
    </label>
  );
}

export default function ModelForm({
  initialValues,
  initialDefaultParams,
  onSubmit,
  onCancel,
  submitLabel = 'Create',
  saving = false,
  error = null,
}: ModelFormProps) {
  const [form, setForm] = useState<ModelFormValues>(initialValues || { ...EMPTY_FORM });
  const [defaultParams, setDefaultParams] = useState<{ key: string; value: string }[]>(initialDefaultParams || []);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<RequiredField, string>>>({});
  const [liveDiscoveryState, setLiveDiscoveryState] = useState<{
    requestKey: string | null;
    data: ProviderModelOption[];
    warnings: string[];
    loading: boolean;
  }>({
    requestKey: null,
    data: [],
    warnings: [],
    loading: false,
  });
  const mode = form.mode;
  const credentialProvider = canonicalNamedCredentialProvider(form.provider);
  const { data: providerPresetResponse } = useApi(() => models.providerPresets(), []);
  const { data: namedCredentialResponse } = useApi(
    () => (credentialProvider ? namedCredentials.list({ provider: credentialProvider }) : Promise.resolve({ data: [] as NamedCredential[] })),
    [credentialProvider],
  );
  const { data: catalogDiscoveryResponse, loading: catalogDiscoveryLoading } = useApi(
    async () => {
      if (!form.provider) {
        return emptyDiscoveryResult();
      }
      try {
        return await models.discoverProviderModels({
          provider: form.provider,
          mode,
        });
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Failed to load provider model suggestions.';
        return { data: [], warnings: [message] };
      }
    },
    [form.provider, mode],
  );
  const modelSuggestionListId = useId();

  const providerPresets = providerPresetResponse?.data || [];
  const availableNamedCredentials = namedCredentialResponse?.data || [];
  const supportsCustomAuth = supportsCustomUpstreamAuthProvider(form.provider, form.model);
  const liveDiscoveryPayload: ProviderModelDiscoveryPayload = {
    provider: form.provider,
    mode,
    named_credential_id: form.credential_source === 'named' ? form.named_credential_id.trim() || null : null,
    api_key: form.credential_source === 'inline' ? form.api_key.trim() || null : null,
    api_base: form.credential_source === 'inline' ? form.api_base.trim() || null : null,
    api_version: form.credential_source === 'inline' ? form.api_version.trim() || null : null,
    auth_header_name: form.credential_source === 'inline' && supportsCustomAuth ? form.auth_header_name.trim() || null : null,
    auth_header_format: form.credential_source === 'inline' && supportsCustomAuth ? form.auth_header_format.trim() || null : null,
  };
  const liveDiscoveryRequestKey = buildLiveDiscoveryRequestKey(liveDiscoveryPayload);
  const hasActiveLiveDiscovery = liveDiscoveryState.requestKey === liveDiscoveryRequestKey;
  const modelOptions = hasActiveLiveDiscovery && liveDiscoveryState.data.length > 0
    ? liveDiscoveryState.data
    : catalogDiscoveryResponse?.data || [];
  const discoveryWarnings = [
    ...(catalogDiscoveryResponse?.warnings || []),
    ...(hasActiveLiveDiscovery ? liveDiscoveryState.warnings : []),
  ];
  const discoveryLoading = catalogDiscoveryLoading || (hasActiveLiveDiscovery && liveDiscoveryState.loading);
  const selectedProviderPreset = providerPresets.find((preset) => preset.provider === credentialProvider || preset.provider === form.provider);
  const selectedNamedCredential = availableNamedCredentials.find((credential) => credential.credential_id === form.named_credential_id) || null;
  const selectedModelOption = findProviderModelOption(modelOptions, form.model);

  const clearValidation = (field?: RequiredField) => {
    if (validationError) setValidationError(null);
    if (field && fieldErrors[field]) {
      setFieldErrors((current) => {
        const next = { ...current };
        delete next[field];
        return next;
      });
    }
  };

  const invalidateLiveDiscovery = () => {
    setLiveDiscoveryState((current) => {
      if (current.requestKey == null && !current.loading && current.data.length === 0 && current.warnings.length === 0) {
        return current;
      }
      return {
        requestKey: null,
        data: [],
        warnings: [],
        loading: false,
      };
    });
  };

  const applyProvider = (provider: string) => {
    const preset = providerPresets.find((item) => item.provider === provider);
    invalidateLiveDiscovery();
    setForm((current) => ({
      ...current,
      provider,
      model: current.provider === provider ? current.model : '',
      named_credential_id: current.provider === provider ? current.named_credential_id : '',
      named_credential_name: current.provider === provider ? current.named_credential_name : '',
      api_base: preset?.api_base || '',
      clear_inline_api_key: false,
    }));
    clearValidation('provider');
    clearValidation('model');
    clearValidation('api_base');
    clearValidation('named_credential_id');
  };

  const applyMode = (nextMode: ModelMode) => {
    invalidateLiveDiscovery();
    setForm((current) => ({ ...current, mode: nextMode }));
  };

  const updateProviderCredentialField = (
    field: 'api_key' | 'api_base' | 'api_version',
    value: string,
    clearField?: RequiredField,
  ) => {
    invalidateLiveDiscovery();
    setForm((current) => ({
      ...current,
      [field]: value,
      clear_inline_api_key: field === 'api_key' ? false : current.clear_inline_api_key,
    }));
    if (clearField) {
      clearValidation(clearField);
    }
  };

  const setCredentialSource = (credentialSource: 'inline' | 'named') => {
    invalidateLiveDiscovery();
    setForm((current) => ({
      ...current,
      credential_source: credentialSource,
      named_credential_id: credentialSource === 'named' ? current.named_credential_id : '',
      named_credential_name: credentialSource === 'named' ? current.named_credential_name : '',
      clear_inline_api_key: credentialSource === 'inline' ? current.clear_inline_api_key : false,
    }));
    clearValidation('api_base');
    clearValidation('named_credential_id');
  };

  const applyNamedCredential = (credentialId: string) => {
    const selected = availableNamedCredentials.find((credential) => credential.credential_id === credentialId) || null;
    invalidateLiveDiscovery();
    setForm((current) => ({
      ...current,
      named_credential_id: credentialId,
      named_credential_name: selected?.name || '',
    }));
    clearValidation('named_credential_id');
  };

  const updateModelValue = (value: string) => {
    clearValidation('model');
    const matchedOption = findProviderModelOption(modelOptions, value);
    setForm((current) => {
      const next = { ...current, model: value };
      return applyKnownMetadataToForm(next, matchedOption?.known_metadata || null);
    });
  };

  const refreshProviderModels = async () => {
    if (!form.provider) {
      return;
    }
    if (form.credential_source === 'named' && !form.named_credential_id.trim()) {
      return;
    }
    if (form.credential_source === 'inline' && !form.api_key.trim()) {
      return;
    }

    setLiveDiscoveryState({
      requestKey: liveDiscoveryRequestKey,
      data: [],
      warnings: [],
      loading: true,
    });

    try {
      const response = await models.discoverProviderModels(liveDiscoveryPayload);
      setLiveDiscoveryState({
        requestKey: liveDiscoveryRequestKey,
        data: response.data || [],
        warnings: response.warnings || [],
        loading: false,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to refresh provider models.';
      setLiveDiscoveryState({
        requestKey: liveDiscoveryRequestKey,
        data: [],
        warnings: [message],
        loading: false,
      });
    }
  };

  const handleSubmit = async () => {
    setValidationError(null);
    const nextFieldErrors: Partial<Record<RequiredField, string>> = {};
    const modelName = form.model_name.trim();
    const provider = form.provider.trim();
    const upstreamModel = form.model.trim();
    const apiBase = form.api_base.trim();
    const namedCredentialId = form.named_credential_id.trim();
    const useNamedCredential = form.credential_source === 'named';

    if (!modelName) {
      nextFieldErrors.model_name = 'Model Name is required.';
    }
    if (!provider) {
      nextFieldErrors.provider = 'Provider is required.';
    }
    if (!upstreamModel) {
      nextFieldErrors.model = 'Provider Model is required.';
    }
    if (useNamedCredential && !namedCredentialId) {
      nextFieldErrors.named_credential_id = 'Named credential is required.';
    }
    if (!useNamedCredential && !apiBase) {
      nextFieldErrors.api_base = 'API Base URL is required.';
    }
    if (Object.keys(nextFieldErrors).length > 0) {
      setFieldErrors(nextFieldErrors);
      setValidationError('Fill in the required fields highlighted below.');
      return;
    }
    setFieldErrors({});
    if (selectedProviderPreset && !selectedProviderPreset.supported_modes.includes(mode)) {
      const modeLabel = MODE_OPTIONS.find((m) => m.value === mode)?.label || mode;
      const supported = selectedProviderPreset.supported_modes
        .map((m) => MODE_OPTIONS.find((opt) => opt.value === m)?.label || m)
        .join(', ');
      setValidationError(
        `Provider "${providerDisplayName(selectedProviderPreset.provider)}" does not support "${modeLabel}". Supported types: ${supported || 'none'}.`,
      );
      return;
    }
    const payload = buildModelPayload(
      {
        ...form,
        model_name: modelName,
        provider,
        model: upstreamModel,
        api_base: apiBase,
        named_credential_id: namedCredentialId,
      },
      defaultParams,
    );
    await onSubmit(payload);
  };

  return (
    <div className="space-y-6">
      <Card title="Model Type">
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {MODE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => applyMode(opt.value)}
              className={`flex items-center gap-2 p-2.5 rounded-lg border text-left text-sm transition-colors ${
                mode === opt.value
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-gray-200 hover:border-gray-300 text-gray-600'
              }`}
            >
              <opt.icon className="w-4 h-4" />
              <div>
                <div className="font-medium text-xs">{opt.label}</div>
                <div className="text-[10px] text-gray-400 leading-tight">{opt.description}</div>
              </div>
            </button>
          ))}
        </div>
      </Card>

      <Card title="Provider Connection">
        <div className="space-y-4">
          <p className="text-xs text-gray-500">Fields marked <span className="text-red-500">*</span> are required.</p>
          {validationError ? <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{validationError}</div> : null}
          <div>
            <FieldLabel label="Model Name" required />
            <input value={form.model_name} onChange={(e) => { setForm({ ...form, model_name: e.target.value }); clearValidation('model_name'); }} placeholder={mode === 'image_generation' ? 'gpt-image-1.5' : mode === 'audio_speech' ? 'gpt-4o-mini-tts' : mode === 'audio_transcription' ? 'gpt-4o-transcribe' : mode === 'embedding' ? 'text-embedding-3-large' : mode === 'rerank' ? 'rerank-english-v3' : 'gpt-5.4'} className={inputClasses(Boolean(fieldErrors.model_name))} />
            {fieldErrors.model_name ? <p className="mt-1 text-xs text-red-600">{fieldErrors.model_name}</p> : null}
            <p className="text-xs text-gray-400 mt-1">Public name users will reference in API calls</p>
          </div>
          <div>
            <FieldLabel label="Provider and Provider Model" required />
            <div className={`flex overflow-hidden rounded-lg border bg-white focus-within:ring-2 ${fieldErrors.provider || fieldErrors.model ? 'border-red-300 focus-within:ring-red-500' : 'border-gray-300 focus-within:ring-blue-500'}`}>
              <select
                value={form.provider}
                onChange={(e) => applyProvider(e.target.value)}
                className="w-40 shrink-0 border-r border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-900 focus:outline-none"
              >
                <option value="">Select provider</option>
                {providerPresets.map((preset) => (
                  <option key={preset.provider} value={preset.provider}>
                    {providerDisplayName(preset.provider)}
                  </option>
                ))}
              </select>
              <input
                list={modelSuggestionListId}
                value={form.model}
                onChange={(e) => updateModelValue(e.target.value)}
                placeholder={upstreamModelPlaceholder(mode)}
                className="min-w-0 flex-1 px-3 py-2 text-sm text-gray-900 focus:outline-none"
              />
            </div>
            <datalist id={modelSuggestionListId}>
              {modelOptions.map((option) => (
                <option
                  key={`${option.provider}:${option.id}`}
                  value={option.id}
                  label={option.label}
                />
              ))}
            </datalist>
            {fieldErrors.provider ? <p className="mt-1 text-xs text-red-600">{fieldErrors.provider}</p> : null}
            {!fieldErrors.provider && fieldErrors.model ? <p className="mt-1 text-xs text-red-600">{fieldErrors.model}</p> : null}
            <div className="mt-2 flex items-center gap-2">
              <span className="text-xs text-gray-500">Selected provider:</span>
              {form.provider ? <ProviderBadge provider={form.provider} model={form.model} /> : <span className="text-xs text-gray-400">Choose a provider first</span>}
            </div>
            {discoveryLoading ? <p className="mt-1 text-xs text-gray-500">Loading provider model suggestions...</p> : null}
            {form.provider && !discoveryLoading && modelOptions.length > 0 ? (
              <p className="mt-1 text-xs text-gray-500">
                {modelOptions.length} suggested model{modelOptions.length === 1 ? '' : 's'} available. Custom values are still allowed.
              </p>
            ) : null}
            {form.provider && form.credential_source === 'inline' && !form.api_key.trim() ? (
              <p className="mt-1 text-xs text-gray-400">
                Showing built-in suggestions first. Add the provider API key, then refresh to fetch live provider models when supported.
              </p>
            ) : null}
            {form.provider && form.credential_source === 'named' && !form.named_credential_id.trim() ? (
              <p className="mt-1 text-xs text-gray-400">
                Select a named credential to fetch live provider models without entering inline secrets.
              </p>
            ) : null}
            {form.provider && ((form.credential_source === 'named' && form.named_credential_id.trim()) || (form.credential_source === 'inline' && form.api_key.trim())) ? (
              <div className="mt-2">
                <button
                  type="button"
                  onClick={() => { void refreshProviderModels(); }}
                  disabled={discoveryLoading}
                  className="inline-flex items-center rounded-md border border-gray-300 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {hasActiveLiveDiscovery && liveDiscoveryState.loading ? 'Refreshing...' : 'Refresh provider models'}
                </button>
              </div>
            ) : null}
            {selectedModelOption?.known_metadata ? (
              <p className="mt-1 text-xs text-emerald-700">
                Known pricing and limit metadata is available for this model. Empty fields below will be filled automatically.
              </p>
            ) : null}
            {selectedProviderPreset && (
              <p className="text-xs text-gray-500 mt-1">
                Supported model types: {selectedProviderPreset.supported_modes.join(', ')}
              </p>
            )}
            {discoveryWarnings.length > 0 ? (
              <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                {discoveryWarnings.join(' ')}
              </div>
            ) : null}
            <p className="text-xs text-gray-400 mt-1">Enter the upstream model name only. Example for Groq: <code>openai/gpt-oss-120b</code>.</p>
          </div>
          <div className="space-y-4 rounded-lg border border-gray-200 bg-gray-50/60 p-4">
            <div>
              <FieldLabel label="Credential Source" required />
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => setCredentialSource('named')}
                  className={`rounded-lg border px-3 py-3 text-left text-sm transition-colors ${form.credential_source === 'named' ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'}`}
                >
                  <div className="font-medium">Named Credential</div>
                  <div className="mt-0.5 text-xs text-gray-500">Recommended for shared provider access and credential rotation.</div>
                </button>
                <button
                  type="button"
                  onClick={() => setCredentialSource('inline')}
                  className={`rounded-lg border px-3 py-3 text-left text-sm transition-colors ${form.credential_source === 'inline' ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'}`}
                >
                  <div className="font-medium">Inline Credentials</div>
                  <div className="mt-0.5 text-xs text-gray-500">Use deployment-specific provider credentials directly on this model.</div>
                </button>
              </div>
            </div>

            {form.credential_source === 'named' ? (
              <div className="space-y-3">
                <div>
                  <FieldLabel label="Named Credential" required />
                  <select
                    value={form.named_credential_id}
                    onChange={(e) => applyNamedCredential(e.target.value)}
                    className={inputClasses(Boolean(fieldErrors.named_credential_id))}
                  >
                    <option value="">Select named credential</option>
                    {availableNamedCredentials.map((credential) => (
                      <option key={credential.credential_id} value={credential.credential_id}>
                        {credential.name}
                      </option>
                    ))}
                  </select>
                  {fieldErrors.named_credential_id ? <p className="mt-1 text-xs text-red-600">{fieldErrors.named_credential_id}</p> : null}
                  {form.provider && availableNamedCredentials.length === 0 ? (
                    <p className="mt-1 text-xs text-gray-400">No named credentials available for {providerDisplayName(form.provider)} yet.</p>
                  ) : null}
                </div>

                {selectedNamedCredential ? (
                  <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-3 text-sm">
                    <div className="font-medium text-blue-900">{selectedNamedCredential.name}</div>
                    <div className="mt-1 text-xs text-blue-700">
                      {selectedNamedCredential.connection_config?.api_base
                        ? `API base: ${String(selectedNamedCredential.connection_config.api_base)}`
                        : selectedNamedCredential.connection_config?.region
                          ? `Region: ${String(selectedNamedCredential.connection_config.region)}`
                          : 'Connection details are managed by this credential.'}
                    </div>
                    <div className="mt-1 text-xs text-blue-700">
                      Linked deployments: {selectedNamedCredential.usage_count || 0}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <FieldLabel label="API Key" />
                    <input type="password" value={form.api_key} onChange={(e) => updateProviderCredentialField('api_key', e.target.value)} placeholder="sk-..." className={inputClass} />
                    <div className="mt-1 flex items-center gap-3 text-xs">
                      {form.inline_credentials_present && !form.clear_inline_api_key ? (
                        <span className="text-gray-500">Stored inline key present. Leave blank to keep it.</span>
                      ) : null}
                      {form.clear_inline_api_key ? <span className="text-red-600">Stored inline key will be cleared on save.</span> : null}
                      {form.inline_credentials_present ? (
                        <button
                          type="button"
                          onClick={() => setForm((current) => ({ ...current, api_key: '', clear_inline_api_key: true }))}
                          className="font-medium text-red-600 hover:text-red-700"
                        >
                          Clear stored key
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <div>
                    <FieldLabel label="API Base URL" required />
                    <input value={form.api_base} onChange={(e) => updateProviderCredentialField('api_base', e.target.value, 'api_base')} placeholder={selectedProviderPreset?.api_base || 'https://your-provider.example/v1'} className={inputClasses(Boolean(fieldErrors.api_base))} />
                    {fieldErrors.api_base ? <p className="mt-1 text-xs text-red-600">{fieldErrors.api_base}</p> : null}
                    <p className="mt-1 text-xs text-gray-400">
                      {selectedProviderPreset?.api_base
                        ? 'Filled from the selected provider. Override it if your deployment uses a custom endpoint.'
                        : form.provider
                          ? 'This provider has no built-in default. You must enter the API base URL.'
                          : 'Choose a provider to auto-fill its default API base URL when one is available.'}
                    </p>
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <FieldLabel label="API Version" />
                    <input value={form.api_version} onChange={(e) => updateProviderCredentialField('api_version', e.target.value)} placeholder="e.g. 2024-02-01 (Azure)" className={inputClass} />
                  </div>
                  <div>
                    <FieldLabel label="Timeout (s)" />
                    <input type="number" value={form.timeout} onChange={(e) => setForm({ ...form, timeout: e.target.value })} placeholder="300" className={inputClass} />
                  </div>
                </div>
                {supportsCustomAuth ? (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div>
                      <FieldLabel label="Auth Header Name" />
                      <input
                        value={form.auth_header_name}
                        onChange={(e) => setForm({ ...form, auth_header_name: e.target.value })}
                        placeholder={DEFAULT_CUSTOM_AUTH_HEADER_NAME}
                        className={inputClass}
                      />
                    </div>
                    <div>
                      <FieldLabel label="Auth Header Format" />
                      <input
                        value={form.auth_header_format}
                        onChange={(e) => setForm({ ...form, auth_header_format: e.target.value })}
                        placeholder={DEFAULT_CUSTOM_AUTH_HEADER_FORMAT}
                        className={inputClass}
                      />
                    </div>
                    <p className="sm:col-span-2 text-xs text-gray-400">
                      Optional override for OpenAI-compatible providers. Only the <code>{'{api_key}'}</code> placeholder is supported.
                    </p>
                  </div>
                ) : null}
              </>
            )}
          </div>

          {form.credential_source === 'named' ? (
            <div>
              <FieldLabel label="Timeout (s)" />
              <input type="number" value={form.timeout} onChange={(e) => setForm({ ...form, timeout: e.target.value })} placeholder="300" className={inputClass} />
            </div>
          ) : null}
        </div>
      </Card>

      <CollapsibleCard title="Rate Limits & Routing">
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM</label>
              <input type="number" value={form.rpm} onChange={(e) => setForm({ ...form, rpm: e.target.value })} placeholder="500" className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM</label>
              <input type="number" value={form.tpm} onChange={(e) => setForm({ ...form, tpm: e.target.value })} placeholder="100000" className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Weight</label>
              <input type="number" value={form.weight} onChange={(e) => setForm({ ...form, weight: e.target.value })} placeholder="1" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Load balancing</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Priority</label>
              <input type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} placeholder="0" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Higher = preferred</p>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Tags</label>
            <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="production, fast, us-east" className={inputClass} />
            <p className="text-xs text-gray-400 mt-1">Comma-separated, for tag-based routing</p>
          </div>
        </div>
      </CollapsibleCard>

      {mode === 'chat' && (
        <CollapsibleCard title="Chat Settings">
          <div className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Stream Timeout (s)</label>
                <input type="number" value={form.stream_timeout} onChange={(e) => setForm({ ...form, stream_timeout: e.target.value })} placeholder="120" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens / Request</label>
                <input type="number" value={form.max_tokens} onChange={(e) => setForm({ ...form, max_tokens: e.target.value })} placeholder="4096" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Context Window</label>
                <input type="number" value={form.max_context_window} onChange={(e) => setForm({ ...form, max_context_window: e.target.value })} placeholder="128000" className={inputClass} />
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Max Input Tokens</label>
                <input type="number" value={form.max_input_tokens} onChange={(e) => setForm({ ...form, max_input_tokens: e.target.value })} placeholder="e.g. 128000" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Max Output Tokens</label>
                <input type="number" value={form.max_output_tokens} onChange={(e) => setForm({ ...form, max_output_tokens: e.target.value })} placeholder="e.g. 4096" className={inputClass} />
              </div>
            </div>
          </div>
        </CollapsibleCard>
      )}

      {mode === 'embedding' && (
        <CollapsibleCard title="Embedding Settings">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Context Window</label>
              <input type="number" value={form.max_context_window} onChange={(e) => setForm({ ...form, max_context_window: e.target.value })} placeholder="8192" className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Output Vector Size</label>
              <input type="number" value={form.output_vector_size} onChange={(e) => setForm({ ...form, output_vector_size: e.target.value })} placeholder="1536" className={inputClass} />
            </div>
          </div>
        </CollapsibleCard>
      )}

      <CollapsibleCard title="Cost Tracking">
        <div className="space-y-4">
          {mode === 'chat' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
                <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="0.000005" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Output Cost / Token ($)</label>
                <input type="number" step="any" value={form.output_cost_per_token} onChange={(e) => setForm({ ...form, output_cost_per_token: e.target.value })} placeholder="0.000015" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cached Input Cost / Token ($)</label>
                <input type="number" step="any" value={form.input_cost_per_token_cache_hit} onChange={(e) => setForm({ ...form, input_cost_per_token_cache_hit: e.target.value })} placeholder="Optional override" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cached Output Cost / Token ($)</label>
                <input type="number" step="any" value={form.output_cost_per_token_cache_hit} onChange={(e) => setForm({ ...form, output_cost_per_token_cache_hit: e.target.value })} placeholder="Optional override" className={inputClass} />
              </div>
            </div>
          )}

          {mode === 'embedding' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
                <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="0.0000001" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cached Input Cost / Token ($)</label>
                <input type="number" step="any" value={form.input_cost_per_token_cache_hit} onChange={(e) => setForm({ ...form, input_cost_per_token_cache_hit: e.target.value })} placeholder="Optional override" className={inputClass} />
              </div>
            </div>
          )}

          {mode === 'image_generation' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Image ($)</label>
              <input type="number" step="any" value={form.input_cost_per_image} onChange={(e) => setForm({ ...form, input_cost_per_image: e.target.value })} placeholder="0.04" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Cost per generated image</p>
            </div>
          )}

          {mode === 'audio_speech' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Character ($)</label>
                <input type="number" step="any" value={form.input_cost_per_character} onChange={(e) => setForm({ ...form, input_cost_per_character: e.target.value })} placeholder="0.000015" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Output Cost / Character ($)</label>
                <input type="number" step="any" value={form.output_cost_per_character} onChange={(e) => setForm({ ...form, output_cost_per_character: e.target.value })} placeholder="0.000015" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Second ($)</label>
                <input type="number" step="any" value={form.input_cost_per_second} onChange={(e) => setForm({ ...form, input_cost_per_second: e.target.value })} placeholder="0.00025" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Output Cost / Second ($)</label>
                <input type="number" step="any" value={form.output_cost_per_second} onChange={(e) => setForm({ ...form, output_cost_per_second: e.target.value })} placeholder="0.00025" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Audio Token Cost ($)</label>
                <input type="number" step="any" value={form.input_cost_per_audio_token} onChange={(e) => setForm({ ...form, input_cost_per_audio_token: e.target.value })} placeholder="0.0001" className={inputClass} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Output Audio Token Cost ($)</label>
                <input type="number" step="any" value={form.output_cost_per_audio_token} onChange={(e) => setForm({ ...form, output_cost_per_audio_token: e.target.value })} placeholder="0.0001" className={inputClass} />
              </div>
            </div>
          )}

          {mode === 'audio_transcription' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Second ($)</label>
                <input type="number" step="any" value={form.input_cost_per_second} onChange={(e) => setForm({ ...form, input_cost_per_second: e.target.value })} placeholder="0.0001" className={inputClass} />
                <p className="text-xs text-gray-400 mt-1">Primary STT pricing field for duration-based billing</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Output Cost / Second ($)</label>
                <input type="number" step="any" value={form.output_cost_per_second} onChange={(e) => setForm({ ...form, output_cost_per_second: e.target.value })} placeholder="0" className={inputClass} />
                <p className="text-xs text-gray-400 mt-1">Optional for providers that bill output audio duration separately</p>
              </div>
            </div>
          )}

          {mode === 'rerank' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
              <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="0.000002" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Cost per token in query + documents</p>
            </div>
          )}

          {(mode === 'chat' || mode === 'embedding') && (
            <>
              <div className="border-t border-gray-100 pt-4 mt-4">
                <h4 className="text-sm font-medium text-gray-700 mb-1">Batch Pricing</h4>
                <p className="text-xs text-gray-400 mb-3">Optional overrides for batch API processing. Set a multiplier (e.g. 0.5 = 50% discount) or explicit per-token costs. Explicit costs take priority over the multiplier.</p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Price Multiplier</label>
                    <input type="number" step="any" value={form.batch_price_multiplier} onChange={(e) => setForm({ ...form, batch_price_multiplier: e.target.value })} placeholder="0.5" className={inputClass} />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Batch Input Cost / Token ($)</label>
                    <input type="number" step="any" value={form.batch_input_cost_per_token} onChange={(e) => setForm({ ...form, batch_input_cost_per_token: e.target.value })} placeholder="e.g. 0.0000025" className={inputClass} />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Batch Output Cost / Token ($)</label>
                    <input type="number" step="any" value={form.batch_output_cost_per_token} onChange={(e) => setForm({ ...form, batch_output_cost_per_token: e.target.value })} placeholder="e.g. 0.0000075" className={inputClass} />
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </CollapsibleCard>

      <CollapsibleCard title="Default Parameters">
        <div className="space-y-3">
          <p className="text-xs text-gray-400">Default values injected into provider requests when not specified by the caller (e.g. voice, response_format)</p>
          {defaultParams.map((param, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <input
                value={param.key}
                onChange={(e) => {
                  const updated = [...defaultParams];
                  updated[idx] = { ...updated[idx], key: e.target.value };
                  setDefaultParams(updated);
                }}
                placeholder="Key (e.g. voice)"
                className={`flex-1 ${inputClass}`}
              />
              <input
                value={param.value}
                onChange={(e) => {
                  const updated = [...defaultParams];
                  updated[idx] = { ...updated[idx], value: e.target.value };
                  setDefaultParams(updated);
                }}
                placeholder="Value (e.g. alloy)"
                className={`flex-1 ${inputClass}`}
              />
              <button
                type="button"
                onClick={() => setDefaultParams(defaultParams.filter((_, i) => i !== idx))}
                className="p-2 hover:bg-red-50 rounded-lg"
              >
                <X className="w-4 h-4 text-red-400" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => setDefaultParams([...defaultParams, { key: '', value: '' }])}
            className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 font-medium"
          >
            <Plus className="w-3.5 h-3.5" /> Add Default Parameter
          </button>
        </div>
      </CollapsibleCard>

      {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}

      <div className="flex justify-end gap-3">
        <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
        <button onClick={handleSubmit} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : submitLabel}</button>
      </div>
    </div>
  );
}
