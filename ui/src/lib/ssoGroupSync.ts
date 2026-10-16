import type {
  SsoGroupMapping,
  SsoGroupPreviewChange,
  SsoGroupSyncSettings,
  SsoGroupTarget,
  SsoOrgRole,
  SsoTargetStatus,
  SsoTeamRole,
} from './api/ssoGroupSync';

export const TEAM_ROLE_OPTIONS: Array<{ value: SsoTeamRole; label: string }> = [
  { value: 'team_admin', label: 'Admin' },
  { value: 'team_developer', label: 'Developer' },
  { value: 'team_viewer', label: 'Viewer' },
];

export const ORG_ROLE_OPTIONS: Array<{ value: SsoOrgRole; label: string }> = [
  { value: 'org_owner', label: 'Owner' },
  { value: 'org_admin', label: 'Admin' },
  { value: 'org_auditor', label: 'Auditor' },
  { value: 'org_billing', label: 'Billing' },
  { value: 'org_member', label: 'Member' },
];

const ROLE_LABELS: Record<string, string> = {
  platform_admin: 'Platform admin',
  org_user: 'User',
  ...Object.fromEntries(TEAM_ROLE_OPTIONS.map((item) => [item.value, item.label])),
  ...Object.fromEntries(ORG_ROLE_OPTIONS.map((item) => [item.value, item.label])),
};

export function roleLabel(role: string | null | undefined): string {
  if (!role) return '';
  return ROLE_LABELS[role] ?? role;
}

export const TARGET_STATUS_LABELS: Record<SsoTargetStatus, string> = {
  ok: 'Available',
  not_found: 'Not found',
  external_managed: 'Managed by DeltaLLM Cloud',
  inactive_organization: 'Organization not active',
};

export const DEFAULT_SSO_GROUP_SYNC: SsoGroupSyncSettings = {
  enabled: false,
  claim: 'groups',
  admin_values: [],
  require_mapped_value: false,
  remove_unlisted: false,
  mappings: [],
};

/** Values typed into a chip or preview input, split on commas and new lines. Group names can have spaces. */
export function splitClaimValues(raw: string): string[] {
  return raw.split(/[,\n]+/).map((item) => item.trim()).filter(Boolean);
}

export function addUniqueValues(current: string[], raw: string): string[] {
  const next = [...current];
  for (const value of splitClaimValues(raw)) {
    if (!next.includes(value)) next.push(value);
  }
  return next;
}

export function mappingKey(mapping: SsoGroupMapping): string {
  return `${mapping.value}::${mapping.team_id ? `team:${mapping.team_id}` : `org:${mapping.organization_id}`}`;
}

/** The first problem that the server would also reject, as a sentence for the form. */
export function validateSsoGroupSync(settings: SsoGroupSyncSettings): string | null {
  if (!/^[A-Za-z0-9_:-]+(\.[A-Za-z0-9_:-]+)*$/.test(settings.claim.trim())) {
    return 'Enter a claim name, or a dot path such as realm_access.roles.';
  }
  const seen = new Set<string>();
  for (const mapping of settings.mappings) {
    const value = mapping.value.trim();
    if (!value) return 'Each mapping needs a claim value.';
    if (settings.admin_values.includes(value)) {
      return `"${value}" is already an admin value. Use a different value for the mapping.`;
    }
    if (!mapping.team_id && !mapping.organization_id) return `Pick a team or organization for "${value}".`;
    const key = mappingKey({ ...mapping, value });
    if (seen.has(key)) return `"${value}" maps to the same team or organization twice.`;
    seen.add(key);
  }
  return null;
}

/** Trim values and drop the role field that does not apply to the target. */
export function buildSsoGroupSyncPayload(settings: SsoGroupSyncSettings): SsoGroupSyncSettings {
  return {
    enabled: settings.enabled,
    claim: settings.claim.trim(),
    admin_values: settings.admin_values.map((value) => value.trim()).filter(Boolean),
    require_mapped_value: settings.require_mapped_value,
    remove_unlisted: settings.remove_unlisted,
    mappings: settings.mappings.map((mapping) => (
      mapping.team_id
        ? { value: mapping.value.trim(), team_id: mapping.team_id, team_role: mapping.team_role ?? 'team_viewer' }
        : {
          value: mapping.value.trim(),
          organization_id: mapping.organization_id,
          org_role: mapping.org_role ?? 'org_member',
        }
    )),
  };
}

export function sameSsoGroupSync(a: SsoGroupSyncSettings, b: SsoGroupSyncSettings): boolean {
  return JSON.stringify(buildSsoGroupSyncPayload(a)) === JSON.stringify(buildSsoGroupSyncPayload(b));
}

export function targetFor(
  mapping: SsoGroupMapping,
  targets: Map<string, SsoGroupTarget>,
): SsoGroupTarget | undefined {
  return mapping.team_id
    ? targets.get(`team:${mapping.team_id}`)
    : targets.get(`organization:${mapping.organization_id}`);
}

export function targetIndex(targets: SsoGroupTarget[]): Map<string, SsoGroupTarget> {
  return new Map(targets.map((target) => [`${target.kind}:${target.id}`, target]));
}

/** Parse the preview input: a JSON object of token claims, or a plain list of values. */
export function parsePreviewInput(raw: string): { claims: Record<string, unknown> } | { values: string[] } {
  const text = raw.trim();
  if (text.startsWith('{')) {
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Token claims must be a JSON object.');
    }
    return { claims: parsed as Record<string, unknown> };
  }
  return { values: splitClaimValues(text) };
}

export function describeChange(change: SsoGroupPreviewChange): string {
  const target = change.kind === 'platform_role'
    ? 'Platform role'
    : change.target_name || change.target_id || '';
  if (change.action === 'kept') return `${target} kept: last platform admin`;
  if (change.action === 'remove') return `${target}: remove ${roleLabel(change.role_before)}`;
  if (change.action === 'add') return `${target}: add as ${roleLabel(change.role_after)}`;
  return `${target}: ${roleLabel(change.role_before)} to ${roleLabel(change.role_after)}`;
}

/** The Single sign-on settings tab shows only when SSO is configured on the server. */
export function isSsoTabVisible(config: { sso_enabled?: boolean } | null | undefined): boolean {
  return Boolean(config?.sso_enabled);
}
