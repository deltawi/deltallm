import type { MCPOAuthServerSettings } from './api/mcpOAuth';
import { parseScopes } from './mcpConnections';

// Form values and form-to-payload conversion for the MCP server editor.

export type MCPAuthMode = 'none' | 'bearer' | 'basic' | 'header_map' | 'oauth2';

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
  oauth_client_id: string;
  oauth_client_secret: string;
  oauth_clear_client_secret: boolean;
  oauth_scopes: string;
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
  oauth_client_id: '',
  oauth_client_secret: '',
  oauth_clear_client_secret: false,
  oauth_scopes: '',
  forwarded_headers_allowlist: '',
  request_timeout_ms: '30000',
};

export function buildMCPServerPayload(
  form: MCPServerFormValues,
  options?: { preserveExistingCredentials?: boolean }
) {
  const allowlist = form.forwarded_headers_allowlist
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);

  const preserveExistingCredentials = Boolean(options?.preserveExistingCredentials);
  let auth_config: Record<string, unknown> | undefined;
  if (form.auth_mode === 'bearer') {
    const token = form.bearer_token.trim();
    if (!preserveExistingCredentials || token) {
      auth_config = { token };
    }
  } else if (form.auth_mode === 'basic') {
    const username = form.basic_username.trim();
    const password = form.basic_password;
    if (!preserveExistingCredentials || username || password) {
      auth_config = {
        username,
        password,
      };
    }
  } else if (form.auth_mode === 'header_map') {
    const headersJson = form.header_map_json.trim();
    if (!preserveExistingCredentials || headersJson) {
      auth_config = { headers: headersJson ? JSON.parse(headersJson) : {} };
    }
  } else if (form.auth_mode === 'oauth2') {
    // The client secret is write-only: send it only when the user typed a new one.
    const secret = form.oauth_client_secret.trim();
    auth_config = {
      client_id: form.oauth_client_id.trim(),
      scopes: parseScopes(form.oauth_scopes),
      ...(secret ? { client_secret: secret } : {}),
      ...(form.oauth_clear_client_secret && !secret ? { clear_client_secret: true } : {}),
    };
  } else {
    auth_config = {};
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
    forwarded_headers_allowlist: allowlist,
    request_timeout_ms: Number(form.request_timeout_ms || '30000'),
    ...(auth_config !== undefined ? { auth_config } : {}),
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
  forwarded_headers_allowlist?: string[] | null;
  request_timeout_ms: number;
  oauth?: MCPOAuthServerSettings | null;
}): MCPServerFormValues {
  const oauth = server.oauth;
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
    bearer_token: '',
    basic_username: '',
    basic_password: '',
    header_map_json: '',
    // A client that the gateway registered is not shown as typed input, so an
    // empty field keeps it.
    oauth_client_id: oauth?.client_id_source === 'manual' ? oauth.client_id || '' : '',
    oauth_client_secret: '',
    oauth_clear_client_secret: false,
    oauth_scopes: (oauth?.scopes || []).join(' '),
    forwarded_headers_allowlist: (server.forwarded_headers_allowlist || []).join(', '),
    request_timeout_ms: String(server.request_timeout_ms || 30000),
  };
}
