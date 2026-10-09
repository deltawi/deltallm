import { apiFetch, withQuery } from './transport';
import type { Paginated } from './pagination';
import type { Tier, TierActivationPreview, TierBootstrapResponse, TierCapacityBoostPayload, TierCapacityDashboard, TierCapacityDashboardBoost, TierCapacityPool, TierCapacityPoolCreatePayload, TierCapacityPoolPatchPayload, TierConfigurationDeleteResult, TierConfigurationMutationResult, TierConfigurationPage, TierCreatePayload, TierDetail, TierModelPolicy, TierModelPolicyBulkLimitsPayload, TierModelPolicyCreatePayload, TierModelPolicyPatchPayload, TierUpdatePayload, TierVersion, TierVersionCreatePayload, TierVersionDetail } from './tierContracts';

export const tierCapacity = {
  dashboard: (params?: { top_org_limit?: number; pool_limit?: number }) =>
    apiFetch<TierCapacityDashboard>(withQuery('/ui/api/tier-capacity/dashboard', params)),
  upsertBoost: (payload: TierCapacityBoostPayload) =>
    apiFetch<TierCapacityDashboardBoost & {
      pool_key: string;
      callable_key: string;
      ttl_seconds: number;
    }>('/ui/api/tier-capacity/boosts', { method: 'POST', json: payload }),
  deleteBoost: (params: { organization_id: string; pool_key: string; callable_key: string }) =>
    apiFetch<{ deleted: boolean; organization_id: string; pool_key: string; callable_key: string }>(
      withQuery('/ui/api/tier-capacity/boosts', params),
      { method: 'DELETE' },
    ),
};

