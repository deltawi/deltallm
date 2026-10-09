import type { Paginated } from './pagination';
import type { OrganizationTierAssignment } from './organizations';

export interface Tier {
  tier_id: string;
  tier_key: string;
  name: string;
  description?: string | null;
  enabled: boolean;
  metadata?: Record<string, unknown> | null;
  active_version_id?: string | null;
  active_version?: TierCatalogVersionSummary | null;
  latest_draft_version?: TierCatalogVersionSummary | null;
  draft_count?: number;
  version_count: number;
  assignment_count: number;
  live_assignment_count?: number;
  organization_count?: number;
  last_activity_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierCatalogVersionSummary {
  tier_version_id: string;
  version_number: number;
  configuration_revision: number;
  model_policy_count: number;
  capacity_pool_count: number;
  created_by_account_id?: string | null;
  created_by_kind: 'account' | 'master_key' | 'system' | 'unknown' | string;
  created_by_email?: string | null;
  source_tier_version_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierCreatePayload {
  tier_key: string;
  name: string;
  description?: string | null;
  enabled?: boolean;
  metadata?: Record<string, unknown> | null;
}

export type TierUpdatePayload = Partial<TierCreatePayload>;

export interface TierVersion {
  tier_version_id: string;
  tier_id: string;
  version_number: number;
  status: 'draft' | 'active' | 'archived' | string;
  configuration_revision: number;
  published_at?: string | null;
  published_by_account_id?: string | null;
  created_by_account_id?: string | null;
  created_by_kind: 'account' | 'master_key' | 'system' | 'unknown' | string;
  created_by_email?: string | null;
  source_tier_version_id?: string | null;
  metadata?: Record<string, unknown> | null;
  model_policy_count: number;
  capacity_pool_count: number;
  assignment_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierVersionCreatePayload {
  version_number?: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface TierModelPolicy {
  tier_model_policy_id?: string;
  tier_version_id?: string;
  callable_key: string;
  enabled: boolean;
  access_mode: 'allow' | 'deny' | string;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  output_tpm_limit?: number | null;
  rph_limit?: number | null;
  rpd_limit?: number | null;
  tpd_limit?: number | null;
  max_parallel_requests?: number | null;
  batch_rpm_limit?: number | null;
  batch_tpm_limit?: number | null;
  pricing?: Record<string, number> | null;
  capacity_pool_key?: string | null;
  priority: number;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierModelPolicyPayload {
  callable_key: string;
  enabled: boolean;
  access_mode: string;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  output_tpm_limit?: number | null;
  rph_limit?: number | null;
  rpd_limit?: number | null;
  tpd_limit?: number | null;
  max_parallel_requests?: number | null;
  batch_rpm_limit?: number | null;
  batch_tpm_limit?: number | null;
  pricing?: Record<string, number> | null;
  capacity_pool_key?: string | null;
  priority: number;
  metadata?: Record<string, unknown> | null;
}

export type TierModelPolicyCreatePayload = TierModelPolicyPayload & {
  expected_revision: number;
};

export type TierModelPolicyPatchPayload = Partial<
  Omit<TierModelPolicyPayload, 'callable_key'>
> & {
  expected_revision: number;
};

export interface TierModelPolicyBulkLimitsPayload {
  expected_revision: number;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  output_tpm_limit?: number | null;
  policy_ids?: string[];
  all_filtered?: boolean;
  search?: string;
  enabled?: boolean;
  access_mode?: string;
  capacity_pool_key?: string;
}

export interface TierCapacityPool {
  tier_capacity_pool_id?: string;
  tier_version_id?: string;
  pool_key: string;
  callable_key: string;
  rpm_capacity?: number | null;
  tpm_capacity?: number | null;
  max_parallel_requests?: number | null;
  strategy: string;
  saturation_threshold?: number | null;
  burst_multiplier?: number | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TierCapacityPoolPayload {
  pool_key: string;
  callable_key: string;
  rpm_capacity?: number | null;
  tpm_capacity?: number | null;
  max_parallel_requests?: number | null;
  strategy: string;
  saturation_threshold?: number | null;
  burst_multiplier?: number | null;
  metadata?: Record<string, unknown> | null;
}

export type TierCapacityPoolCreatePayload = TierCapacityPoolPayload & {
  expected_revision: number;
};

export type TierCapacityPoolPatchPayload = Partial<
  Omit<TierCapacityPoolPayload, 'pool_key' | 'callable_key'>
> & {
  expected_revision: number;
};

export interface TierDetail {
  tier: Tier;
  versions: TierVersion[];
}

export interface TierVersionDetail {
  tier_version: TierVersion;
  model_policies: TierModelPolicy[];
  capacity_pools: TierCapacityPool[];
}

export interface TierBootstrapResponse {
  tier: Tier;
  initial_version: TierVersion;
  idempotency_resolution: 'created' | 'replayed';
}

export interface TierConfigurationPage<T> extends Paginated<T> {
  configuration_revision: number;
  version_updated_at?: string | null;
}

export interface TierConfigurationMutationResult<T> {
  data: T;
  configuration_revision: number;
  version_updated_at?: string | null;
}

export interface TierConfigurationDeleteResult {
  deleted: boolean;
  configuration_revision: number;
  version_updated_at?: string | null;
}

export interface TierActivationChangeGroup {
  count: number;
  items: string[];
  truncated: boolean;
}

export interface TierActivationNotice {
  code: string;
  message: string;
  assignment_count?: number;
}

export interface TierActivationPreview {
  draft: TierVersion;
  draft_configuration_revision: number;
  current_active_version?: TierVersion | null;
  expected_active_version_id: string | null;
  affected_assignment_count: number;
  affected_organization_count: number;
  pinned_assignment_count: number;
  changes: {
    policy_added: TierActivationChangeGroup;
    policy_removed: TierActivationChangeGroup;
    policy_changed: TierActivationChangeGroup;
    pool_added: TierActivationChangeGroup;
    pool_removed: TierActivationChangeGroup;
    pool_changed: TierActivationChangeGroup;
  };
  warnings: TierActivationNotice[];
  blockers: TierActivationNotice[];
  can_activate: boolean;
}



export interface TierPolicySnapshotInfo {
  etag: string;
  generated_at: string;
  org_count: number;
  assignment_count: number;
  model_policy_count: number;
  capacity_pool_count: number;
  next_transition_at?: string | null;
  mode: string;
  snapshot_stale: boolean;
  last_reload_failed: boolean;
  last_reload_error_at?: string | null;
}

export interface TierRateLimitDescriptor {
  scope: string;
  entity_id: string;
  limit: number;
  amount_kind: 'requests' | 'tokens' | string;
  window_seconds: number;
  mode: string;
}

export interface TierCompiledModelPolicy {
  organization_id: string;
  callable_key: string;
  access_mode: string;
  source: Record<string, unknown>;
  limits: Record<string, number | null>;
  pricing: Record<string, number>;
  capacity_pool_key?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface TierCompiledPricingPolicy {
  organization_id: string;
  callable_key: string;
  mode: string;
  pricing: Record<string, number>;
  source: Record<string, unknown>;
}

export interface TierCompiledCapacityPool {
  pool_key: string;
  callable_key: string;
  rpm_capacity?: number | null;
  tpm_capacity?: number | null;
  max_parallel_requests?: number | null;
  strategy: string;
  saturation_threshold?: number | null;
  burst_multiplier?: number | null;
  source_tier_version_ids: string[];
  source_pool_ids: string[];
  metadata?: Record<string, unknown> | null;
  rate_limit_descriptors: TierRateLimitDescriptor[];
}

export interface OrganizationTierPolicyPreview {
  organization_id: string;
  snapshot: TierPolicySnapshotInfo;
  explicit_policy: boolean;
  tier_keys: string[];
  assignments: OrganizationTierAssignment[];
  allowed_callable_keys: string[];
  model_policies: TierCompiledModelPolicy[];
  pricing_policies: TierCompiledPricingPolicy[];
  rate_limits: TierRateLimitDescriptor[];
  organization_hard_caps: Partial<Record<
    'rpm_limit' | 'tpm_limit' | 'output_tpm_limit' | 'rph_limit' | 'rpd_limit' | 'tpd_limit' | 'model_rpm_limit' | 'model_tpm_limit',
    number | Record<string, number>
  >>;
  organization_rate_limits: TierRateLimitDescriptor[];
  capacity_pools: TierCompiledCapacityPool[];
}

export type TierSimulationBillingMode =
  | 'chat'
  | 'embedding'
  | 'rerank'
  | 'image_generation'
  | 'audio_speech'
  | 'audio_transcription';

export interface TierPolicySimulation {
  organization_id: string;
  callable_key: string;
  mode: string;
  request: {
    request_count: number;
    prompt_tokens: number;
    completion_tokens: number;
    tokens_per_request: number;
    aggregate_tokens: number;
    billing_mode: TierSimulationBillingMode | null;
    usage: Record<string, number>;
  };
  access: {
    allowed: boolean;
    reason: string;
    explicit_policy: boolean;
    tier_keys: string[];
  };
  decision: {
    allowed: boolean;
    reason: string;
    primary_limiting_scope: string | null;
    limiting_scopes: string[];
    basis: 'empty_window_static';
    live_capacity_evaluated: false;
  };
  model_policy: TierCompiledModelPolicy | null;
  pricing: TierCompiledPricingPolicy | null;
  calculated_price: {
    status: 'available' | 'partial' | 'unavailable';
    reason: string | null;
    currency: string;
    kind: 'exact' | 'range' | null;
    amount: number | null;
    minimum_amount: number | null;
    maximum_amount: number | null;
    request_count: number;
    amount_scope: 'aggregate';
    per_request_amount: number | null;
    per_request_minimum_amount: number | null;
    per_request_maximum_amount: number | null;
    billing_mode: TierSimulationBillingMode | null;
    usage_snapshot: Record<string, number>;
    configured_candidate_count: number;
    priced_candidate_count: number;
    unpriced_candidate_count: number;
    unevaluated_candidate_count: number;
    unpriced_reasons: string[];
    pricing_sources: string[];
    basis: 'configured_routes';
  };
  rate_limits: TierRateLimitDescriptor[];
  organization_hard_caps: OrganizationTierPolicyPreview['organization_hard_caps'];
  organization_rate_limits: TierRateLimitDescriptor[];
  capacity_pool: TierCompiledCapacityPool | null;
  capacity_pool_rate_limits: TierRateLimitDescriptor[];
  output_limit_projection?: Array<{ scope: string; limit: number; projected_output: number; next_call_blocked: boolean; basis: string }>;
  static_limit_checks: Array<TierRateLimitDescriptor & {
    amount: number;
    would_exceed_limit: boolean;
    remaining_after_amount: number;
  }>;
  snapshot: TierPolicySnapshotInfo;
}

export interface TierPolicySimulationPayload {
  callable_key: string;
  mode?: string;
  billing_mode?: TierSimulationBillingMode;
  request_count?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  input_images?: number;
  output_images?: number;
  input_characters?: number;
  output_characters?: number;
  input_audio_tokens?: number;
  output_audio_tokens?: number;
  duration_seconds?: number;
}

export interface TierCapacityDashboardOrgUsage {
  organization_id: string;
  rpm_used: number;
  tpm_used: number;
  total_usage: number;
}

export interface TierCapacityDashboardBoost {
  organization_id: string;
  weight_multiplier: number;
  reason?: string | null;
  expires_at?: string | null;
}

export interface TierCapacityDashboardPool {
  pool_key: string;
  callable_key: string;
  strategy: string;
  advanced_fair_share: boolean;
  rpm_capacity?: number | null;
  tpm_capacity?: number | null;
  rpm_used: number | null;
  tpm_used: number | null;
  rpm_saturation?: number | null;
  tpm_saturation?: number | null;
  saturation_threshold?: number | null;
  burst_multiplier?: number | null;
  member_count: number;
  active_org_count: number | null;
  top_orgs: TierCapacityDashboardOrgUsage[];
  active_boosts: TierCapacityDashboardBoost[];
  active_boost_count: number | null;
  cleanup_lagged?: boolean | null;
}

export interface TierCapacityLimitHit {
  pool_key: string;
  callable_key: string;
  organization_id?: string | null;
  scope: string;
  tier_key?: string | null;
  count: number;
}

export interface TierCapacityDashboard {
  snapshot: TierPolicySnapshotInfo;
  window_seconds: number;
  window_id: number;
  generated_at: string;
  pools: TierCapacityDashboardPool[];
  total_pool_count: number;
  scanned_pool_count: number;
  pool_scan_limit: number;
  pool_scan_truncated: boolean;
  advanced_pool_count: number;
  saturated_pool_count: number | null;
  pool_limit: number;
  truncated: boolean;
  limit_hit_count: number | null;
  limit_hit_heatmap: TierCapacityLimitHit[];
  live_data: {
    status: 'healthy' | 'partial' | 'unavailable';
    redis_available: boolean;
    failed_sections: string[];
  };
}

export interface TierCapacityBoostPayload {
  organization_id: string;
  pool_key: string;
  callable_key: string;
  weight_multiplier?: number;
  ttl_seconds?: number;
  reason?: string | null;
}
