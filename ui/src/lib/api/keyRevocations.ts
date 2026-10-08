import { apiFetch } from './transport';

export interface KeyRemovalResult {
  enforcement?: 'enforced' | 'pending';
  invalidation_id?: string;
  maximum_enforcement_delay_seconds?: number;
}

export function keyRevocationStatus(id: string, signal?: AbortSignal): Promise<KeyRemovalResult> {
  return apiFetch(`/ui/api/key-revocations/${encodeURIComponent(id)}`, { signal });
}
