import { apiFetch, withQuery } from './transport';
import type { Paginated } from './pagination';

export type MCPBundleOwnerScopeType = 'global' | 'organization' | 'team';

export interface MCPBundleMember {
  mcp_server_id: string;
  server_key: string;
  server_name: string;
  server_enabled: boolean;
  all_tools: boolean;
  /** null when the member uses all tools of the server. */
  tool_names: string[] | null;
  name_prefix: string | null;
  tool_count: number;
  server_tool_count: number;
}

export interface MCPBundle {
  mcp_bundle_id: string;
  bundle_key: string;
  name: string;
  description: string | null;
  owner_scope_type: MCPBundleOwnerScopeType;
  owner_scope_id: string | null;
  enabled: boolean;
  members: MCPBundleMember[];
  tool_count: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  capabilities: { can_mutate: boolean };
}

export interface MCPBundleMemberInput {
  mcp_server_id: string;
  all_tools: boolean;
  tool_names?: string[];
  name_prefix?: string | null;
}

export interface MCPBundleCreateInput {
  bundle_key: string;
  name: string;
  description?: string | null;
  owner_scope_type: MCPBundleOwnerScopeType;
  owner_scope_id?: string | null;
  enabled?: boolean;
  members: MCPBundleMemberInput[];
}

export type MCPBundleUpdateInput = Partial<Pick<MCPBundleCreateInput, 'name' | 'description' | 'enabled' | 'members'>>;

export interface MCPBundleServerTool {
  name: string;
  description: string | null;
  needs_approval: boolean;
  disabled_by_policy: boolean;
}

export interface MCPBundleServerTools {
  mcp_server_id: string;
  server_key: string;
  name: string;
  enabled: boolean;
  tools: MCPBundleServerTool[];
}

export interface MCPBundleExposedTool {
  exposed_name: string;
  server_id: string;
  server_key: string;
  tool_name: string;
  description: string | null;
}

export type MCPBundleToolAccess = 'available' | 'needs_approval' | 'disabled_by_policy' | 'no_access';

export interface MCPBundleKeyPreview {
  api_key: string;
  team_id: string | null;
  organization_id: string | null;
  sees_bundle: boolean;
  reason: string | null;
  usable_count: number;
  tools: Array<{ name: string; access: MCPBundleToolAccess }>;
}

export interface MCPBundlePreview {
  servers: MCPBundleServerTools[];
  tools: MCPBundleExposedTool[];
  errors: string[];
  key: MCPBundleKeyPreview | null;
}

export interface MCPBundlePreviewInput {
  /** A saved bundle. The draft members replace the saved members when given. */
  mcp_bundle_id?: string;
  owner_scope_type?: MCPBundleOwnerScopeType;
  owner_scope_id?: string | null;
  members?: MCPBundleMemberInput[];
  /** The token hash of the API key to check. */
  api_key?: string;
}

/** A short server row for the "Add server" picker. */
export interface MCPBundleServerOption {
  mcp_server_id: string;
  server_key: string;
  name: string;
  enabled: boolean;
  tool_count: number;
  owner_scope_type: string;
  owner_scope_id: string | null;
}

const BASE = '/ui/api/mcp-bundles';
const bundlePath = (bundleId: string) => `${BASE}/${encodeURIComponent(bundleId)}`;

export const mcpBundles = {
  list: (params?: { search?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    apiFetch<Paginated<MCPBundle>>(withQuery(BASE, params), { signal }),
  get: (bundleId: string, signal?: AbortSignal) => apiFetch<MCPBundle>(bundlePath(bundleId), { signal }),
  create: (payload: MCPBundleCreateInput) => apiFetch<MCPBundle>(BASE, { method: 'POST', json: payload }),
  update: (bundleId: string, payload: MCPBundleUpdateInput) =>
    apiFetch<MCPBundle>(bundlePath(bundleId), { method: 'PATCH', json: payload }),
  delete: (bundleId: string) =>
    apiFetch<{ deleted: boolean; mcp_bundle_id: string }>(bundlePath(bundleId), { method: 'DELETE' }),
  preview: (payload: MCPBundlePreviewInput, signal?: AbortSignal) =>
    apiFetch<MCPBundlePreview>(`${BASE}/preview`, { method: 'POST', json: payload, signal }),
  searchServers: (params: { search?: string; limit?: number }, signal?: AbortSignal) =>
    apiFetch<Paginated<MCPBundleServerOption>>(withQuery('/ui/api/mcp-servers', params), { signal }),
};
