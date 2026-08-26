import { apiFetch } from './apiClient';
import type { UIBranding } from './branding';

export type UIBrandingResetResponse = UIBranding & {
  reconciliation_pending: boolean;
};

export function resetBranding(): Promise<UIBrandingResetResponse> {
  return apiFetch<UIBrandingResetResponse>('/ui/api/branding/reset', { method: 'POST' });
}
