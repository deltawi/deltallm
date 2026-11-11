import { useId, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, PlugZap, RefreshCw, Sparkles, XCircle } from 'lucide-react';
import { type AccessGroupTokenInputHandle } from './AccessGroupTokenInput';
import Button from './Button';
import SegmentedControl from './SegmentedControl';
import { useApi } from '../lib/hooks';
import {
  models,
  namedCredentials,
  type ManagedAssetAccessInput,
  type ModelConnectionTestResult,
  type ModelConnectionTestStatus,
  type NamedCredential,
  type ProviderModelDiscoveryPayload,
  type ProviderModelOption,
} from '../lib/api';
import NamedCredentialCreateDialog from './NamedCredentialCreateDialog';
import type { NamedCredentialAudienceOption } from './NamedCredentialForm';
import {
  canonicalNamedCredentialProvider,
  providerDisplayName,
  supportsCustomUpstreamAuthProvider,
} from '../lib/providers';
import {
  EMPTY_FORM,
  MODE_OPTIONS,
  buildModelPayload,
  suggestApiModelSlug,
  type ModelFormValues,
  type ModelMode,
  type ModelPayload,
} from './modelFormShared';
import RealtimeModelFields from './models/RealtimeModelFields';
import ChatCapabilitiesFields from './models/ChatCapabilitiesFields';
import { HeadersNetworkCardBody, ParametersCardBody } from './models/RequestShapingCards';
import {
  EMPTY_REQUEST_SHAPING,
  activeNetworkCount,
  activeParamCount,
  type RequestShapingForm,
} from './models/requestShaping';
import { applyKnownMetadata, type AutofilledValues } from './models/metadataAutofill';
import { buildConnectionTestPayload, connectionTestBlocker, connectionTestKey } from './models/connectionTest';
import ProviderPicker from './models/ProviderPicker';
import ProviderModelPicker from './models/ProviderModelPicker';
import { compactTokenCount, modeShortLabel } from './models/providerListing';
import CredentialFields from './models/form/CredentialFields';
import PricingFields from './models/form/PricingFields';
import ModelFormAside from './models/form/ModelFormAside';
import { BatchExecutionFields, ChatLimitsFields, EmbeddingLimitsFields, LimitsRoutingFields } from './models/form/AdvancedSettings';
import { FieldError, FieldLabel, FormPanel, SettingsList, SettingsRow } from './models/form/formPrimitives';
import { HELP_CLASS, fieldClass } from './models/form/formStyles';
import { modelFormChecklist } from './models/form/modelFormSummary';
import { validateModelForm, type ErrorSection, type RequiredField } from './models/form/modelFormValidation';
import {
  PROVIDER_API_BASE_PLACEHOLDERS,
  PROVIDER_API_KEY_HINTS,
  buildLiveDiscoveryRequestKey,
  defaultParameterHint,
  emptyDiscoveryResult,
  findProviderModelOption,
  normalizedProviderKey,
  upstreamModelPlaceholder,
} from './models/form/modelFormHints';

const CONNECTION_TEST_STYLES: Record<ModelConnectionTestStatus, { box: string; icon: typeof Info; title: string }> = {
  passed: { box: 'border-green-200 bg-green-50 text-green-800', icon: CheckCircle2, title: 'Connection works' },
  warning: { box: 'border-amber-200 bg-amber-50 text-amber-800', icon: AlertTriangle, title: 'Connected, check the model' },
  failed: { box: 'border-red-200 bg-red-50 text-red-800', icon: XCircle, title: 'Connection failed' },
  unavailable: { box: 'border-gray-200 bg-gray-50 text-gray-700', icon: Info, title: 'Not tested' },
};

