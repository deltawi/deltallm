import { apiFetch } from './transport';

export type AssetVisibility = 'private' | 'team' | 'organization' | 'public' | 'shared';
export type AssetAccessRole = 'reader' | 'editor';
export type AssetSubjectType = 'team' | 'organization' | 'public';

export interface ManagedAssetGrantInput {
  subject_type: AssetSubjectType;
  subject_id: string | null;
  access_role: AssetAccessRole;
}

export interface ManagedAssetAccess {
  managed_asset_id: string;
  asset_kind: string;
  governance_source: 'platform' | 'creator';
  owner_account_id: string | null;
  visibility: AssetVisibility;
  subject_id: string | null;
  access_role: AssetAccessRole | null;
  grants: ManagedAssetGrantInput[];
  effective_role: AssetAccessRole | 'owner' | null;
  policy_version: number;
  capabilities: {
    read: boolean;
    write: boolean;
    manage_access: boolean;
    delete: boolean;
    platform_admin_override: boolean;
  };
}

export interface ManagedAssetAccessInput {
  grants: ManagedAssetGrantInput[];
  visibility?: AssetVisibility;
  subject_id?: string | null;
  access_role?: AssetAccessRole | null;
  expected_policy_version?: number;
}

export function managedAssetAccessInput(
  grants: ManagedAssetGrantInput[],
  expectedPolicyVersion?: number,
): ManagedAssetAccessInput {
  const onlyGrant = grants.length === 1 ? grants[0] : null;
  return {
    grants,
    visibility: grants.length === 0
      ? 'private'
      : onlyGrant?.subject_type || 'shared',
    subject_id: onlyGrant?.subject_id || null,
    access_role: onlyGrant?.access_role || null,
    expected_policy_version: expectedPolicyVersion,
  };
}

export const managedAssets = {
  audienceOptions: (
    subjectType: 'team' | 'organization',
    params: { search?: string; selectedIds?: string[]; limit?: number },
    signal?: AbortSignal,
  ) => {
    const query = new URLSearchParams({
      subject_type: subjectType,
      search: params.search || '',
      limit: String(params.limit ?? 3),
    });
    for (const selectedId of params.selectedIds || []) query.append('selected_id', selectedId);
    return apiFetch<{ data: Array<{ id: string; label: string }> }>(
      `/ui/api/assets/audience-options?${query.toString()}`,
      { signal },
    );
  },
  updateAccess: (managedAssetId: string, access: ManagedAssetAccessInput) =>
    apiFetch<{ access: ManagedAssetAccess; warnings?: string[] }>(
      `/ui/api/assets/${encodeURIComponent(managedAssetId)}/access`,
      { method: 'PUT', json: access },
    ),
};
