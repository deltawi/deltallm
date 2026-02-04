export interface User {
  id: string;
  email: string;
  first_name?: string;
  last_name?: string;
  is_superuser: boolean;
  is_active: boolean;
  last_login_at?: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  description?: string;
  max_budget?: number;
  spend: number;
  settings: Record<string, any>;
  created_at: string;
  updated_at: string;
  member_count?: number;
  team_count?: number;
}

export interface Team {
  id: string;
  name: string;
  slug: string;
  org_id: string;
  organization?: Organization;
  description?: string;
  max_budget?: number;
  spend: number;
  settings: Record<string, any>;
  created_at: string;
  updated_at: string;
  member_count?: number;
}

export interface OrgMember {
  id: string;
  user_id: string;
  user: User;
  org_id: string;
  role: 'owner' | 'admin' | 'member' | 'viewer';
  joined_at: string;
}

export interface TeamMember {
  id: string;
  user_id: string;
  user: User;
  team_id: string;
  role: 'admin' | 'member';
  joined_at: string;
}

export interface BudgetStatus {
  entity_type: string;
  entity_id: string;
  entity_name?: string;
  max_budget?: number;
  current_spend: number;
  remaining_budget?: number;
  budget_utilization_percent?: number;
  is_exceeded: boolean;
}

export interface SpendLog {
  id: string;
  request_id: string;
  api_key_id?: string;
  user_id?: string;
  team_id?: string;
  org_id?: string;
  model: string;
  provider?: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  spend: number;
  latency_ms?: number;
  status: string;
  error_message?: string;
  request_tags: string[];
  created_at: string;
}

export interface SpendSummary {
  total_spend: number;
  total_requests: number;
  total_tokens: number;
  successful_requests: number;
  failed_requests: number;
  avg_latency_ms?: number;
  top_models: Array<{
    model: string;
    requests: number;
    spend: number;
  }>;
  daily_breakdown: Array<{
    date: string;
    spend: number;
    requests: number;
  }>;
}

export interface AuditLog {
  id: string;
  org_id?: string;
  user_id?: string;
  user?: User;
  action: string;
  resource_type?: string;
  resource_id?: string;
  old_values?: Record<string, any>;
  new_values?: Record<string, any>;
  ip_address?: string;
  user_agent?: string;
  created_at: string;
}

export interface ApiKey {
  id: string;
  key_hash: string;
  key_alias?: string;
  user_id?: string;
  team_id?: string;
  org_id?: string;
  models?: string[];
  max_budget?: number;
  spend: number;
  tpm_limit?: number;
  rpm_limit?: number;
  expires_at?: string;
  created_at: string;
}

