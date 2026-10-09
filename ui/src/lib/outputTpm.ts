export const OUTPUT_TPM_HELP = 'Counts output after text calls finish. New calls are blocked when recorded usage reaches the limit. Admitted calls can exceed it. The counter resets at UTC minute boundaries. Blank means no limit at this scope.';

export function parseOutputTpm(value: string): number | null {
  if (!value.trim()) return null;
  if (!/^\d+$/.test(value.trim())) throw new Error('Output TPM must be a positive whole number.');
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > 2147483647) {
    throw new Error('Output TPM must be between 1 and 2,147,483,647.');
  }
  return parsed;
}

export function reconcileRuntimeOutputTpm(
  current: Principal | null, accountId: string, userId: string, limit: number | null,
): Principal | null {
  if (!current || current.account_id !== accountId || current.runtime_user?.user_id !== userId) return current;
  return { ...current, runtime_user: { ...current.runtime_user, output_tpm_limit: limit } };
}
import type { Principal } from './api';
