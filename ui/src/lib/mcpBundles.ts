import type {
  MCPBundle,
  MCPBundleCreateInput,
  MCPBundleMemberInput,
  MCPBundleOwnerScopeType,
  MCPBundleToolAccess,
  MCPBundleUpdateInput,
} from './api/mcpBundles';

// These rules are the same as the rules of the API (src/mcp/bundles.py).
export const BUNDLE_KEY_PATTERN = /^[a-z0-9][a-z0-9_-]{0,62}$/;
export const NAME_PREFIX_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/;
export const RESERVED_TOOL_NAMES: ReadonlySet<string> = new Set(['mcp_tool_search']);
export const MAX_BUNDLE_MEMBERS = 50;

export const MCP_BUNDLE_NEW_PATH = '/mcp-servers/bundles/new';
export const mcpBundlePath = (bundleId: string) => `/mcp-servers/bundles/${encodeURIComponent(bundleId)}`;

export const BUNDLE_INFO_TEXT =
  'A bundle shows tools from several servers at one endpoint. It does not give access. '
  + 'A caller sees only the tools that their key can already use.';

export interface BundleMemberDraft {
  mcp_server_id: string;
  server_key: string;
  server_name: string;
  all_tools: boolean;
  /** The selected tools when all_tools is false. */
  tool_names: string[];
  name_prefix: string;
}

export interface BundleDraft {
  bundle_key: string;
  name: string;
  description: string;
  owner_scope_type: MCPBundleOwnerScopeType;
  owner_scope_id: string;
  members: BundleMemberDraft[];
}

export interface NameConflict {
  name: string;
  /** Namespaced tools (`server.tool`) that use the name. */
  sources: string[];
}

export function emptyBundleDraft(ownerScopeType: MCPBundleOwnerScopeType, ownerScopeId = ''): BundleDraft {
  return {
    bundle_key: '',
    name: '',
    description: '',
    owner_scope_type: ownerScopeType,
    owner_scope_id: ownerScopeType === 'global' ? '' : ownerScopeId,
    members: [],
  };
}

export function draftFromBundle(bundle: MCPBundle): BundleDraft {
  return {
    bundle_key: bundle.bundle_key,
    name: bundle.name,
    description: bundle.description ?? '',
    owner_scope_type: bundle.owner_scope_type,
    owner_scope_id: bundle.owner_scope_id ?? '',
    members: bundle.members.map((member) => ({
      mcp_server_id: member.mcp_server_id,
      server_key: member.server_key,
      server_name: member.server_name,
      all_tools: member.all_tools,
      tool_names: member.tool_names ?? [],
      name_prefix: member.name_prefix ?? '',
    })),
  };
}

/** Make a URL-safe key from a name: "Eng Assist" becomes "eng-assist". */
export function suggestBundleKey(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^[-_]+/, '')
    .replace(/-+$/, '')
    .slice(0, 63);
}

export function exposedToolName(prefix: string | null | undefined, toolName: string): string {
  const clean = (prefix ?? '').trim();
  return clean ? `${clean}_${toolName}` : toolName;
}

/** The tools of the member, in the order of the server tool list. */
export function memberToolNames(member: BundleMemberDraft, serverTools: readonly string[]): string[] {
  if (member.all_tools) return [...serverTools];
  const selected = new Set(member.tool_names);
  const known = serverTools.filter((name) => selected.has(name));
  const unknown = member.tool_names.filter((name) => !serverTools.includes(name));
  return [...known, ...unknown];
}

function memberInput(member: BundleMemberDraft): MCPBundleMemberInput {
  const prefix = member.name_prefix.trim();
  return member.all_tools
    ? { mcp_server_id: member.mcp_server_id, all_tools: true, name_prefix: prefix || null }
    : { mcp_server_id: member.mcp_server_id, all_tools: false, tool_names: [...member.tool_names], name_prefix: prefix || null };
}

export function memberInputs(members: readonly BundleMemberDraft[]): MCPBundleMemberInput[] {
  return members.map(memberInput);
}

/**
 * Members for a preview read. A member with no selected tool is not valid,
 * so the preview leaves it out and the editor shows its own message.
 */
export function previewMemberInputs(members: readonly BundleMemberDraft[]): MCPBundleMemberInput[] {
  return members.filter((member) => member.all_tools || member.tool_names.length > 0).map(memberInput);
}

