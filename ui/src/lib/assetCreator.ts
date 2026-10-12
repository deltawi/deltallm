import type { AssetCreatorFields, AssetCreatorKind } from './api/managedAssets';

export type AssetCreatorDisplay =
  | { kind: 'account'; accountId: string; label: string | null }
  | { kind: 'label'; label: string };

const LABELS: Record<Exclude<AssetCreatorKind, 'account'>, string> = {
  master_key: 'Master key',
  system: 'System',
  unknown: 'Not recorded',
};

export function assetCreatorDisplay(row: AssetCreatorFields): AssetCreatorDisplay {
  const accountId = row.created_by_user_id || null;
  if (accountId) return { kind: 'account', accountId, label: row.created_by_email || null };
  const kind = row.created_by_kind || 'unknown';
  // The account row was deleted, so only the creator kind remains.
  if (kind === 'account') return { kind: 'label', label: 'Deleted account' };
  return { kind: 'label', label: LABELS[kind] ?? LABELS.unknown };
}
