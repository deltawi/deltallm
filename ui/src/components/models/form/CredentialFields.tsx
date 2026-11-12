import { useId } from 'react';
import { KeyRound } from 'lucide-react';
import type { NamedCredential } from '../../../lib/api';
import {
  DEFAULT_CUSTOM_AUTH_HEADER_FORMAT,
  DEFAULT_CUSTOM_AUTH_HEADER_NAME,
  providerDisplayName,
} from '../../../lib/providers';
import Button from '../../Button';
import SegmentedControl from '../../SegmentedControl';
import type { ModelFormValues } from '../../modelFormShared';
import { Disclosure, FieldError, FieldLabel } from './formPrimitives';
import { HELP_CLASS, fieldClass } from './formStyles';

const CREATE_NEW_NAMED_CREDENTIAL = '__create_new_named_credential__';

type CredentialField = 'api_key' | 'api_base' | 'api_version';

interface CredentialFieldsProps {
  form: ModelFormValues;
  allowInlineCredentials: boolean;
  supportsCustomAuth: boolean;
  credentials: NamedCredential[];
  selectedCredential: NamedCredential | null;
  apiBasePlaceholder: string;
  apiBaseHelp: string;
  apiKeyHint?: string;
  errors: { named_credential_id?: string; api_base?: string };
  onSourceChange: (source: 'inline' | 'named') => void;
  onSelectCredential: (credentialId: string) => void;
  onCreateCredential: () => void;
  onReplaceKeptCredential: () => void;
  onCredentialFieldChange: (field: CredentialField, value: string) => void;
  onChange: (patch: Partial<ModelFormValues>) => void;
}

function credentialSummary(credential: NamedCredential): string {
  const config = credential.connection_config || {};
  const where = config.api_base
    ? `API base ${String(config.api_base)}`
    : config.region
      ? `Region ${String(config.region)}`
      : 'The credential holds the connection details';
  const usage = credential.usage_count || 0;
  return `${where}. Used by ${usage} ${usage === 1 ? 'model' : 'models'}.`;
}