export function createPayload(draft: BundleDraft): MCPBundleCreateInput {
  return {
    bundle_key: draft.bundle_key.trim().toLowerCase(),
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    owner_scope_type: draft.owner_scope_type,
    owner_scope_id: draft.owner_scope_type === 'global' ? null : draft.owner_scope_id.trim() || null,
    members: memberInputs(draft.members),
  };
}

export function updatePayload(draft: BundleDraft): MCPBundleUpdateInput {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    members: memberInputs(draft.members),
  };
}

/** Exposed names that two or more tools use. */
export function findNameConflicts(
  members: readonly BundleMemberDraft[],
  toolsByServer: Readonly<Record<string, readonly string[]>>,
): NameConflict[] {
  const sources = new Map<string, string[]>();
  for (const member of members) {
    for (const tool of memberToolNames(member, toolsByServer[member.mcp_server_id] ?? [])) {
      const name = exposedToolName(member.name_prefix, tool);
      sources.set(name, [...(sources.get(name) ?? []), `${member.server_key}.${tool}`]);
    }
  }
  return [...sources.entries()]
    .filter(([, items]) => items.length > 1)
    .map(([name, items]) => ({ name, sources: items }));
}

/** Problems that stop a save. The API checks the same rules again. */
export function validateDraft(
  draft: BundleDraft,
  toolsByServer: Readonly<Record<string, readonly string[]>>,
): string[] {
  const errors: string[] = [];
  if (!BUNDLE_KEY_PATTERN.test(draft.bundle_key.trim().toLowerCase())) {
    errors.push('Bundle key must start with a letter or digit and use only a-z, 0-9, "-" and "_" (at most 63 characters).');
  }
  if (!draft.name.trim()) errors.push('Name is required.');
  if (draft.owner_scope_type !== 'global' && !draft.owner_scope_id.trim()) {
    errors.push(`Select the ${draft.owner_scope_type} that owns the bundle.`);
  }
  if (draft.members.length > MAX_BUNDLE_MEMBERS) errors.push(`A bundle can have at most ${MAX_BUNDLE_MEMBERS} servers.`);
  for (const member of draft.members) {
    if (!member.all_tools && member.tool_names.length === 0) {
      errors.push(`Select at least one tool of "${member.server_key}".`);
    }
    const prefix = member.name_prefix.trim();
    if (prefix && !NAME_PREFIX_PATTERN.test(prefix)) {
      errors.push(`The name prefix of "${member.server_key}" must start with a letter or digit and use only letters, digits, "-" and "_".`);
    }
    for (const tool of memberToolNames(member, toolsByServer[member.mcp_server_id] ?? [])) {
      const name = exposedToolName(member.name_prefix, tool);
      if (RESERVED_TOOL_NAMES.has(name)) errors.push(`The name "${name}" is reserved. Set a different name prefix for "${member.server_key}".`);
    }
  }
  for (const conflict of findNameConflicts(draft.members, toolsByServer)) {
    errors.push(`Two tools use the name "${conflict.name}": ${conflict.sources.join(' and ')}. Set a name prefix on one of the servers.`);
  }
  return errors;
}

export function gatewayOrigin(): string {
  return typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000';
}

export function bundleEndpointUrl(bundleKey: string, origin: string = gatewayOrigin()): string {
  return `${origin}/mcp/${encodeURIComponent(bundleKey || '<bundle_key>')}`;
}

/** The `tools` entry for /v1/chat/completions and /v1/responses. */
export function bundleToolSnippet(bundleKey: string): string {
  return JSON.stringify({ tools: [{ type: 'mcp', server: bundleKey || '<bundle_key>' }] }, null, 2);
}

export const ACCESS_LABELS: Record<MCPBundleToolAccess, string> = {
  available: 'Can use',
  needs_approval: 'Needs approval',
  disabled_by_policy: 'Disabled by policy',
  no_access: 'No access',
};

export function ownerScopeLabel(type: MCPBundleOwnerScopeType, id: string | null | undefined): string {
  if (type === 'global') return 'Global';
  const label = type === 'organization' ? 'Organization' : 'Team';
  return id ? `${label} · ${id}` : label;
}
