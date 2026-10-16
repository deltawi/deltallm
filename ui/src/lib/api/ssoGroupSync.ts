import { apiFetch } from './transport';

export type SsoTeamRole = 'team_admin' | 'team_developer' | 'team_viewer';
export type SsoOrgRole = 'org_owner' | 'org_admin' | 'org_auditor' | 'org_billing' | 'org_member';

export interface SsoGroupMapping {
  value: string;
  team_id?: string | null;
  team_role?: SsoTeamRole | null;
  organization_id?: string | null;
  org_role?: SsoOrgRole | null;
}

export interface SsoGroupSyncSettings {
  enabled: boolean;
  claim: string;
  admin_values: string[];
  require_mapped_value: boolean;
  remove_unlisted: boolean;
  mappings: SsoGroupMapping[];
}

export type SsoTargetStatus = 'ok' | 'not_found' | 'external_managed' | 'inactive_organization';

export interface SsoGroupTarget {
  id: string;
  kind: 'team' | 'organization';
  name: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  status: SsoTargetStatus;
}

export interface SsoGroupSyncResponse {
  settings: SsoGroupSyncSettings;
  provider: { enabled: boolean; provider: string; authorize_url: string | null };
  targets: SsoGroupTarget[];
}

export interface SsoGroupPreviewRequest {
  settings: SsoGroupSyncSettings;
  email?: string | null;
  claims?: Record<string, unknown> | null;
  values?: string[] | null;
}

export interface SsoGroupPreviewChange {
  kind: 'platform_role' | 'organization' | 'team';
  action: 'add' | 'update' | 'remove' | 'kept';
  target_id: string | null;
  target_name: string | null;
  role_before: string | null;
  role_after: string | null;
  reason: string | null;
  revokes_keys: boolean;
}

export interface SsoGroupPreviewResponse {
  claim_found: boolean;
  values: string[];
  matched_values: string[];
  sign_in_denied: boolean;
  account_found: boolean;
  account_role: string | null;
  changes: SsoGroupPreviewChange[];
  skipped: SsoGroupTarget[];
}

const PATH = '/ui/api/settings/sso-group-sync';

export const ssoGroupSync = {
  get: (signal?: AbortSignal) => apiFetch<SsoGroupSyncResponse>(PATH, { signal }),
  update: (settings: SsoGroupSyncSettings, signal?: AbortSignal) =>
    apiFetch<SsoGroupSyncResponse>(PATH, { method: 'PUT', json: settings, signal }),
  preview: (payload: SsoGroupPreviewRequest, signal?: AbortSignal) =>
    apiFetch<SsoGroupPreviewResponse>(`${PATH}/preview`, { method: 'POST', json: payload, signal }),
};
