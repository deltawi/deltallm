import { apiFetch } from './transport';
import { backgroundRequestHeaders } from '../sessionActivity';

export interface KeyRemovalResult {
  enforcement?: 'enforced' | 'pending';
  invalidation_id?: string;
  maximum_enforcement_delay_seconds?: number;
}

export function keyRevocationStatus(id: string, signal?: AbortSignal): Promise<KeyRemovalResult> {
  // Only ever polled, so it must not keep an idle session signed in.
  return apiFetch(`/ui/api/key-revocations/${encodeURIComponent(id)}`, {
    signal,
    headers: backgroundRequestHeaders(),
  });
}
