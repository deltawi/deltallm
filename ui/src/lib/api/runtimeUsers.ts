import { apiFetch } from './transport';

export interface RuntimeOutputPolicyResult {
  user_id: string;
  output_tpm_limit: number | null;
}

export function updateRuntimeOutputTpm(userId: string, limit: number | null, signal?: AbortSignal) {
  return apiFetch<RuntimeOutputPolicyResult>(`/ui/api/users/${encodeURIComponent(userId)}`, {
    method: 'PUT', json: { output_tpm_limit: limit }, signal,
  });
}