export interface LoginCredentials {
  username: string;
  password: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export type CreateOrganizationRequest = {
  name: string;
  slug: string;
  description?: string;
  max_budget?: number;
};

export type UpdateOrganizationRequest = Partial<CreateOrganizationRequest>;

export type CreateTeamRequest = {
  name: string;
  slug: string;
  org_id: string;
  description?: string;
  max_budget?: number;
};

export type UpdateTeamRequest = Partial<Omit<CreateTeamRequest, 'org_id'>>;

export type AddMemberRequest = {
  user_id: string;
  role?: string;
};

export type CreateApiKeyRequest = {
  key_alias?: string;
  org_id?: string;
  team_id?: string;
  models?: string[];
  max_budget?: number;
  tpm_limit?: number;
  rpm_limit?: number;
  expires_at?: string;
};

export type CreateApiKeyResponse = {
  key: string;
  key_hash: string;
};

// Models
export interface ModelInfo {
  id: string;
  object: 'model';
  created: number;
  owned_by: string;
  max_tokens?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
  supports_vision: boolean;
  supports_tools: boolean;
  supports_streaming: boolean;
  provider?: string;
}

export interface ModelList {
  object: 'list';
  data: ModelInfo[];
}

// Model Deployments
export interface ModelDeployment {
  id: string;
  model_name: string;
  provider_model: string;
  provider_config_id?: string | null;  // Now optional for standalone deployments
  provider_type?: string | null;       // Provider type (openai, anthropic, etc.)
  model_type: string;                  // Model type (chat, embedding, image_generation, etc.)
  api_base?: string | null;            // Custom API base URL
  org_id?: string;
  is_active: boolean;
  priority: number;
  tpm_limit?: number;
  rpm_limit?: number;
  timeout?: number;
  settings: Record<string, any>;
  provider_name?: string;
  created_at: string;
  updated_at: string;
}

export interface ModelDeploymentWithProvider extends ModelDeployment {
  provider?: ProviderConfig;
}

// Deployment creation modes:
// 1. Linked mode: provider_config_id required, uses provider's API key
// 2. Standalone mode: provider_config_id=null, requires provider_type and api_key
export type CreateDeploymentRequest = {
  model_name: string;
  provider_model: string;
  // Mode selection
  provider_config_id?: string | null;  // null for standalone deployments
  // Standalone deployment fields (required when provider_config_id is null)
  provider_type?: string;              // e.g., 'openai', 'anthropic'
  api_key?: string;                    // API key for standalone deployments
  api_base?: string;                   // Optional custom API base URL
  // Model type classification
  model_type?: string;                 // Model type: chat, embedding, image_generation, etc.
  // Common fields
  org_id?: string;
  is_active?: boolean;
  priority?: number;
  tpm_limit?: number;
  rpm_limit?: number;
  timeout?: number;
  settings?: Record<string, any>;
};

export type UpdateDeploymentRequest = Partial<Omit<CreateDeploymentRequest, 'org_id'>>;

// Providers
export interface ProviderConfig {
  id: string;
  name: string;
  provider_type: string;
  api_base?: string;
  org_id?: string;
  is_active: boolean;
  tpm_limit?: number;
  rpm_limit?: number;
  settings: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface ProviderHealthStatus {
  provider_id: string;
  name: string;
  provider_type: string;
  is_active: boolean;
  is_healthy: boolean;
  latency_ms?: number;
  last_check?: string;
  error_message?: string;
}

export interface ProviderTestResult {
  success: boolean;
  latency_ms?: number;
  error_message?: string;
  model_list?: string[];
}

export type CreateProviderRequest = {
  name: string;
  provider_type: string;
  api_key?: string;
  api_base?: string;
  org_id?: string;
  is_active?: boolean;
  tpm_limit?: number;
  rpm_limit?: number;
  settings?: Record<string, any>;
};

export type UpdateProviderRequest = Partial<CreateProviderRequest>;

// Guardrails
export interface GuardrailPolicy {
  id: string;
  name: string;
  description: string;
  blocked_topics: string[];
  allowed_topics: string[];
  enable_pii_filter: boolean;
  enable_toxicity_filter: boolean;
  enable_injection_filter: boolean;
  pii_action: string;
  toxicity_action: string;
  injection_action: string;
  custom_blocked_patterns: string[];
  violation_action: string;
  alert_on_violation: boolean;
}

export interface GuardrailsStatus {
  org_id: string;
  active_policy_id: string | null;
  active_policy_name: string | null;
  policies_available: string[];
  total_requests_checked: number;
  total_violations: number;
}

export interface ContentCheckRequest {
  content: string;
  policy_id?: string;
}

export interface ContentCheckResponse {
  allowed: boolean;
  action: string;
  message?: string;
  filtered_content?: string;
  violations: Array<{
    policy_id: string;
    policy_name: string;
    severity: string;
    message: string;
  }>;
}

export interface SetOrgPolicyResponse {
  success: boolean;
  org_id: string;
  policy_id: string;
  policy_name: string;
}

// Pricing
export interface ModelPricing {
  model: string;
  mode: string;
  input_cost_per_token: string;
  output_cost_per_token: string;
  cache_creation_input_token_cost?: string;
  cache_read_input_token_cost?: string;
  image_cost_per_image?: string;
  image_sizes?: Record<string, string>;
  quality_pricing?: Record<string, number>;
  audio_cost_per_character?: string;
  audio_cost_per_minute?: string;
  rerank_cost_per_search?: string;
  batch_discount_percent?: number;
  base_model?: string;
  max_tokens?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
  source: string;
  org_id?: string;
  team_id?: string;
}

export interface PricingCreateRequest {
  mode: 'chat' | 'embedding' | 'image_generation' | 'audio_speech' | 'audio_transcription' | 'rerank' | 'moderation' | 'batch';
  input_cost_per_token?: string;
  output_cost_per_token?: string;
  cache_creation_input_token_cost?: string;
  cache_read_input_token_cost?: string;
  image_cost_per_image?: string;
  audio_cost_per_character?: string;
  audio_cost_per_minute?: string;
  rerank_cost_per_search?: string;
  max_tokens?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
}
