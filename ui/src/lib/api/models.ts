import { apiFetch, withQuery } from './transport';

export type ProviderHealthStatus = 'healthy' | 'degraded' | 'down';

export interface ProviderHealthSummaryRow {
  provider: string;
  models: number;
  healthy_models: number;
  unhealthy_models: number;
  status: ProviderHealthStatus;
}

export interface ProviderHealthSummary {
  total_models: number;
  providers: ProviderHealthSummaryRow[];
  summary: {
    total_providers: number;
    active_providers: number;
    down_providers: number;
  };
}

export interface ProviderPreset {
  provider: string;
  api_base: string | null;
  compat: string;
  supported_modes: string[];
}

export interface ProviderModelOption {
  id: string;
  label: string;
  provider: string;
  source: 'catalog' | 'provider_api' | 'catalog+provider_api';
  supported_modes: string[];
  known_metadata: Record<string, number | null> | null;
}

export interface ProviderModelDiscoveryPayload {
  provider: string;
  mode?: string | null;
  named_credential_id?: string | null;
  api_key?: string | null;
  api_base?: string | null;
  api_version?: string | null;
  auth_header_name?: string | null;
  auth_header_format?: string | null;
}

export interface ProviderModelDiscoveryResponse {
  data: ProviderModelOption[];
  warnings: string[];
}

export interface DeploymentHealth {
  healthy: boolean;
  in_cooldown: boolean;
  consecutive_failures: number;
  last_error: string | null;
  last_error_at: number | null;
  last_success_at: number | null;
}

export interface ModelRuntimeParams extends Record<string, unknown> {
  model?: string;
  api_base?: string;
  api_version?: string;
  timeout?: number | null;
  stream_timeout?: number | null;
  max_tokens?: number | null;
  rpm?: number | null;
  tpm?: number | null;
  weight?: number | null;
}

export interface ModelInfo extends Record<string, unknown> {
  mode?: string;
  max_tokens?: number | null;
  max_input_tokens?: number | null;
  max_output_tokens?: number | null;
  output_vector_size?: number | null;
  upstream_max_batch_inputs?: number | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  weight?: number | null;
  priority?: number | null;
  tags?: string[];
  access_groups?: string[];
  default_params?: Record<string, unknown>;
  batch_capacity?: {
    max_in_flight?: number | null;
    max_claim_work_units?: number | null;
    capacity_fraction?: number | null;
  };
  input_cost_per_token?: number | null;
  output_cost_per_token?: number | null;
  input_cost_per_image?: number | null;
  input_cost_per_character?: number | null;
  output_cost_per_character?: number | null;
  input_cost_per_second?: number | null;
  output_cost_per_second?: number | null;
  input_cost_per_audio_token?: number | null;
  output_cost_per_audio_token?: number | null;
  batch_price_multiplier?: number | null;
  batch_input_cost_per_token?: number | null;
  batch_output_cost_per_token?: number | null;
}

export interface ModelDeploymentDetail {
  deployment_id: string;
  model_name: string;
  provider: string;
  mode?: string;
  credential_source?: 'inline' | 'named';
  named_credential_id?: string | null;
  named_credential_name?: string | null;
  inline_credentials_present?: boolean;
  connection_summary?: {
    api_base?: string | null;
    api_version?: string | null;
    region?: string | null;
    auth_header_name?: string | null;
    custom_auth_label?: string | null;
  };
  healthy?: boolean;
  health?: DeploymentHealth;
  deltallm_params: ModelRuntimeParams;
  model_info: ModelInfo;
}

export interface ModelWritePayload {
  model_name: string;
  named_credential_id?: string | null;
  deltallm_params: Record<string, unknown>;
  model_info: Record<string, unknown>;
}

export interface ModelMutationResponse extends ModelDeploymentDetail {
  warnings: string[];
}

export interface ModelDeleteResponse {
  deleted: boolean;
  warnings: string[];
}

export interface ModelListResponse {
  data: ModelDeploymentDetail[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
    has_more: boolean;
  };
}

export const models = {
  list: (
    params?: {
      search?: string;
      provider?: string;
      mode?: string;
      limit?: number;
      offset?: number;
    },
    signal?: AbortSignal,
  ) => apiFetch<ModelListResponse>(withQuery('/ui/api/models', params), { signal }),
  providerHealthSummary: (signal?: AbortSignal) =>
    apiFetch<ProviderHealthSummary>('/ui/api/models/provider-health-summary', { signal }),
  providerPresets: (signal?: AbortSignal) =>
    apiFetch<{ data: ProviderPreset[] }>('/ui/api/provider-presets', { signal }),
  discoverProviderModels: (payload: ProviderModelDiscoveryPayload, signal?: AbortSignal) =>
    apiFetch<ProviderModelDiscoveryResponse>('/ui/api/provider-models/discover', {
      method: 'POST',
      json: payload,
      signal,
    }),
  get: (deploymentId: string, signal?: AbortSignal) =>
    apiFetch<ModelDeploymentDetail>(`/ui/api/models/${encodeURIComponent(deploymentId)}`, {
      signal,
    }),
  checkHealth: (deploymentId: string, signal?: AbortSignal) =>
    apiFetch<{
      deployment_id: string;
      healthy: boolean;
      health: DeploymentHealth;
      message: string;
      status_code?: number | null;
      checked_at: number;
    }>(`/ui/api/models/${encodeURIComponent(deploymentId)}/health-check`, {
      method: 'POST',
      signal,
    }),
  create: (payload: ModelWritePayload, signal?: AbortSignal) =>
    apiFetch<ModelMutationResponse>('/ui/api/models', {
      method: 'POST',
      json: payload,
      signal,
    }),
  update: (deploymentId: string, payload: ModelWritePayload, signal?: AbortSignal) =>
    apiFetch<ModelMutationResponse>(`/ui/api/models/${encodeURIComponent(deploymentId)}`, {
      method: 'PUT',
      json: payload,
      signal,
    }),
  delete: (deploymentId: string, signal?: AbortSignal) =>
    apiFetch<ModelDeleteResponse>(`/ui/api/models/${encodeURIComponent(deploymentId)}`, {
      method: 'DELETE',
      signal,
    }),
};
