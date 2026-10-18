import { useState, type KeyboardEvent } from 'react';
import { AlertTriangle, Check, Copy, RefreshCw, X } from 'lucide-react';
import Button from '../Button';
import StatusBadge from '../StatusBadge';
import { useApi } from '../../lib/hooks';
import { errorMessage } from '../../lib/errorMessage';
import { fmtUtcDateTime } from '../../lib/format';
import { parseScopes, toggleScope } from '../../lib/mcpConnections';
import {
  mcpOAuth,
  type MCPOAuthDiscovery,
  type MCPOAuthServerSettings,
} from '../../lib/api/mcpOAuth';
import type { MCPServerFormValues } from '../../lib/mcpServerForm';

const fieldClass =
  'control-field w-full rounded-lg border border-gray-300 bg-white px-3 focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-gray-50 disabled:text-gray-500';
const labelClass = 'mb-1 block text-sm font-medium text-gray-700';
const hintClass = 'mt-1 text-xs text-gray-500';

interface DiscoveryState {
  discovery: MCPOAuthDiscovery | null;
  error: string | null;
}

interface MCPOAuthFieldsProps {
  value: MCPServerFormValues;
  onChange: (next: MCPServerFormValues) => void;
  disabled?: boolean;
  // Stored settings of an existing server. Absent while the server is created.
  oauth?: MCPOAuthServerSettings | null;
  serverId?: string;
}

function CopyField({ id, value }: { id: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex gap-2">
      <input id={id} value={value} readOnly className={`${fieldClass} bg-gray-50 font-mono`} />
      <Button
        variant="outline"
        size="icon"
        aria-label="Copy redirect URL"
        title="Copy redirect URL"
        onClick={() => {
          navigator.clipboard?.writeText(value).then(
            () => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            },
            () => undefined,
          );
        }}
      >
        {copied ? <Check className="h-4 w-4 text-emerald-600" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
      </Button>
    </div>
  );
}

