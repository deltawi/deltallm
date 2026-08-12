export type TierPolicyMode = 'disabled' | 'shadow' | 'enforce';

export function normalizeTierPolicyMode(value: unknown): TierPolicyMode {
  return value === 'enforce' || value === 'shadow' ? value : 'disabled';
}

export function organizationUsesTier(
  mode: TierPolicyMode,
  legacyMigrationException: boolean,
): boolean {
  if (mode === 'disabled') return false;
  if (mode === 'enforce') return true;
  return !legacyMigrationException;
}

export function organizationTierIsRequired(mode: TierPolicyMode): boolean {
  return mode === 'enforce';
}
