export interface TeamRecord {
  team_id: string;
  team_alias?: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  organization_lifecycle_state?: string | null;
  max_budget?: number | null;
  spend?: number | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  output_tpm_limit?: number | null;
  model_output_tpm_limit?: Record<string, number> | null;
  rph_limit?: number | null;
  rpd_limit?: number | null;
  tpd_limit?: number | null;
  blocked?: boolean;
  member_count?: number;
  self_service_keys_enabled?: boolean;
  self_service_max_keys_per_user?: number | null;
  self_service_budget_ceiling?: number | null;
  self_service_require_expiry?: boolean;
  self_service_max_expiry_days?: number | null;
  capabilities?: Record<string, boolean>;
  created_at?: string | null;
  updated_at?: string | null;
  [key: string]: unknown;
}

export interface TeamMemberRecord {
  user_id: string;
  user_email?: string | null;
  user_role: string;
  spend: number;
}

export interface TeamMemberCandidate {
  account_id: string;
  email?: string | null;
  organization_role?: string | null;
  team_role?: string | null;
  already_member?: boolean;
}
