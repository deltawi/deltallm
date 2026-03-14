import type { ChangeEvent } from 'react';

export type MCPAuthMode = 'none' | 'bearer' | 'basic' | 'header_map';

export interface MCPServerFormValues {
  server_key: string;
  name: string;
  description: string;
  owner_scope_type: 'global' | 'organization';
  owner_scope_id: string;
  base_url: string;
  transport: 'streamable_http';
  enabled: boolean;
  auth_mode: MCPAuthMode;
  bearer_token: string;
  basic_username: string;
  basic_password: string;
  header_map_json: string;
  forwarded_headers_allowlist: string;
  request_timeout_ms: string;
}

export const EMPTY_MCP_SERVER_FORM: MCPServerFormValues = {
  server_key: '',
  name: '',
  description: '',
  owner_scope_type: 'global',
  owner_scope_id: '',
  base_url: '',
  transport: 'streamable_http',
  enabled: true,
  auth_mode: 'none',
  bearer_token: '',
  basic_username: '',
  basic_password: '',
  header_map_json: '{\n  "Authorization": "Bearer <token>"\n}',
  forwarded_headers_allowlist: '',
  request_timeout_ms: '30000',
};

const inputClass =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

const textareaClass =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500';

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
}

export function buildMCPServerPayload(form: MCPServerFormValues) {
  const allowlist = form.forwarded_headers_allowlist
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);

  let auth_config: Record<string, unknown> = {};
  if (form.auth_mode === 'bearer') {
    auth_config = { token: form.bearer_token.trim() };
  } else if (form.auth_mode === 'basic') {
    auth_config = {
      username: form.basic_username.trim(),
      password: form.basic_password,
    };
  } else if (form.auth_mode === 'header_map') {
    auth_config = { headers: form.header_map_json.trim() ? JSON.parse(form.header_map_json) : {} };
  }

  return {
    server_key: form.server_key.trim().toLowerCase(),
    name: form.name.trim(),
    description: form.description.trim() || null,
    owner_scope_type: form.owner_scope_type,
    owner_scope_id: form.owner_scope_type === 'organization' ? form.owner_scope_id.trim() : null,
    base_url: form.base_url.trim(),
    transport: form.transport,
    enabled: form.enabled,
    auth_mode: form.auth_mode,
    auth_config,
    forwarded_headers_allowlist: allowlist,
    request_timeout_ms: Number(form.request_timeout_ms || '30000'),
  };
}

export function formFromMCPServer(server: {
  server_key: string;
  name: string;
  description?: string | null;
  owner_scope_type: 'global' | 'organization';
  owner_scope_id?: string | null;
  base_url: string;
  transport: 'streamable_http';
  enabled: boolean;
  auth_mode: MCPAuthMode;
  auth_config?: Record<string, unknown> | null;
  forwarded_headers_allowlist?: string[] | null;
  request_timeout_ms: number;
}): MCPServerFormValues {
  const auth = server.auth_config || {};
  return {
    server_key: server.server_key,
    name: server.name,
    description: server.description || '',
    owner_scope_type: server.owner_scope_type || 'global',
    owner_scope_id: server.owner_scope_id || '',
    base_url: server.base_url,
    transport: server.transport,
    enabled: server.enabled,
    auth_mode: server.auth_mode,
    bearer_token: typeof auth.token === 'string' ? auth.token : '',
    basic_username: typeof auth.username === 'string' ? auth.username : '',
    basic_password: typeof auth.password === 'string' ? auth.password : '',
    header_map_json: JSON.stringify((auth.headers as Record<string, unknown>) || {}, null, 2),
    forwarded_headers_allowlist: (server.forwarded_headers_allowlist || []).join(', '),
    request_timeout_ms: String(server.request_timeout_ms || 30000),
  };
}

export default function MCPServerForm({
  value,
  onChange,
  disableServerKey = false,
  disabled = false,
  ownerScopeOptions = [],
  lockOwnerScopeType = false,
  disableOwnerScopeId = false,
}: MCPServerFormProps) {
  const inputDisabledClass = 'disabled:bg-gray-50 disabled:text-gray-500';
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Server Key *</label>
          <input
            value={value.server_key}
            onChange={(event) => onChange({ ...value, server_key: textValue(event) })}
            placeholder="docs"
            data-autofocus={!disableServerKey ? 'true' : undefined}
            disabled={disableServerKey || disabled}
            className={`${inputClass} ${inputDisabledClass}`}
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
            className={`${inputClass} ${inputDisabledClass}`}
          />
        </div>
      </div>

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
            className={`${inputClass} ${inputDisabledClass}`}
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
              className={`${inputClass} ${inputDisabledClass}`}
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
          className={`${inputClass} ${inputDisabledClass}`}
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
            className={`${inputClass} ${inputDisabledClass}`}
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
            className={`${inputClass} ${inputDisabledClass}`}
          >
            <option value="none">None</option>
            <option value="bearer">Bearer</option>
            <option value="basic">Basic</option>
            <option value="header_map">Header Map</option>
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
            className={`${inputClass} ${inputDisabledClass}`}
          />
        </div>
      </div>

      {value.auth_mode === 'bearer' && (
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Bearer Token *</label>
          <input
            type="password"
            value={value.bearer_token}
            onChange={(event) => onChange({ ...value, bearer_token: textValue(event) })}
            placeholder="token"
            disabled={disabled}
            className={`${inputClass} ${inputDisabledClass}`}
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
              disabled={disabled}
              className={`${inputClass} ${inputDisabledClass}`}
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Password *</label>
            <input
              type="password"
              value={value.basic_password}
              onChange={(event) => onChange({ ...value, basic_password: textValue(event) })}
              disabled={disabled}
              className={`${inputClass} ${inputDisabledClass}`}
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
            disabled={disabled}
            className={`${textareaClass} ${inputDisabledClass}`}
          />
        </div>
      )}

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Forwarded Headers Allowlist</label>
        <input
          value={value.forwarded_headers_allowlist}
          onChange={(event) => onChange({ ...value, forwarded_headers_allowlist: textValue(event) })}
          placeholder="authorization, x-api-key"
          disabled={disabled}
          className={`${inputClass} ${inputDisabledClass}`}
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
