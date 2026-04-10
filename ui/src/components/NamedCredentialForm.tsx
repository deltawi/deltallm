import { useEffect, useMemo, useState } from 'react';
import { type NamedCredential, type ProviderPreset } from '../lib/api';
import {
  DEFAULT_CUSTOM_AUTH_HEADER_FORMAT,
  DEFAULT_CUSTOM_AUTH_HEADER_NAME,
  providerDisplayName,
  supportsCustomUpstreamAuthProvider,
} from '../lib/providers';

type SecretField = 'api_key' | 'aws_access_key_id' | 'aws_secret_access_key' | 'aws_session_token';

type NamedCredentialFormValues = {
  name: string;
  provider: string;
  api_key: string;
  api_base: string;
  api_version: string;
  auth_header_name: string;
  auth_header_format: string;
  region: string;
  aws_access_key_id: string;
  aws_secret_access_key: string;
  aws_session_token: string;
  clearedSecrets: Partial<Record<SecretField, boolean>>;
  existingSecrets: Partial<Record<SecretField, boolean>>;
};

type NamedCredentialPayload = {
  name: string;
  provider: string;
  connection_config: Record<string, unknown>;
};

interface NamedCredentialFormProps {
  initialCredential?: NamedCredential | null;
  providerPresets: ProviderPreset[];
  saving?: boolean;
  error?: string | null;
  onSave: (payload: NamedCredentialPayload) => Promise<void>;
  onCancel: () => void;
}

const MASK = '***REDACTED***';
const inputClass = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

function emptyForm(): NamedCredentialFormValues {
  return {
    name: '',
    provider: '',
    api_key: '',
    api_base: '',
    api_version: '',
    auth_header_name: '',
    auth_header_format: '',
    region: '',
    aws_access_key_id: '',
    aws_secret_access_key: '',
    aws_session_token: '',
    clearedSecrets: {},
    existingSecrets: {},
  };
}

function isBedrock(provider: string): boolean {
  return provider === 'bedrock';
}

function secretPresence(credential: NamedCredential | null | undefined): Partial<Record<SecretField, boolean>> {
  const config = credential?.connection_config || {};
  return {
    api_key: config.api_key === MASK,
    aws_access_key_id: config.aws_access_key_id === MASK,
    aws_secret_access_key: config.aws_secret_access_key === MASK,
    aws_session_token: config.aws_session_token === MASK,
  };
}

function formFromCredential(credential: NamedCredential | null | undefined): NamedCredentialFormValues {
  if (!credential) return emptyForm();
  const config = credential.connection_config || {};
  return {
    name: credential.name || '',
    provider: credential.provider || '',
    api_key: '',
    api_base: typeof config.api_base === 'string' ? config.api_base : '',
    api_version: typeof config.api_version === 'string' ? config.api_version : '',
    auth_header_name: typeof config.auth_header_name === 'string' ? config.auth_header_name : '',
    auth_header_format: typeof config.auth_header_format === 'string' ? config.auth_header_format : '',
    region: typeof config.region === 'string' ? config.region : '',
    aws_access_key_id: '',
    aws_secret_access_key: '',
    aws_session_token: '',
    clearedSecrets: {},
    existingSecrets: secretPresence(credential),
  };
}

function hasExistingSecret(values: NamedCredentialFormValues, field: SecretField): boolean {
  return Boolean(values.existingSecrets[field]) && !values.clearedSecrets[field];
}

function buildPayload(values: NamedCredentialFormValues, initialCredential?: NamedCredential | null): NamedCredentialPayload {
  const connectionConfig: Record<string, unknown> = {};
  const trim = (value: string) => value.trim();
  const initialConfig = initialCredential?.connection_config || {};

  const assignOptionalText = (field: 'api_base' | 'api_version' | 'auth_header_name' | 'auth_header_format' | 'region') => {
    const nextValue = trim(values[field]);
    const hadInitial = typeof initialConfig[field] === 'string' && String(initialConfig[field]).trim() !== '';
    if (nextValue) {
      connectionConfig[field] = nextValue;
    } else if (initialCredential && hadInitial) {
      connectionConfig[field] = null;
    }
  };

  const assignSecretField = (field: SecretField) => {
    const nextValue = values[field];
    if (values.clearedSecrets[field]) {
      connectionConfig[field] = null;
      return;
    }
    if (nextValue.trim()) {
      connectionConfig[field] = nextValue.trim();
    }
  };

  if (isBedrock(values.provider)) {
    assignOptionalText('region');
    assignOptionalText('api_base');
    assignSecretField('aws_access_key_id');
    assignSecretField('aws_secret_access_key');
    assignSecretField('aws_session_token');
  } else {
    assignOptionalText('api_base');
    assignOptionalText('api_version');
    assignSecretField('api_key');
    if (supportsCustomUpstreamAuthProvider(values.provider)) {
      assignOptionalText('auth_header_name');
      assignOptionalText('auth_header_format');
    }
  }

  return {
    name: trim(values.name),
    provider: trim(values.provider),
    connection_config: connectionConfig,
  };
}