function ScopeEditor({
  value,
  suggestions,
  disabled,
  onChange,
}: {
  value: string;
  suggestions: string[];
  disabled?: boolean;
  onChange: (next: string) => void;
}) {
  const [draft, setDraft] = useState('');
  const scopes = parseScopes(value);
  const addDraft = () => {
    const added = parseScopes(draft);
    if (added.length === 0) return;
    onChange(parseScopes(`${value} ${added.join(' ')}`).join(' '));
    setDraft('');
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ' ' || event.key === ',') {
      event.preventDefault();
      addDraft();
    } else if (event.key === 'Backspace' && !draft && scopes.length > 0) {
      onChange(scopes.slice(0, -1).join(' '));
    }
  };
  const unused = suggestions.filter((scope) => !scopes.includes(scope));
  return (
    <div className="space-y-2">
      <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-lg border border-gray-300 bg-white p-1.5 focus-within:ring-2 focus-within:ring-brand-primary">
        {scopes.map((scope) => (
          <span key={scope} className="inline-flex items-center gap-1 rounded-md bg-gray-100 pl-2 font-mono text-xs text-gray-700">
            {scope}
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove scope ${scope}`}
              disabled={disabled}
              onClick={() => onChange(toggleScope(value, scope))}
              className="shadow-none"
            >
              <X className="h-3 w-3" aria-hidden="true" />
            </Button>
          </span>
        ))}
        <input
          id="mcp-oauth-scopes"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={addDraft}
          disabled={disabled}
          placeholder={scopes.length ? 'Add scope' : 'Type a scope and press Enter'}
          className="control-field-md min-w-[8rem] flex-1 border-0 bg-transparent px-1 focus:outline-none focus:ring-0"
        />
      </div>
      {unused.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-gray-500">The server supports:</span>
          {unused.map((scope) => (
            <Button
              key={scope}
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => onChange(toggleScope(value, scope))}
              className="font-mono"
            >
              + {scope}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function DiscoveryPanel({
  state,
  running,
  canRun,
  onRun,
}: {
  state: DiscoveryState | null;
  running: boolean;
  canRun: boolean;
  onRun: () => void;
}) {
  const discovery = state?.discovery ?? null;
  const status = discovery
    ? { status: 'healthy', label: 'Found' }
    : state?.error
      ? { status: 'unhealthy', label: 'Failed' }
      : { status: 'disabled', label: 'Not checked' };
  const facts: Array<[string, string]> = discovery
    ? [
        ['Authorization server', discovery.authorization_server],
        ['Found from', discovery.found_from],
        ['Authorization endpoint', discovery.authorization_endpoint],
        ['Token endpoint', discovery.token_endpoint],
        ['Resource', discovery.resource],
        ['PKCE (S256)', discovery.pkce_s256_supported ? 'Supported' : 'Not declared'],
        ['Client registration', discovery.dynamic_registration_supported ? 'Supported' : 'Not supported'],
        ['Checked', discovery.checked_at ? fmtUtcDateTime(discovery.checked_at) : '—'],
      ]
    : [];
  return (
    <section aria-labelledby="mcp-oauth-discovery-title" className="rounded-xl border border-gray-200 bg-gray-50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h4 id="mcp-oauth-discovery-title" className="text-sm font-semibold text-gray-900">Discovery</h4>
          <StatusBadge status={status.status} label={status.label} />
        </div>
        <Button variant="outline" size="sm" onClick={onRun} loading={running} disabled={!canRun}>
          <RefreshCw className={`h-3.5 w-3.5 ${running ? 'motion-safe:animate-spin' : ''}`} aria-hidden="true" />
          {state ? 'Run again' : 'Run discovery'}
        </Button>
      </div>
      {!state ? (
        <p className={hintClass}>
          The gateway reads the OAuth metadata of the server (RFC 9728, then RFC 8414 or OpenID configuration).
          Enter the base URL, then run discovery. Discovery also runs when you save.
        </p>
      ) : null}
      {state?.error ? (
        <p role="alert" className="mt-2 text-sm text-red-700">{state.error}</p>
      ) : null}
      {facts.length > 0 ? (
        <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
          {facts.map(([label, text]) => (
            <div key={label} className="min-w-0">
              <dt className="text-xs text-gray-500">{label}</dt>
              <dd className="break-all text-sm text-gray-900">{text}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {discovery && !discovery.pkce_s256_supported ? (
        <p className="mt-3 flex items-start gap-2 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          The server does not declare PKCE with S256. Users cannot connect until it does.
        </p>
      ) : null}
    </section>
  );
}

export default function MCPOAuthFields({ value, onChange, disabled = false, oauth, serverId }: MCPOAuthFieldsProps) {
  const { data: gateway } = useApi((signal) => mcpOAuth.settings(signal), []);
  const [discoveryState, setDiscoveryState] = useState<DiscoveryState | null>(
    oauth && (oauth.discovery || oauth.discovery_error)
      ? { discovery: oauth.discovery, error: oauth.discovery_error }
      : null,
  );
  const [running, setRunning] = useState(false);
  const baseUrl = value.base_url.trim();

  const runDiscovery = async () => {
    if (running) return;
    setRunning(true);
    try {
      if (serverId) {
        const updated = await mcpOAuth.runDiscovery(serverId);
        setDiscoveryState({
          discovery: updated.oauth?.discovery ?? null,
          error: updated.oauth?.discovery_error ?? null,
        });
      } else {
        const preview = await mcpOAuth.previewDiscovery(baseUrl);
        setDiscoveryState({ discovery: preview.discovery, error: preview.discovery_error });
      }
    } catch (error) {
      setDiscoveryState({ discovery: null, error: errorMessage(error, 'Discovery failed.') });
    } finally {
      setRunning(false);
    }
  };

  const dynamicClientId = oauth?.client_id_source === 'dynamic' ? oauth.client_id : null;
  const suggestions = discoveryState?.discovery?.scopes_supported ?? [];

  return (
    <div className="space-y-4 rounded-xl border border-gray-200 p-4">
      <p className="text-sm text-gray-600">
        Each user connects an own account on the Connected accounts page. Tool calls from a key that the user owns use
        the token of that user.
      </p>

      {gateway && !gateway.encryption_configured ? (
        <p role="alert" className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          Set general_settings.mcp_oauth_encryption_key before you save this server. The gateway encrypts user tokens with this key.
        </p>
      ) : null}
      {gateway && !gateway.public_base_url_configured ? (
        <p role="alert" className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          Set general_settings.email_base_url to the public gateway URL. The redirect URL and the connect links use it.
        </p>
      ) : null}

      <DiscoveryPanel
        state={discoveryState}
        running={running}
        canRun={!disabled && (Boolean(serverId) || /^https?:\/\//i.test(baseUrl))}
        onRun={() => { void runDiscovery(); }}
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="mcp-oauth-client-id" className={labelClass}>Client ID</label>
          <input
            id="mcp-oauth-client-id"
            value={value.oauth_client_id}
            onChange={(event) => onChange({ ...value, oauth_client_id: event.target.value })}
            placeholder="Empty: register a client automatically"
            autoComplete="off"
            disabled={disabled}
            className={fieldClass}
          />
          <p className={hintClass}>
            {dynamicClientId
              ? `Registered automatically: ${dynamicClientId}. Enter a client ID to use your own OAuth app.`
              : 'Leave empty to use dynamic client registration (RFC 7591).'}
          </p>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between gap-2">
            <label htmlFor="mcp-oauth-client-secret" className="block text-sm font-medium text-gray-700">Client secret</label>
            {oauth ? (
              <StatusBadge status={oauth.client_secret_set ? 'active' : 'disabled'} label={oauth.client_secret_set ? 'Set' : 'Not set'} />
            ) : null}
          </div>
          <input
            id="mcp-oauth-client-secret"
            type="password"
            value={value.oauth_client_secret}
            onChange={(event) => onChange({ ...value, oauth_client_secret: event.target.value })}
            placeholder={oauth?.client_secret_set ? 'Leave empty to keep the stored secret' : 'Optional'}
            autoComplete="new-password"
            disabled={disabled || value.oauth_clear_client_secret}
            className={fieldClass}
          />
          {oauth?.client_secret_set ? (
            <label className="mt-2 flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={value.oauth_clear_client_secret}
                onChange={(event) => onChange({ ...value, oauth_clear_client_secret: event.target.checked, oauth_client_secret: '' })}
                disabled={disabled}
                className="rounded border-gray-300"
              />
              Remove the stored secret
            </label>
          ) : (
            <p className={hintClass}>The gateway stores the secret encrypted and never shows it again.</p>
          )}
        </div>
      </div>

      <div>
        <label htmlFor="mcp-oauth-scopes" className={labelClass}>Scopes</label>
        <ScopeEditor
          value={value.oauth_scopes}
          suggestions={suggestions}
          disabled={disabled}
          onChange={(next) => onChange({ ...value, oauth_scopes: next })}
        />
      </div>

      <div>
        <label htmlFor="mcp-oauth-redirect-url" className={labelClass}>Redirect URL</label>
        {gateway?.redirect_url ? (
          <CopyField id="mcp-oauth-redirect-url" value={gateway.redirect_url} />
        ) : (
          <input id="mcp-oauth-redirect-url" value="" placeholder="Not available" readOnly className={`${fieldClass} bg-gray-50`} />
        )}
        <p className={hintClass}>Add this URL to the OAuth app at the provider. You cannot change it here.</p>
      </div>
    </div>
  );
}
