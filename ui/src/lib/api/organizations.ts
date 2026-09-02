import { apiFetch, withQuery } from './transport';
import {
  normalizeOrganizationLifecycleState,
  type OrganizationLifecycleState,
} from '../organizationLifecycle';
import {
  normalizeOrganizationCount,
  type OrganizationCount,
} from '../organizationCounts';

export interface OrganizationTierAssignment {
  assignment_id: string;
  organization_id: string;
  tier_id: string;
  tier_key?: string | null;
  tier_name?: string | null;
  tier_version_id?: string | null;
  tier_version_number?: number | null;
  tier_version_status?: string | null;
  assignment_type: 'primary' | 'addon' | 'override' | string;
  enabled: boolean;
  weight: number;
  starts_at?: string | null;
  ends_at?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface OrganizationTierAssignmentPayload {
  tier_id: string;
  tier_version_id?: string | null;
  assignment_type: string;
  enabled?: boolean;
  weight?: number;
  starts_at?: string | null;
  ends_at?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface OrganizationPrimaryTierSummary {
  assignment_id?: string | null;
  tier_id?: string | null;
  tier_key?: string | null;
  tier_name?: string | null;
  tier_version_id?: string | null;
  tier_version_number?: number | null;
  follows_active_version: boolean;
}

export interface OrganizationServicePolicy {
  source: 'tier' | 'legacy';
  runtime_source: 'tier' | 'legacy';
  tier_authoritative: boolean;
  tier_policy_mode: 'disabled' | 'shadow' | 'enforce' | string;
  primary_tier: OrganizationPrimaryTierSummary | null;
  active_assignment_count: number;
  overlay_count: number;
  hard_caps_configured: boolean;
  organization_hard_caps: Partial<Record<
    'rpm_limit' | 'tpm_limit' | 'rph_limit' | 'rpd_limit' | 'tpd_limit',
    number
  >>;
  legacy_model_limits_configured: boolean;
}

export interface OrganizationCapabilities {
  view: boolean;
  edit: boolean;
  add_team: boolean;
  manage_members: boolean;
  manage_assets: boolean;
  manage_service_policy: boolean;
  view_usage: boolean;
}

export interface OrganizationRecord {
  organization_id: string;
  organization_name?: string | null;
  lifecycle_state: OrganizationLifecycleState;
  deletion_requested_at?: string | null;
  deletion_not_before_at?: string | null;
  max_budget?: number | null;
  soft_budget?: number | null;
  spend?: number | null;
  budget_duration?: string | null;
  budget_reset_at?: string | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  rph_limit?: number | null;
  rpd_limit?: number | null;
  tpd_limit?: number | null;
  model_rpm_limit?: Record<string, number> | null;
  model_tpm_limit?: Record<string, number> | null;
  audit_content_storage_enabled?: boolean | null;
  service_policy: OrganizationServicePolicy;
  primary_tier_assignment?: OrganizationTierAssignment;
  capabilities: OrganizationCapabilities;
  team_count?: number | null;
  member_count?: number | null;
  user_count?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  [key: string]: unknown;
}

export interface OrganizationListItem extends OrganizationRecord {
  team_count: OrganizationCount;
  member_count: OrganizationCount;
}

export interface OrganizationCreatePayload {
  organization_name: string;
  legacy_policy_exception?: boolean;
  primary_tier?: {
    tier_id: string;
    tier_version_id?: string | null;
  };
  max_budget?: number;
  soft_budget?: number;
  budget_duration?: string;
  budget_reset_at?: string;
  rpm_limit?: number;
  tpm_limit?: number;
  rph_limit?: number;
  rpd_limit?: number;
  tpd_limit?: number;
  audit_content_storage_enabled?: boolean;
  callable_target_bindings?: Array<{ callable_key: string }>;
}

export interface OrganizationPage {
  data: OrganizationListItem[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
    has_more: boolean;
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function capability(value: Record<string, unknown> | null, key: string): boolean {
  return value?.[key] === true;
}

export function normalizeOrganizationCapabilities(
  value: unknown,
  lifecycleState: OrganizationLifecycleState,
): OrganizationCapabilities {
  const raw = isRecord(value) ? value : null;
  const active = lifecycleState === 'active';
  return {
    view: raw?.view !== false,
    edit: active && capability(raw, 'edit'),
    add_team: active && capability(raw, 'add_team'),
    manage_members: active && capability(raw, 'manage_members'),
    manage_assets: active && capability(raw, 'manage_assets'),
    manage_service_policy: active && capability(raw, 'manage_service_policy'),
    view_usage: lifecycleState !== 'unavailable' && capability(raw, 'view_usage'),
  };
}

export function normalizeOrganizationRecord(value: unknown): OrganizationRecord {
  if (!isRecord(value)) throw new Error('Invalid organization response');
  const organizationId = typeof value.organization_id === 'string'
    ? value.organization_id.trim()
    : '';
  if (!organizationId) throw new Error('Invalid organization response');

  const lifecycleState = normalizeOrganizationLifecycleState(value.lifecycle_state);
  return {
    ...value,
    organization_id: organizationId,
    lifecycle_state: lifecycleState,
    capabilities: normalizeOrganizationCapabilities(value.capabilities, lifecycleState),
  } as OrganizationRecord;
}

export function normalizeOrganizationListItem(value: unknown): OrganizationListItem {
  if (!isRecord(value)) throw new Error('Invalid organization response');
  return {
    ...normalizeOrganizationRecord(value),
    team_count: normalizeOrganizationCount(value.team_count),
    member_count: normalizeOrganizationCount(value.member_count),
  };
}

export function normalizeOrganizationPage(value: unknown): OrganizationPage {
  if (!isRecord(value) || !Array.isArray(value.data) || !isRecord(value.pagination)) {
    throw new Error('Invalid organization list response');
  }
  const pagination = value.pagination;
  return {
    data: value.data.map(normalizeOrganizationListItem),
    pagination: {
      total: Number(pagination.total || 0),
      limit: Number(pagination.limit || 0),
      offset: Number(pagination.offset || 0),
      has_more: pagination.has_more === true,
    },
  };
}

export const organizationRecordsApi = {
  list: async (
    params?: { search?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ): Promise<OrganizationPage> => normalizeOrganizationPage(
    await apiFetch<unknown>(withQuery('/ui/api/organizations', params), { signal }),
  ),
  get: async (organizationId: string, signal?: AbortSignal): Promise<OrganizationRecord> => (
    normalizeOrganizationRecord(
      await apiFetch<unknown>(
        `/ui/api/organizations/${encodeURIComponent(organizationId)}`,
        { signal },
      ),
    )
  ),
  create: async (payload: OrganizationCreatePayload): Promise<OrganizationRecord> => (
    normalizeOrganizationRecord(
      await apiFetch<unknown>('/ui/api/organizations', { method: 'POST', json: payload }),
    )
  ),
  update: async (organizationId: string, payload: object): Promise<OrganizationRecord> => (
    normalizeOrganizationRecord(
      await apiFetch<unknown>(
        `/ui/api/organizations/${encodeURIComponent(organizationId)}`,
        { method: 'PUT', json: payload },
      ),
    )
  ),
};