export default function NamedCredentialForm({
  initialCredential = null,
  providerPresets,
  saving = false,
  error = null,
  onSave,
  onCancel,
}: NamedCredentialFormProps) {
  const [form, setForm] = useState<NamedCredentialFormValues>(() => formFromCredential(initialCredential));
  const [validationError, setValidationError] = useState<string | null>(null);
  const editing = Boolean(initialCredential);
  const supportsCustomAuth = supportsCustomUpstreamAuthProvider(form.provider);

  useEffect(() => {
    setForm(formFromCredential(initialCredential));
    setValidationError(null);
  }, [initialCredential]);

  const selectedPreset = useMemo(
    () => providerPresets.find((preset) => preset.provider === form.provider) || null,
    [form.provider, providerPresets],
  );

  const setSecretField = (field: SecretField, value: string) => {
    setForm((current) => ({
      ...current,
      [field]: value,
      clearedSecrets: { ...current.clearedSecrets, [field]: false },
    }));
  };

  const markSecretForClear = (field: SecretField) => {
    setForm((current) => ({
      ...current,
      [field]: '',
      clearedSecrets: { ...current.clearedSecrets, [field]: true },
    }));
  };

  const handleSave = async () => {
    const name = form.name.trim();
    const provider = form.provider.trim();
    if (!name) {
      setValidationError('Name is required.');
      return;
    }
    if (!provider) {
      setValidationError('Provider is required.');
      return;
    }
    setValidationError(null);
    await onSave(buildPayload(form, initialCredential));
  };

  const secretField = (
    field: SecretField,
    label: string,
    placeholder: string,
  ) => (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700">{label}</label>
      <input
        type="password"
        value={form[field]}
        onChange={(e) => setSecretField(field, e.target.value)}
        placeholder={placeholder}
        className={inputClass}
      />
      <div className="mt-1 flex items-center gap-3 text-xs">
        {hasExistingSecret(form, field) ? (
          <span className="text-gray-500">Stored value present. Leave blank to keep it.</span>
        ) : null}
        {form.clearedSecrets[field] ? (
          <span className="text-red-600">Will be cleared on save.</span>
        ) : null}
        {editing && (hasExistingSecret(form, field) || form.clearedSecrets[field]) ? (
          <button
            type="button"
            onClick={() => markSecretForClear(field)}
            className="font-medium text-red-600 hover:text-red-700"
          >
            Clear stored value
          </button>
        ) : null}
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      {(validationError || error) ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {validationError || error}
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Name</label>
          <input
            value={form.name}
            onChange={(e) => setForm((current) => ({ ...current, name: e.target.value }))}
            placeholder="OpenAI production"
            className={inputClass}
            data-autofocus="true"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Provider</label>
          <select
            value={form.provider}
            onChange={(e) => setForm((current) => ({ ...current, provider: e.target.value }))}
            className={inputClass}
            disabled={editing}
          >
            <option value="">Select provider</option>
            {providerPresets.map((preset) => (
              <option key={preset.provider} value={preset.provider}>
                {providerDisplayName(preset.provider)}
              </option>
            ))}
          </select>
          {editing ? <p className="mt-1 text-xs text-gray-400">Provider cannot be changed after creation.</p> : null}
        </div>
      </div>

      {!isBedrock(form.provider) && (
        <>
          {secretField('api_key', 'API Key', 'sk-...')}
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">API Base URL</label>
              <input
                value={form.api_base}
                onChange={(e) => setForm((current) => ({ ...current, api_base: e.target.value }))}
                placeholder={selectedPreset?.api_base || 'https://your-provider.example/v1'}
                className={inputClass}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">API Version</label>
              <input
                value={form.api_version}
                onChange={(e) => setForm((current) => ({ ...current, api_version: e.target.value }))}
                placeholder="Optional"
                className={inputClass}
              />
            </div>
          </div>
          {supportsCustomAuth ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Auth Header Name</label>
                <input
                  value={form.auth_header_name}
                  onChange={(e) => setForm((current) => ({ ...current, auth_header_name: e.target.value }))}
                  placeholder={DEFAULT_CUSTOM_AUTH_HEADER_NAME}
                  className={inputClass}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Auth Header Format</label>
                <input
                  value={form.auth_header_format}
                  onChange={(e) => setForm((current) => ({ ...current, auth_header_format: e.target.value }))}
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

      {isBedrock(form.provider) && (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Region</label>
              <input
                value={form.region}
                onChange={(e) => setForm((current) => ({ ...current, region: e.target.value }))}
                placeholder="us-east-1"
                className={inputClass}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">API Base URL</label>
              <input
                value={form.api_base}
                onChange={(e) => setForm((current) => ({ ...current, api_base: e.target.value }))}
                placeholder={selectedPreset?.api_base || 'https://bedrock-runtime.{region}.amazonaws.com'}
                className={inputClass}
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {secretField('aws_access_key_id', 'AWS Access Key ID', 'AKIA...')}
            {secretField('aws_secret_access_key', 'AWS Secret Access Key', 'AWS secret')}
          </div>
          {secretField('aws_session_token', 'AWS Session Token', 'Optional session token')}
        </>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => { void handleSave(); }}
          disabled={saving}
          className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? 'Saving...' : editing ? 'Save Changes' : 'Create Credential'}
        </button>
      </div>
    </div>
  );
}