export const tiers = {
  list: (params?: { search?: string; enabled?: boolean | string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<Tier>>(withQuery('/ui/api/tiers', params)),
  listAll: async (params?: { search?: string; enabled?: boolean | string }) => {
    const limit = 200;
    let offset = 0;
    let items: Tier[] = [];
    while (true) {
      const page = await apiFetch<Paginated<Tier>>(
        withQuery('/ui/api/tiers', { ...(params || {}), limit, offset }),
      );
      items = items.concat(page.data || []);
      if (!page.pagination?.has_more) {
        break;
      }
      offset += limit;
    }
    return items;
  },
  get: (tierId: string, params?: { include_versions?: boolean }) =>
    apiFetch<TierDetail>(withQuery(`/ui/api/tiers/${encodeURIComponent(tierId)}`, params)),
  create: (payload: TierCreatePayload) =>
    apiFetch<Tier>('/ui/api/tiers', { method: 'POST', json: payload }),
  bootstrap: (payload: TierCreatePayload, idempotencyKey: string) =>
    apiFetch<TierBootstrapResponse>('/ui/api/tiers/bootstrap', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      json: payload,
    }),
  update: (tierId: string, payload: TierUpdatePayload) =>
    apiFetch<Tier>(`/ui/api/tiers/${encodeURIComponent(tierId)}`, { method: 'PATCH', json: payload }),
  delete: (tierId: string) =>
    apiFetch<{ deleted: boolean; tier_id: string }>(`/ui/api/tiers/${encodeURIComponent(tierId)}`, { method: 'DELETE' }),
  createVersion: (tierId: string, payload: TierVersionCreatePayload = {}) =>
    apiFetch<TierVersion>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions`, { method: 'POST', json: payload }),
  listVersions: (tierId: string, params?: { status?: string | string[]; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    const statuses = Array.isArray(params?.status) ? params.status : params?.status ? [params.status] : [];
    for (const status of statuses) {
      if (status.trim()) query.append('status', status.trim());
    }
    if (params?.limit !== undefined) query.set('limit', String(params.limit));
    if (params?.offset !== undefined) query.set('offset', String(params.offset));
    const suffix = query.toString();
    const path = `/ui/api/tiers/${encodeURIComponent(tierId)}/versions${suffix ? `?${suffix}` : ''}`;
    return apiFetch<Paginated<TierVersion>>(path);
  },
  cloneVersion: (tierId: string, sourceVersionId: string) =>
    apiFetch<TierVersion>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(sourceVersionId)}/clone`, { method: 'POST' }),
  getVersion: (tierId: string, versionId: string) =>
    apiFetch<TierVersionDetail>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}`),
  listModelPolicies: (
    tierId: string,
    versionId: string,
    params?: {
      search?: string;
      enabled?: boolean;
      access_mode?: string;
      capacity_pool_key?: string;
      sort?: 'callable_key' | 'priority' | 'updated_at';
      order?: 'asc' | 'desc';
      limit?: number;
      offset?: number;
    },
  ) => apiFetch<TierConfigurationPage<TierModelPolicy>>(
    withQuery(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/model-policies`, params),
  ),
  createModelPolicy: (
    tierId: string,
    versionId: string,
    payload: TierModelPolicyCreatePayload,
  ) => apiFetch<TierConfigurationMutationResult<TierModelPolicy>>(
    `/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/model-policies`,
    { method: 'POST', json: payload },
  ),
  updateModelPolicy: (
    tierId: string,
    versionId: string,
    policyId: string,
    payload: TierModelPolicyPatchPayload,
  ) => apiFetch<TierConfigurationMutationResult<TierModelPolicy>>(
    `/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/model-policies/${encodeURIComponent(policyId)}`,
    { method: 'PATCH', json: payload },
  ),
  deleteModelPolicy: (
    tierId: string,
    versionId: string,
    policyId: string,
    expectedRevision: number,
  ) => apiFetch<TierConfigurationDeleteResult>(
    `/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/model-policies/${encodeURIComponent(policyId)}`,
    { method: 'DELETE', json: { expected_revision: expectedRevision } },
  ),
  bulkUpdateModelPolicyLimits: (
    tierId: string,
    versionId: string,
    payload: TierModelPolicyBulkLimitsPayload,
  ) => apiFetch<Omit<TierConfigurationDeleteResult, 'deleted'> & { affected_count: number }>(
    `/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/model-policies/bulk-limits`,
    { method: 'POST', json: payload },
  ),
  listCapacityPools: (
    tierId: string,
    versionId: string,
    params?: {
      search?: string;
      callable_key?: string;
      strategy?: string;
      sort?: 'pool_key' | 'callable_key' | 'updated_at';
      order?: 'asc' | 'desc';
      limit?: number;
      offset?: number;
    },
  ) => apiFetch<TierConfigurationPage<TierCapacityPool>>(
    withQuery(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/capacity-pools`, params),
  ),
  createCapacityPool: (
    tierId: string,
    versionId: string,
    payload: TierCapacityPoolCreatePayload,
  ) => apiFetch<TierConfigurationMutationResult<TierCapacityPool>>(
    `/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/capacity-pools`,
    { method: 'POST', json: payload },
  ),
  updateCapacityPool: (
    tierId: string,
    versionId: string,
    poolId: string,
    payload: TierCapacityPoolPatchPayload,
  ) => apiFetch<TierConfigurationMutationResult<TierCapacityPool>>(
    `/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/capacity-pools/${encodeURIComponent(poolId)}`,
    { method: 'PATCH', json: payload },
  ),
  deleteCapacityPool: (
    tierId: string,
    versionId: string,
    poolId: string,
    expectedRevision: number,
  ) => apiFetch<TierConfigurationDeleteResult>(
    `/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/capacity-pools/${encodeURIComponent(poolId)}`,
    { method: 'DELETE', json: { expected_revision: expectedRevision } },
  ),
  activationPreview: (tierId: string, versionId: string) =>
    apiFetch<TierActivationPreview>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/activation-preview`),
  activateVersion: (
    tierId: string,
    versionId: string,
    payload: { expected_revision: number; expected_active_version_id: string | null },
  ) => apiFetch<TierVersion>(
    `/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/activate`,
    { method: 'POST', json: payload },
  ),
  archiveVersion: (tierId: string, versionId: string) =>
    apiFetch<TierVersion>(`/ui/api/tiers/${encodeURIComponent(tierId)}/versions/${encodeURIComponent(versionId)}/archive`, { method: 'POST' }),
};