export default function CredentialFields({
  form,
  allowInlineCredentials,
  supportsCustomAuth,
  credentials,
  selectedCredential,
  apiBasePlaceholder,
  apiBaseHelp,
  apiKeyHint,
  errors,
  onSourceChange,
  onSelectCredential,
  onCreateCredential,
  onReplaceKeptCredential,
  onCredentialFieldChange,
  onChange,
}: CredentialFieldsProps) {
  const id = useId();
  const named = form.credential_source === 'named';
  const timeoutField = (
    <div className="grid gap-4 sm:grid-cols-2">
      {!named ? (
        <div>
          <FieldLabel htmlFor={`${id}-version`} label="API version" />
          <input id={`${id}-version`} value={form.api_version} onChange={(event) => onCredentialFieldChange('api_version', event.target.value)} placeholder="e.g. 2024-02-01 (Azure)" className={fieldClass()} />
        </div>
      ) : null}
      <div>
        <FieldLabel htmlFor={`${id}-timeout`} label="Timeout (s)" />
        <input id={`${id}-timeout`} type="number" min="1" value={form.timeout} onChange={(event) => onChange({ timeout: event.target.value })} placeholder="300" className={fieldClass()} />
      </div>
      {!named && supportsCustomAuth ? (
        <>
          <div>
            <FieldLabel htmlFor={`${id}-auth-name`} label="Auth header name" />
            <input id={`${id}-auth-name`} value={form.auth_header_name} onChange={(event) => onChange({ auth_header_name: event.target.value })} placeholder={DEFAULT_CUSTOM_AUTH_HEADER_NAME} className={fieldClass()} />
          </div>
          <div>
            <FieldLabel htmlFor={`${id}-auth-format`} label="Auth header format" />
            <input id={`${id}-auth-format`} value={form.auth_header_format} onChange={(event) => onChange({ auth_header_format: event.target.value })} placeholder={DEFAULT_CUSTOM_AUTH_HEADER_FORMAT} className={fieldClass()} />
          </div>
          <p className={`${HELP_CLASS} sm:col-span-2`}>
            Optional, for OpenAI-compatible providers. Only the <code>{'{api_key}'}</code> placeholder is supported.
          </p>
        </>
      ) : null}
    </div>
  );

  return (
    <>
      {allowInlineCredentials ? (
        <div>
          <FieldLabel label="Credential" required />
          <SegmentedControl
            label="Credential source"
            value={form.credential_source}
            onChange={onSourceChange}
            items={[
              { value: 'named', label: 'Saved credential', icon: KeyRound },
              { value: 'inline', label: 'API key on this model' },
            ]}
          />
          <p className={HELP_CLASS}>
            {named
              ? 'Recommended. Many models can share one saved credential, and you rotate its key in one place.'
              : 'Only this model uses this key.'}
          </p>
        </div>
      ) : null}

      {named ? (
        form.keep_existing_named_credential ? (
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 text-sm">
            <div className="font-medium text-gray-900">Owner-managed credential</div>
            <p className="mt-1 text-xs text-gray-500">
              This model keeps its private credential. You can change other settings without seeing or changing it.
            </p>
            <Button size="sm" variant="outline" className="mt-3" onClick={onReplaceKeptCredential}>
              Replace with a credential I can use
            </Button>
          </div>
        ) : (
          <div>
            <FieldLabel htmlFor={`${id}-named`} label="Saved credential" required />
            <select
              id={`${id}-named`}
              value={form.named_credential_id}
              onChange={(event) => (event.target.value === CREATE_NEW_NAMED_CREDENTIAL ? onCreateCredential() : onSelectCredential(event.target.value))}
              aria-invalid={Boolean(errors.named_credential_id) || undefined}
              aria-describedby={errors.named_credential_id ? `${id}-named-error` : `${id}-named-help`}
              className={fieldClass(Boolean(errors.named_credential_id))}
            >
              <option value="">Select a credential</option>
              <option value={CREATE_NEW_NAMED_CREDENTIAL}>+ Create new credential...</option>
              {credentials.map((credential) => (
                <option key={credential.credential_id} value={credential.credential_id}>{credential.name}</option>
              ))}
            </select>
            <FieldError id={`${id}-named-error`} message={errors.named_credential_id} />
            <p id={`${id}-named-help`} className={HELP_CLASS}>
              {selectedCredential
                ? credentialSummary(selectedCredential)
                : form.provider && credentials.length === 0
                  ? `No saved credentials for ${providerDisplayName(form.provider)} yet. Choose Create new credential.`
                  : 'The credential holds the API key and the API base URL.'}
            </p>
          </div>
        )
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <FieldLabel htmlFor={`${id}-key`} label="API key" />
            <input
              id={`${id}-key`}
              type="password"
              autoComplete="off"
              value={form.api_key}
              onChange={(event) => onCredentialFieldChange('api_key', event.target.value)}
              placeholder={form.inline_credentials_present && !form.clear_inline_api_key ? 'Stored key. Leave empty to keep it.' : 'sk-...'}
              className={`${fieldClass()} font-mono`}
            />
            {apiKeyHint && !form.inline_credentials_present ? <p className={HELP_CLASS}>{apiKeyHint}</p> : null}
            {form.inline_credentials_present ? (
              <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs">
                {form.clear_inline_api_key
                  ? <span className="text-red-600">The stored key is removed when you save.</span>
                  : <span className="text-gray-500">A key is stored. Leave the field empty to keep it.</span>}
                {!form.clear_inline_api_key ? (
                  <button type="button" onClick={() => onChange({ api_key: '', clear_inline_api_key: true })} className="font-medium text-red-600 hover:text-red-700">
                    Clear stored key
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
          <div>
            <FieldLabel htmlFor={`${id}-base`} label="API base URL" required />
            <input
              id={`${id}-base`}
              value={form.api_base}
              onChange={(event) => onCredentialFieldChange('api_base', event.target.value)}
              placeholder={apiBasePlaceholder}
              aria-invalid={Boolean(errors.api_base) || undefined}
              aria-describedby={errors.api_base ? `${id}-base-error` : `${id}-base-help`}
              className={`${fieldClass(Boolean(errors.api_base))} font-mono`}
            />
            <FieldError id={`${id}-base-error`} message={errors.api_base} />
            <p id={`${id}-base-help`} className={HELP_CLASS}>{apiBaseHelp}</p>
          </div>
        </div>
      )}

      <Disclosure
        label={named ? 'Timeout' : supportsCustomAuth ? 'API version, timeout and auth header' : 'API version and timeout'}
        defaultOpen={Boolean(form.timeout || form.api_version || form.auth_header_name || form.auth_header_format)}
      >
        {timeoutField}
      </Disclosure>
    </>
  );
}