function ConnectionTestNotice({ result }: { result: ModelConnectionTestResult }) {
  const style = CONNECTION_TEST_STYLES[result.status] || CONNECTION_TEST_STYLES.unavailable;
  const Icon = style.icon;
  return (
    <div role="status" className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${style.box}`}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <div className="font-medium">{style.title}</div>
        <p className="mt-0.5 text-xs">{result.message}</p>
      </div>
    </div>
  );
}

const MODE_SHORT: Record<ModelMode, string> = {
  chat: 'Chat',
  embedding: 'Embedding',
  image_generation: 'Image',
  audio_speech: 'Speech',
  audio_transcription: 'Transcription',
  realtime: 'Realtime',
  rerank: 'Rerank',
};
const MODE_ITEMS = MODE_OPTIONS.map((option) => ({
  value: option.value,
  label: MODE_SHORT[option.value] || option.label,
  icon: option.icon,
  title: `${option.label}: ${option.description}`,
}));

const NAME_PLACEHOLDERS: Partial<Record<ModelMode, string>> = {
  image_generation: 'Image Generator',
  audio_speech: 'Voice Assistant',
  audio_transcription: 'Meeting Transcriber',
  embedding: 'Document Embeddings',
  rerank: 'Search Reranker',
};

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

interface ModelFormProps {
  /** The saved deployment being edited, so a connection test can reuse its stored secrets. */
  deploymentId?: string;
  initialValues?: ModelFormValues;
  initialShaping?: RequestShapingForm;
  initialModelInfo?: Record<string, unknown>;
  onSubmit: (payload: ModelPayload) => Promise<void>;
  onCancel: () => void;
  submitLabel?: string;
  saving?: boolean;
  error?: string | null;
  allowInlineCredentials?: boolean;
  lockApiModelId?: boolean;
  namespaceRequired?: boolean;
  namedCredentialDefaultAccess?: ManagedAssetAccessInput;
  namedCredentialTeamOptions?: NamedCredentialAudienceOption[];
  namedCredentialOrganizationOptions?: NamedCredentialAudienceOption[];
  allowPublicNamedCredentials?: boolean;
  namedCredentialAudiencesLoading?: boolean;
  namedCredentialAudiencesError?: string | null;
  onRetryNamedCredentialAudiences?: () => void;
}

export default function ModelForm({
  deploymentId,
  initialValues,
  initialShaping,
  initialModelInfo,
  onSubmit,
  onCancel,
  submitLabel = 'Create',
  saving = false,
  error = null,
  allowInlineCredentials = true,
  lockApiModelId = false,
  namespaceRequired = false,
  namedCredentialDefaultAccess,
  namedCredentialTeamOptions = [],
  namedCredentialOrganizationOptions = [],
  allowPublicNamedCredentials = false,
  namedCredentialAudiencesLoading = false,
  namedCredentialAudiencesError = null,
  onRetryNamedCredentialAudiences,
}: ModelFormProps) {
  const [form, setForm] = useState<ModelFormValues>(() => {
    const values = initialValues || { ...EMPTY_FORM };
    return allowInlineCredentials || values.credential_source === 'named'
      ? values
      : { ...values, credential_source: 'named' };
  });
  const [shaping, setShaping] = useState<RequestShapingForm>(initialShaping || EMPTY_REQUEST_SHAPING);
  const [initialModelInfoSnapshot] = useState<Record<string, unknown>>(() => ({ ...(initialModelInfo || {}) }));
  const [validationError, setValidationError] = useState<string | null>(null);
  const [errorSection, setErrorSection] = useState<ErrorSection>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<RequiredField, string>>>({});
  const [apiIdentityEdited, setApiIdentityEdited] = useState(
    () => Boolean(initialValues?.api_model_id || initialValues?.api_model_slug),
  );
  const [credentialCreateOpen, setCredentialCreateOpen] = useState(false);
  const [autofilledValues, setAutofilledValues] = useState<AutofilledValues>({});
  const [recentlyCreatedCredential, setRecentlyCreatedCredential] = useState<NamedCredential | null>(null);
  const [connectionTest, setConnectionTest] = useState<{
    key: string;
    loading: boolean;
    result: ModelConnectionTestResult | null;
    error: string | null;
  } | null>(null);
  const accessGroupInputRef = useRef<AccessGroupTokenInputHandle | null>(null);
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
  const { data: providerPresetResponse } = useApi(
    (signal) => models.providerPresets(signal),
    [],
  );
  const { data: namedCredentialResponse, refetch: refetchNamedCredentials } = useApi(
    () => (credentialProvider ? namedCredentials.list({ provider: credentialProvider }) : Promise.resolve({ data: [] as NamedCredential[] })),
    [credentialProvider],
  );
  const { data: catalogDiscoveryResponse, loading: catalogDiscoveryLoading } = useApi(
    async (signal) => {
      if (
        !form.provider
        || (!allowInlineCredentials
          && !form.keep_existing_named_credential
          && !form.named_credential_id.trim())
      ) {
        return emptyDiscoveryResult();
      }
      try {
        return await models.discoverProviderModels({
          provider: form.provider,
          mode,
        }, signal);
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Failed to load provider model suggestions.';
        return { data: [], warnings: [message] };
      }
    },
    [
      allowInlineCredentials,
      form.keep_existing_named_credential,
      form.named_credential_id,
      form.provider,
      mode,
    ],
  );
  const fieldId = useId();
  const ids = {
    name: `${fieldId}-name`,
    apiId: `${fieldId}-api-id`,
    provider: `${fieldId}-provider`,
    providerError: `${fieldId}-provider-error`,
    model: `${fieldId}-model`,
    modelHelp: `${fieldId}-model-help`,
  };

  const providerPresets = providerPresetResponse?.data || [];
  const listedNamedCredentials = namedCredentialResponse?.data || [];
  const availableNamedCredentials = recentlyCreatedCredential
    && canonicalNamedCredentialProvider(recentlyCreatedCredential.provider) === credentialProvider
    && !listedNamedCredentials.some((credential) => credential.credential_id === recentlyCreatedCredential.credential_id)
    ? [recentlyCreatedCredential, ...listedNamedCredentials]
    : listedNamedCredentials;
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
  const autofilledCount = Object.entries(autofilledValues)
    .filter(([field, value]) => form[field as keyof AutofilledValues] === value)
    .length;
  const canFillFromKnownMetadata = Boolean(selectedModelOption?.known_metadata)
    && applyKnownMetadata(form, autofilledValues, selectedModelOption?.known_metadata || null).form !== form;
  const defaultParamHint = defaultParameterHint(form.provider, mode);
  const paramCount = activeParamCount(shaping);
  const networkCount = activeNetworkCount(shaping, { chat: mode === 'chat' });
  const connectionTestContext = { deploymentId, allowInlineCredentials, supportsCustomAuth };
  const connectionTestPayload = buildConnectionTestPayload(form, shaping, connectionTestContext);
  const currentConnectionTestKey = connectionTestKey(connectionTestPayload);
  const connectionTestBlockedBy = connectionTestBlocker(form, connectionTestContext);
  const visibleConnectionTest = connectionTest?.key === currentConnectionTestKey ? connectionTest : null;
  const credentialReady = form.credential_source === 'named'
    ? form.keep_existing_named_credential || Boolean(form.named_credential_id.trim())
    : Boolean(form.api_key.trim() || (form.inline_credentials_present && !form.clear_inline_api_key));
  const canRefreshModels = Boolean(form.provider) && (
    form.credential_source === 'named'
      ? Boolean(form.named_credential_id.trim())
      : Boolean(form.api_key.trim())
  );
  const apiModelIdPreview = namespaceRequired
    ? `${form.api_namespace}/${form.api_model_slug}`
    : form.api_model_id;

  const runConnectionTest = async () => {
    if (connectionTestBlockedBy) return;
    const key = currentConnectionTestKey;
    setConnectionTest({ key, loading: true, result: null, error: null });
    try {
      const result = await models.testConnection(connectionTestPayload);
      setConnectionTest((current) => (current?.key === key ? { key, loading: false, result, error: null } : current));
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Connection test failed.';
      setConnectionTest((current) => (current?.key === key ? { key, loading: false, result: null, error: message } : current));
    }
  };

  const clearValidation = (field?: RequiredField) => {
    if (validationError) setValidationError(null);
    if (errorSection) setErrorSection(null);
    if (field && fieldErrors[field]) {
      setFieldErrors((current) => {
        const next = { ...current };
        delete next[field];
        return next;
      });
    }
  };

  const updateForm = (patch: Partial<ModelFormValues>) => {
    setForm((current) => ({ ...current, ...patch }));
    const touched = Object.keys(patch) as RequiredField[];
    if (validationError || errorSection || touched.some((field) => fieldErrors[field])) {
      setValidationError(null);
      setErrorSection(null);
      setFieldErrors((current) => {
        const next = { ...current };
        for (const field of touched) delete next[field];
        return next;
      });
    }
  };

  const updateShaping = (next: RequestShapingForm) => {
    setShaping(next);
    if (validationError) setValidationError(null);
    if (errorSection) setErrorSection(null);
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
    if (form.keep_existing_named_credential) return;
    const preset = providerPresets.find((item) => item.provider === provider);
    invalidateLiveDiscovery();
    if (form.provider !== provider) {
      // The model is cleared below, so prices and limits it filled go too.
      const cleared = applyKnownMetadata(form, autofilledValues, null);
      setAutofilledValues(cleared.autofilled);
      setForm(cleared.form);
    }
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
    setFieldErrors((current) => {
      const next = { ...current };
      delete next.max_context_window;
      delete next.max_input_tokens;
      delete next.max_output_tokens;
      delete next.provider;
      return next;
    });
    if (validationError) setValidationError(null);
    if (errorSection) setErrorSection(null);
  };

  const updateProviderCredentialField = (field: 'api_key' | 'api_base' | 'api_version', value: string) => {
    invalidateLiveDiscovery();
    setForm((current) => ({
      ...current,
      [field]: value,
      clear_inline_api_key: field === 'api_key' ? false : current.clear_inline_api_key,
    }));
    if (field === 'api_base') clearValidation('api_base');
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
      keep_existing_named_credential: false,
    }));
    clearValidation('named_credential_id');
  };

  const replaceKeptCredential = () => {
    invalidateLiveDiscovery();
    setForm((current) => ({
      ...current,
      keep_existing_named_credential: false,
      named_credential_id: '',
      named_credential_name: '',
    }));
  };

  const handleNamedCredentialCreated = (credential: NamedCredential) => {
    invalidateLiveDiscovery();
    setRecentlyCreatedCredential(credential);
    setForm((current) => ({
      ...current,
      provider: current.provider || credential.provider,
      model: current.provider && current.provider !== credential.provider ? '' : current.model,
      credential_source: 'named',
      named_credential_id: credential.credential_id,
      named_credential_name: credential.name,
      keep_existing_named_credential: false,
      clear_inline_api_key: false,
    }));
    clearValidation('named_credential_id');
    refetchNamedCredentials();
  };

  const updateModelValue = (value: string) => {
    clearValidation('model');
    const matchedOption = findProviderModelOption(modelOptions, value);
    // Fields filled for the previous value follow the new one, so typing
    // "gpt-4o" does not keep the prices of "gpt-4" matched on the way.
    const applied = applyKnownMetadata(
      { ...form, model: value },
      autofilledValues,
      matchedOption?.known_metadata || null,
    );
    setAutofilledValues(applied.autofilled);
    setForm(applied.form);
  };

  const fillFromKnownMetadata = () => {
    const applied = applyKnownMetadata(form, autofilledValues, selectedModelOption?.known_metadata || null);
    setAutofilledValues(applied.autofilled);
    setForm(applied.form);
  };

  const refreshProviderModels = async () => {
    if (!canRefreshModels) return;
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
    setErrorSection(null);
    const accessGroupCommit = accessGroupInputRef.current?.validateAndCommit();
    if (accessGroupCommit && !accessGroupCommit.valid) {
      setValidationError(accessGroupCommit.message || 'Fix the invalid access group before saving.');
      setErrorSection('limits');
      return;
    }
    const result = validateModelForm(form, shaping, {
      namespaceRequired,
      lockApiModelId,
      allowInlineCredentials,
      preset: selectedProviderPreset,
    });
    if (!result.ok) {
      setFieldErrors(result.fieldErrors);
      setValidationError(result.message);
      setErrorSection(result.section);
      return;
    }
    setFieldErrors({});
    const payload = buildModelPayload(
      { ...result.values, access_groups: accessGroupCommit?.value ?? form.access_groups },
      shaping,
      initialModelInfoSnapshot,
    );
    await onSubmit(payload);
  };

  const capacityErrors = {
    max_context_window: fieldErrors.max_context_window,
    max_input_tokens: fieldErrors.max_input_tokens,
    max_output_tokens: fieldErrors.max_output_tokens,
  };
  const limitsSummary = [
    form.rpm ? `${form.rpm} RPM` : null,
    form.tpm ? `${form.tpm} TPM` : null,
    form.weight ? `weight ${form.weight}` : null,
    form.priority ? `priority ${form.priority}` : null,
  ].filter(Boolean).join(' · ') || 'Gateway defaults';
  const contextSummary = compactTokenCount(Number(form.max_context_window || form.max_input_tokens) || null);
  const capabilityCount = Object.values(form.chat_capabilities).filter(Boolean).length;
  const hasPrice = Object.entries(form).some(([field, value]) => field.includes('cost_per_') && typeof value === 'string' && value.trim() !== '');
  const checklist = modelFormChecklist({
    modelName: form.model_name,
    apiModelId: namespaceRequired ? form.api_model_slug : form.api_model_id,
    provider: form.provider,
    model: form.model,
    credentialReady,
    hasPrice,
  });
  const modelFooter = !form.provider ? null
    : form.credential_source === 'inline' && !form.api_key.trim() && !form.inline_credentials_present
      ? 'Built-in suggestions. Add the API key, then refresh the list to load live models.'
      : form.credential_source === 'named' && !form.keep_existing_named_credential && !form.named_credential_id.trim()
        ? 'Select a saved credential to load live models.'
        : `${countLabel(modelOptions.length, 'suggestion')}. Any model ID works.`;

  return (
    <div>
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          <FormPanel title="Model" description="What the model does and the name clients use.">
            <div>
              <FieldLabel label="Type" />
              <SegmentedControl label="Model type" items={MODE_ITEMS} value={mode} onChange={applyMode} />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <FieldLabel htmlFor={ids.name} label="Name" required />
                <input
                  id={ids.name}
                  value={form.model_name}
                  onChange={(e) => {
                    const displayName = e.target.value;
                    const suggestedSlug = suggestApiModelSlug(displayName);
                    setForm((current) => ({
                      ...current,
                      model_name: displayName,
                      api_model_slug: namespaceRequired && !apiIdentityEdited ? suggestedSlug : current.api_model_slug,
                      api_model_id: !namespaceRequired && !apiIdentityEdited ? suggestedSlug : current.api_model_id,
                    }));
                    clearValidation('model_name');
                  }}
                  placeholder={NAME_PLACEHOLDERS[mode] || 'Customer Support Model'}
                  aria-invalid={Boolean(fieldErrors.model_name) || undefined}
                  className={fieldClass(Boolean(fieldErrors.model_name))}
                />
                <FieldError message={fieldErrors.model_name} />
                <p className={HELP_CLASS}>Shown in the dashboard. You can change it later.</p>
              </div>
              <div>
                <FieldLabel htmlFor={ids.apiId} label="API model ID" required />
                {namespaceRequired && !lockApiModelId ? (
                  <div
                    id={ids.apiId}
                    aria-label="Generated API Model ID"
                    className="control-field flex items-center overflow-hidden rounded-lg border border-gray-200 bg-gray-50 px-3 font-mono text-gray-700"
                  >
                    <span className="text-gray-500">{form.api_namespace}/</span>
                    <span className="truncate">{form.api_model_slug || 'model'}</span>
                  </div>
                ) : namespaceRequired ? (
                  <div className="flex items-stretch">
                    <input
                      aria-label="Creator namespace"
                      readOnly={lockApiModelId || form.api_namespace_locked}
                      value={form.api_namespace}
                      onChange={(e) => {
                        setForm((current) => ({ ...current, api_namespace: e.target.value.toLowerCase() }));
                        clearValidation('api_namespace');
                      }}
                      className={`${fieldClass(Boolean(fieldErrors.api_namespace))} rounded-r-none font-mono`}
                    />
                    <span className="flex items-center border-y border-gray-200 bg-gray-50 px-2 font-mono text-sm text-gray-400">/</span>
                    <input
                      aria-label="API model slug"
                      readOnly={lockApiModelId}
                      value={form.api_model_slug}
                      onChange={(e) => {
                        setApiIdentityEdited(true);
                        setForm((current) => ({ ...current, api_model_slug: e.target.value.toLowerCase() }));
                        clearValidation('api_model_slug');
                      }}
                      className={`${fieldClass(Boolean(fieldErrors.api_model_slug))} rounded-l-none font-mono`}
                    />
                  </div>
                ) : (
                  <input
                    id={ids.apiId}
                    readOnly={lockApiModelId}
                    value={form.api_model_id}
                    placeholder="customer-support"
                    onChange={(e) => {
                      setApiIdentityEdited(true);
                      setForm((current) => ({ ...current, api_model_id: e.target.value }));
                      clearValidation('api_model_id');
                    }}
                    aria-invalid={Boolean(fieldErrors.api_model_id) || undefined}
                    className={`${fieldClass(Boolean(fieldErrors.api_model_id))} font-mono`}
                  />
                )}
                <FieldError message={fieldErrors.api_namespace || fieldErrors.api_model_slug || fieldErrors.api_model_id} />
                <p className={HELP_CLASS}>
                  {lockApiModelId
                    ? 'This ID is fixed so existing API clients keep working.'
                    : namespaceRequired
                      ? 'Made from the name. Clients send it as "model". It cannot change after you create the model.'
                      : 'Clients send this as "model". It cannot change after you create the model.'}
                </p>
              </div>
            </div>
          </FormPanel>

          <FormPanel
            title="Connection"
            description="Where requests to this model are sent."
            actions={(
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void runConnectionTest(); }}
                disabled={Boolean(connectionTestBlockedBy) || saving}
                loading={Boolean(visibleConnectionTest?.loading)}
                title={connectionTestBlockedBy || 'Check the provider settings without saving. No billable call is made.'}
              >
                {visibleConnectionTest?.loading ? null : <PlugZap className="h-3.5 w-3.5" aria-hidden="true" />}
                {visibleConnectionTest?.loading ? 'Testing...' : 'Test connection'}
              </Button>
            )}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <FieldLabel htmlFor={ids.provider} label="Provider" required />
                <ProviderPicker
                  id={ids.provider}
                  presets={providerPresets}
                  value={form.provider}
                  mode={mode}
                  onChange={applyProvider}
                  disabled={form.keep_existing_named_credential}
                  invalid={Boolean(fieldErrors.provider)}
                  describedBy={fieldErrors.provider ? ids.providerError : undefined}
                />
                <FieldError id={ids.providerError} message={fieldErrors.provider} />
                {form.keep_existing_named_credential ? (
                  <p className={HELP_CLASS}>The provider comes from the owner-managed credential.</p>
                ) : null}
              </div>
              <div>
                <FieldLabel
                  htmlFor={ids.model}
                  label="Provider model"
                  required
                  action={canRefreshModels ? (
                    <button
                      type="button"
                      onClick={() => { void refreshProviderModels(); }}
                      disabled={discoveryLoading}
                      className="inline-flex items-center gap-1 rounded text-[11px] font-medium text-brand-primary-ink hover:text-brand-primary-ink-hover disabled:opacity-50"
                    >
                      <RefreshCw className={`h-3 w-3 ${hasActiveLiveDiscovery && liveDiscoveryState.loading ? 'animate-spin' : ''}`} aria-hidden="true" />
                      {hasActiveLiveDiscovery && liveDiscoveryState.loading ? 'Loading...' : 'Load live models'}
                    </button>
                  ) : null}
                />
                <ProviderModelPicker
                  id={ids.model}
                  value={form.model}
                  onChange={updateModelValue}
                  options={modelOptions}
                  providerLabel={form.provider ? providerDisplayName(form.provider) : 'the provider'}
                  placeholder={form.provider ? upstreamModelPlaceholder(mode, form.provider) : 'Select a provider first'}
                  loading={discoveryLoading}
                  disabled={!form.provider}
                  invalid={Boolean(fieldErrors.model)}
                  describedBy={ids.modelHelp}
                  footer={modelFooter}
                />
                <FieldError message={fieldErrors.model} />
                <p id={ids.modelHelp} className={HELP_CLASS}>The model name at the provider, for example openai/gpt-oss-120b on Groq.</p>
              </div>
            </div>
            {discoveryWarnings.length > 0 ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">{discoveryWarnings.join(' ')}</div>
            ) : null}
            <CredentialFields
              form={form}
              allowInlineCredentials={allowInlineCredentials}
              supportsCustomAuth={supportsCustomAuth}
              credentials={availableNamedCredentials}
              selectedCredential={selectedNamedCredential}
              apiBasePlaceholder={selectedProviderPreset?.api_base || PROVIDER_API_BASE_PLACEHOLDERS[normalizedProviderKey(form.provider)] || 'https://your-provider.example/v1'}
              apiBaseHelp={selectedProviderPreset?.api_base
                ? 'Filled from the provider. Change it for a custom endpoint.'
                : form.provider
                  ? 'This provider has no default. Enter the API base URL.'
                  : 'Select a provider to fill its default API base URL.'}
              apiKeyHint={PROVIDER_API_KEY_HINTS[normalizedProviderKey(form.provider)]}
              errors={{ named_credential_id: fieldErrors.named_credential_id, api_base: fieldErrors.api_base }}
              onSourceChange={setCredentialSource}
              onSelectCredential={applyNamedCredential}
              onCreateCredential={() => setCredentialCreateOpen(true)}
              onReplaceKeptCredential={replaceKeptCredential}
              onCredentialFieldChange={updateProviderCredentialField}
              onChange={updateForm}
            />
            {visibleConnectionTest?.result ? <ConnectionTestNotice result={visibleConnectionTest.result} /> : null}
            {visibleConnectionTest?.error ? (
              <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{visibleConnectionTest.error}</div>
            ) : null}
          </FormPanel>

          {mode === 'realtime' ? (
            <FormPanel title="Realtime audio" description="Session type and prices for WebSocket sessions.">
              <RealtimeModelFields form={form} onChange={setForm} />
            </FormPanel>
          ) : (
            <FormPanel
              title="Pricing"
              description="Used for spend, budgets and usage reports."
              actions={autofilledCount > 0 ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                  <Sparkles className="h-3 w-3" aria-hidden="true" />
                  Filled from known data
                </span>
              ) : null}
            >
              {autofilledCount > 0 ? (
                <p className="text-xs text-gray-500">
                  {countLabel(autofilledCount, 'price or limit', 'prices and limits')} came from known data for this model. They follow the model you pick until you edit them. Check them before you save.
                </p>
              ) : canFillFromKnownMetadata ? (
                <p className="text-xs text-gray-500">
                  Known prices and limits exist for this model.{' '}
                  <button type="button" onClick={fillFromKnownMetadata} className="font-medium text-brand-primary-ink hover:underline">Fill empty fields</button>
                </p>
              ) : null}
              <PricingFields form={form} onChange={updateForm} />
            </FormPanel>
          )}

          <SettingsList title="More settings" description="Optional. The defaults work for most models.">
            <SettingsRow
              title="Limits and routing"
              description="Requests and tokens per minute, weight, priority, tags, access groups"
              value={limitsSummary}
              hasError={errorSection === 'limits' && !Object.values(capacityErrors).some(Boolean)}
            >
              <LimitsRoutingFields form={form} onChange={updateForm} accessGroupInputRef={accessGroupInputRef} />
            </SettingsRow>
            {mode === 'chat' || mode === 'embedding' ? (
              <SettingsRow
                title="Context and output limits"
                description={mode === 'chat' ? 'Context window, input and output tokens, stream timeout' : 'Context window, input tokens, vector size'}
                value={contextSummary ? `${contextSummary} context` : 'Not set'}
                hasError={Object.values(capacityErrors).some(Boolean)}
              >
                {mode === 'chat'
                  ? <ChatLimitsFields form={form} onChange={updateForm} errors={capacityErrors} />
                  : <EmbeddingLimitsFields form={form} onChange={updateForm} errors={capacityErrors} />}
              </SettingsRow>
            ) : null}
            {mode === 'chat' ? (
              <SettingsRow
                title="Capabilities"
                description="Tools, images, structured outputs and more, for auto-routing"
                value={form.chat_capabilities_declared ? `${capabilityCount} declared` : 'Not declared'}
              >
                <ChatCapabilitiesFields
                  declared={form.chat_capabilities_declared}
                  capabilities={form.chat_capabilities}
                  onDeclaredChange={(declared) => setForm((current) => ({
                    ...current,
                    chat_capabilities_declared: declared,
                    chat_capabilities_cleared: !declared,
                  }))}
                  onCapabilityChange={(key, supported) => setForm((current) => ({
                    ...current,
                    chat_capabilities: { ...current.chat_capabilities, [key]: supported },
                  }))}
                />
              </SettingsRow>
            ) : null}
            {mode === 'chat' || mode === 'embedding' ? (
              <SettingsRow
                title="Batch execution"
                description="How batch jobs send work to this deployment"
                value={mode === 'chat' && form.chat_batching_mode ? modeLabelForBatch(form.chat_batching_mode) : 'Defaults'}
                hasError={errorSection === 'batch'}
              >
                <BatchExecutionFields form={form} onChange={updateForm} />
              </SettingsRow>
            ) : null}
            <SettingsRow
              title="Default parameters"
              description="Values added to, changed in or removed from each request"
              value={paramCount ? countLabel(paramCount, 'rule') : 'None'}
              hasError={errorSection === 'shaping'}
            >
              <ParametersCardBody
                shaping={shaping}
                onChange={updateShaping}
                chat={mode === 'chat'}
                description={defaultParamHint.description}
                details={defaultParamHint.details}
                keyPlaceholder={defaultParamHint.keyPlaceholder}
                valuePlaceholder={defaultParamHint.valuePlaceholder}
              />
            </SettingsRow>
            <SettingsRow
              title={mode === 'chat' ? 'Headers and network' : 'Network'}
              description={mode === 'chat' ? 'Session and forwarded headers, extra headers, outbound proxy' : 'Extra headers and outbound proxy'}
              value={networkCount ? countLabel(networkCount, 'rule') : 'None'}
              hasError={errorSection === 'shaping'}
            >
              <HeadersNetworkCardBody shaping={shaping} onChange={updateShaping} chat={mode === 'chat'} />
            </SettingsRow>
          </SettingsList>
        </div>

        <ModelFormAside
          name={form.model_name}
          provider={form.provider}
          model={form.model}
          mode={mode}
          apiModelId={apiModelIdPreview}
          checklist={checklist}
        />
      </div>

      {validationError || error ? (
        <div role="alert" className="mt-6 space-y-1 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {validationError ? <p>{validationError}</p> : null}
          {error ? <p>{error}</p> : null}
        </div>
      ) : null}

      <div className="mt-6 flex flex-wrap items-center justify-end gap-2 border-t border-gray-200 pt-4">
        {connectionTestBlockedBy ? <span className="mr-auto text-xs text-gray-400">{connectionTestBlockedBy}</span> : null}
        <Button variant="outline" onClick={onCancel} disabled={saving}>Cancel</Button>
        <Button onClick={handleSubmit} loading={saving}>{saving ? 'Saving...' : submitLabel}</Button>
      </div>

      <NamedCredentialCreateDialog
        open={credentialCreateOpen}
        providerPresets={providerPresets}
        initialProvider={credentialProvider}
        initialAccess={namedCredentialDefaultAccess}
        lockProvider={Boolean(credentialProvider)}
        teamOptions={namedCredentialTeamOptions}
        organizationOptions={namedCredentialOrganizationOptions}
        allowPublic={allowPublicNamedCredentials}
        audiencesLoading={namedCredentialAudiencesLoading}
        audiencesError={namedCredentialAudiencesError}
        onRetryAudiences={onRetryNamedCredentialAudiences}
        onClose={() => setCredentialCreateOpen(false)}
        onCreated={handleNamedCredentialCreated}
      />
    </div>
  );
}

function modeLabelForBatch(mode: string): string {
  if (mode === 'disabled') return 'Off';
  if (mode === 'concurrent') return 'Concurrent';
  if (mode === 'sync_microbatch') return 'Sync microbatch';
  return modeShortLabel(mode);
}
