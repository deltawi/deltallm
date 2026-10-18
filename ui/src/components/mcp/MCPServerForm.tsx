import type { ChangeEvent } from 'react';
import type { MCPOAuthServerSettings } from '../../lib/api/mcpOAuth';
import type { MCPAuthMode, MCPServerFormValues } from '../../lib/mcpServerForm';
import MCPOAuthFields from './MCPOAuthFields';

const inputClass =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary';

const textareaClass =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-primary';

function textValue(event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) {
  return event.target.value;
}

interface MCPServerFormProps {
  value: MCPServerFormValues;
  onChange: (next: MCPServerFormValues) => void;
  disableServerKey?: boolean;
  disabled?: boolean;
  ownerScopeOptions?: Array<{ value: string; label: string }>;
  lockOwnerScopeType?: boolean;
  disableOwnerScopeId?: boolean;
  preserveExistingCredentials?: boolean;
  credentialsConfigured?: boolean;
  showOwnerScope?: boolean;
  // Stored OAuth settings and the server ID, for an existing oauth2 server.
  oauth?: MCPOAuthServerSettings | null;
  serverId?: string;
}

export default function MCPServerForm({
  value,
  onChange,
  disableServerKey = false,
  disabled = false,
  ownerScopeOptions = [],
  lockOwnerScopeType = false,
  disableOwnerScopeId = false,
  preserveExistingCredentials = false,
  credentialsConfigured = false,
  showOwnerScope = true,
  oauth,
  serverId,
}: MCPServerFormProps) {
  const inputDisabledClass = 'disabled:bg-gray-50 disabled:text-gray-500';
  return (
    <div className="space-y-4">
      {showOwnerScope ? <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Server Key *</label>
          <input
            value={value.server_key}
            onChange={(event) => onChange({ ...value, server_key: textValue(event) })}
            placeholder="docs"
            data-autofocus={!disableServerKey ? 'true' : undefined}
            disabled={disableServerKey || disabled}
            className={`${inputClass} control-field ${inputDisabledClass}`}
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Name *</label>
          <input
            value={value.name}
            onChange={(event) => onChange({ ...value, name: textValue(event) })}
            placeholder="Docs MCP"
            data-autofocus={disableServerKey ? 'true' : undefined}
            disabled={disabled}
            className={`${inputClass} control-field ${inputDisabledClass}`}
          />
        </div>
      </div> : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Owner Scope *</label>
          <select
            value={value.owner_scope_type}
            onChange={(event) =>
              onChange({
                ...value,
                owner_scope_type: textValue(event) as 'global' | 'organization',
                owner_scope_id: textValue(event) === 'organization' ? value.owner_scope_id : '',
              })
            }
            disabled={disabled || lockOwnerScopeType}
            className={`${inputClass} control-field ${inputDisabledClass}`}
          >
            <option value="global">Global</option>
            <option value="organization">Organization</option>
          </select>
        </div>
        {value.owner_scope_type === 'organization' ? (
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Organization *</label>
            <select
              value={value.owner_scope_id}
              onChange={(event) => onChange({ ...value, owner_scope_id: textValue(event) })}
              disabled={disabled || disableOwnerScopeId}
              className={`${inputClass} control-field ${inputDisabledClass}`}
            >
              <option value="">Select organization</option>
              {ownerScopeOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
            Global MCP servers are shared infrastructure objects managed across organizations.
          </div>
        )}
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Base URL *</label>
        <input
          value={value.base_url}
          onChange={(event) => onChange({ ...value, base_url: textValue(event) })}
          placeholder="https://mcp.example.com"
          disabled={disabled}
          className={`${inputClass} control-field ${inputDisabledClass}`}
        />
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Description</label>
        <textarea
          value={value.description}
          onChange={(event) => onChange({ ...value, description: textValue(event) })}
          rows={3}
          placeholder="What this MCP server is used for."
          disabled={disabled}
          className={`${inputClass} ${inputDisabledClass}`}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Transport</label>
          <select
            value={value.transport}
            onChange={(event) => onChange({ ...value, transport: textValue(event) as 'streamable_http' })}
            disabled={disabled}
            className={`${inputClass} control-field ${inputDisabledClass}`}
          >
            <option value="streamable_http">Streamable HTTP</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Auth Mode</label>
          <select
            value={value.auth_mode}
            onChange={(event) => onChange({ ...value, auth_mode: textValue(event) as MCPAuthMode })}
            disabled={disabled}
            className={`${inputClass} control-field ${inputDisabledClass}`}
          >
            <option value="none">None</option>
            <option value="bearer">Bearer</option>
            <option value="basic">Basic</option>
            <option value="header_map">Header Map</option>
            <option value="oauth2">OAuth 2.0 (per user)</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Request Timeout (ms)</label>
          <input
            value={value.request_timeout_ms}
            onChange={(event) => onChange({ ...value, request_timeout_ms: textValue(event) })}
            type="number"
            min="1"
            disabled={disabled}
            className={`${inputClass} control-field ${inputDisabledClass}`}
          />
        </div>
      </div>

      {preserveExistingCredentials && value.auth_mode !== 'none' && value.auth_mode !== 'oauth2' ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {credentialsConfigured
            ? 'Credentials are already configured. Leave the secret fields blank to keep the current value, or enter a new value to replace it.'
            : 'Credentials are not configured yet. Enter the secret fields below to enable this auth mode.'}
        </div>
      ) : null}

      {value.auth_mode === 'bearer' && (
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Bearer Token *</label>
          <input
            type="password"
            value={value.bearer_token}
            onChange={(event) => onChange({ ...value, bearer_token: textValue(event) })}
            placeholder={preserveExistingCredentials ? 'Leave blank to keep existing token' : 'token'}
            disabled={disabled}
            className={`${inputClass} control-field ${inputDisabledClass}`}
          />
        </div>
      )}

      {value.auth_mode === 'basic' && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Username *</label>
            <input
              value={value.basic_username}
              onChange={(event) => onChange({ ...value, basic_username: textValue(event) })}
              placeholder={preserveExistingCredentials ? 'Leave blank to keep existing username' : undefined}
              disabled={disabled}
              className={`${inputClass} control-field ${inputDisabledClass}`}
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Password *</label>
            <input
              type="password"
              value={value.basic_password}
              onChange={(event) => onChange({ ...value, basic_password: textValue(event) })}
              placeholder={preserveExistingCredentials ? 'Leave blank to keep existing password' : undefined}
              disabled={disabled}
              className={`${inputClass} control-field ${inputDisabledClass}`}
            />
          </div>
        </div>
      )}

      {value.auth_mode === 'header_map' && (
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Headers JSON *</label>
          <textarea
            value={value.header_map_json}
            onChange={(event) => onChange({ ...value, header_map_json: textValue(event) })}
            rows={6}
            placeholder={
              preserveExistingCredentials
                ? '{\n  "Authorization": "Bearer <new-token>"\n}'
                : '{\n  "Authorization": "Bearer <token>"\n}'
            }
            disabled={disabled}
            className={`${textareaClass} ${inputDisabledClass}`}
          />
        </div>
      )}

      {value.auth_mode === 'oauth2' && (
        <MCPOAuthFields
          // Show the stored discovery result again after a save reloads the server.
          key={`${serverId ?? 'new'}:${oauth?.discovery?.checked_at ?? ''}:${oauth?.discovery_error ?? ''}`}
          value={value}
          onChange={onChange}
          disabled={disabled}
          oauth={oauth}
          serverId={serverId}
        />
      )}

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Forwarded Headers Allowlist</label>
        <input
          value={value.forwarded_headers_allowlist}
          onChange={(event) => onChange({ ...value, forwarded_headers_allowlist: textValue(event) })}
          placeholder="authorization, x-api-key"
          disabled={disabled}
          className={`${inputClass} control-field ${inputDisabledClass}`}
        />
        <p className="mt-1 text-xs text-gray-500">Comma-separated list of header names that clients may forward to this MCP server.</p>
      </div>

      <label className="flex items-center gap-2 text-sm text-gray-700">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(event) => onChange({ ...value, enabled: event.target.checked })}
          disabled={disabled}
          className="rounded border-gray-300"
        />
        Enabled
      </label>
    </div>
  );
}
