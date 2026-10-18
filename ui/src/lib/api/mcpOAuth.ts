import { apiFetch } from './transport';
import { listMCPConnections } from './mcpConnectionList';

// Contracts for MCP servers with the OAuth 2.0 (per user) auth mode and for
// the Connected accounts page. Field names match the FastAPI responses.

export interface MCPOAuthDiscovery {
  authorization_server: string;
  found_from: string;
  metadata_url: string;
  authorization_endpoint: string;
  token_endpoint: string;
  resource: string;
  registration_endpoint?: string | null;
  revocation_endpoint?: string | null;
  scopes_supported: string[];
  code_challenge_methods_supported: string[];
  token_endpoint_auth_methods_supported: string[];
  checked_at?: string | null;
  pkce_s256_supported: boolean;
  dynamic_registration_supported: boolean;
}

export interface MCPOAuthServerSettings {
  client_id: string | null;
  client_id_source: 'manual' | 'dynamic' | null;
  client_secret_set: boolean;
  scopes: string[];
  discovery: MCPOAuthDiscovery | null;
  discovery_error: string | null;
}

export interface MCPOAuthGatewaySettings {
  redirect_url: string | null;
  public_base_url_configured: boolean;
  encryption_configured: boolean;
}

export interface MCPOAuthDiscoveryPreview {
  discovery: MCPOAuthDiscovery | null;
  discovery_error: string | null;
}

export type MCPConnectionStatus = 'connected' | 'needs_reconnect' | 'not_connected';

export interface MCPConnectionRow {
  server_id: string;
  server_key: string;
  server_name: string;
  status: MCPConnectionStatus;
  status_reason: string | null;
  account_label: string | null;
  scopes: string[];
  expires_at: string | null;
  renews_automatically: boolean;
  connected_at: string | null;
  connect_available: boolean;
}

export interface MCPConnectionList {
  data: MCPConnectionRow[];
  account_connectable: boolean;
}

export interface MCPDisconnectResult {
  deleted: boolean;
  revoked: boolean;
  server_key: string;
}

const serverKeyPath = (serverKey: string) => encodeURIComponent(serverKey);

export const mcpOAuth = {
  settings: (signal?: AbortSignal) =>
    apiFetch<MCPOAuthGatewaySettings>('/ui/api/mcp-oauth/settings', { signal }),
  previewDiscovery: (baseUrl: string, signal?: AbortSignal) =>
    apiFetch<MCPOAuthDiscoveryPreview>('/ui/api/mcp-servers/oauth/discover', {
      method: 'POST',
      json: { base_url: baseUrl },
      signal,
    }),
  // Returns the updated server, with the new discovery result in `oauth`.
  runDiscovery: (serverId: string) =>
    apiFetch<{ oauth?: MCPOAuthServerSettings | null }>(
      `/ui/api/mcp-servers/${encodeURIComponent(serverId)}/oauth/discovery`,
      { method: 'POST' },
    ),
  listConnections: listMCPConnections,
  connect: (serverKey: string) =>
    apiFetch<{ authorization_url: string }>(`/ui/api/mcp-connections/${serverKeyPath(serverKey)}/connect`, {
      method: 'POST',
    }),
  disconnect: (serverKey: string) =>
    apiFetch<MCPDisconnectResult>(`/ui/api/mcp-connections/${serverKeyPath(serverKey)}`, {
      method: 'DELETE',
    }),
};
