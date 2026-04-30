import type {
  AssetAccessGroup,
  AssetAccessTarget,
  AssetVisibilityResponse,
  AssetVisibilityTarget,
  CallableTargetAccessGroupListItem,
  CallableTargetListItem,
  ScopedAssetAccess,
} from './api';

type AssetAccessMode = ScopedAssetAccess['mode'];
type AssetAccessIdentity = {
  scopeType?: ScopedAssetAccess['scope_type'];
  scopeId?: string | null;
  organizationId?: string | null;
  teamId?: string | null;
  apiKeyId?: string | null;
  userId?: string | null;
};

export function assetAccessLoadErrorMessage(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error && error.message.trim()
    ? error.message
    : 'Asset access options failed to load. Refresh and try again.';
}

function compareTargets(a: { callable_key: string }, b: { callable_key: string }) {
  return a.callable_key.localeCompare(b.callable_key);
}

function compareGroups(a: { group_key: string }, b: { group_key: string }) {
  return a.group_key.localeCompare(b.group_key);
}

function groupCallableKeys(item: CallableTargetAccessGroupListItem | AssetAccessGroup): string[] {
  if ('callable_keys' in item && Array.isArray(item.callable_keys)) {
    return item.callable_keys;
  }
  if ('members' in item && Array.isArray(item.members)) {
    return item.members.map((member) => member.callable_key);
  }
  return [];
}

function normalizedId(value: string | null | undefined): string | null {
  const normalized = String(value ?? '').trim();
  return normalized || null;
}

function idMatches(actual: string | null | undefined, expected: string | null | undefined): boolean {
  if (expected === undefined) return true;
  return normalizedId(actual) === normalizedId(expected);
}

export function isAssetVisibilityFor(
  response: AssetVisibilityResponse | null | undefined,
  expected: AssetAccessIdentity,
): response is AssetVisibilityResponse {
  if (!response) return false;
  return (
    idMatches(response.organization_id, expected.organizationId) &&
    idMatches(response.team_id, expected.teamId) &&
    idMatches(response.api_key_id, expected.apiKeyId) &&
    idMatches(response.user_id, expected.userId)
  );
}

export function isScopedAssetAccessFor(
  response: ScopedAssetAccess | null | undefined,
  expected: AssetAccessIdentity,
): response is ScopedAssetAccess {
  if (!response) return false;
  return (
    (expected.scopeType === undefined || response.scope_type === expected.scopeType) &&
    idMatches(response.scope_id, expected.scopeId) &&
    idMatches(response.organization_id, expected.organizationId) &&
    idMatches(response.team_id, expected.teamId) &&
    idMatches(response.api_key_id, expected.apiKeyId) &&
    idMatches(response.user_id, expected.userId)
  );
}

export function buildCatalogAssetTargets(
  items: Array<Pick<CallableTargetListItem, 'callable_key' | 'target_type'>>,
  selectedKeys: string[],
  persistedSelectedKeys: string[] = [],
): AssetAccessTarget[] {
  const selected = new Set(selectedKeys);
  const persisted = new Set(persistedSelectedKeys);
  return [...items]
    .sort(compareTargets)
    .map((item) => ({
      callable_key: item.callable_key,
      target_type: item.target_type,
      selectable: true,
      selected: selected.has(item.callable_key),
      effective_visible: persisted.has(item.callable_key),
      inherited_only: false,
    }));
}

export function buildCatalogAccessGroups(
  items: CallableTargetAccessGroupListItem[],
  selectedKeys: string[],
  persistedSelectedKeys: string[] = [],
): AssetAccessGroup[] {
  const selected = new Set(selectedKeys);
  const persisted = new Set(persistedSelectedKeys);
  return [...items]
    .sort(compareGroups)
    .map((item) => ({
      group_key: item.group_key,
      member_count: item.member_count,
      selectable: true,
      selected: selected.has(item.group_key),
      effective_visible: persisted.has(item.group_key),
      callable_keys: groupCallableKeys(item),
    }));
}

export function buildParentScopedAssetTargets(
  items: Array<Pick<AssetVisibilityTarget, 'callable_key' | 'target_type' | 'effective_visible'>>,
  selectedKeys: string[],
  mode: AssetAccessMode,
): AssetAccessTarget[] {
  const selected = new Set(selectedKeys);
  return [...items]
    .filter((item) => item.effective_visible)
    .sort(compareTargets)
    .map((item) => ({
      callable_key: item.callable_key,
      target_type: item.target_type,
      selectable: true,
      selected: selected.has(item.callable_key),
      effective_visible: mode === 'inherit',
      inherited_only: mode === 'inherit',
    }));
}

export function buildScopedSelectableTargets(
  items: Array<Pick<AssetAccessTarget, 'callable_key' | 'target_type' | 'selectable'> & Partial<AssetAccessTarget>>,
  selectedKeys: string[],
  mode: AssetAccessMode,
): AssetAccessTarget[] {
  const selected = new Set(selectedKeys);
  return [...items]
    .sort(compareTargets)
    .map((item) => ({
      callable_key: item.callable_key,
      target_type: item.target_type,
      selectable: item.selectable,
      selected: selected.has(item.callable_key),
      effective_visible: Boolean(item.effective_visible),
      inherited_only: mode === 'inherit' && item.selectable,
      via_access_groups: item.via_access_groups,
    }));
}

export function buildScopedSelectableAccessGroups(
  items: Array<Pick<AssetAccessGroup, 'group_key' | 'member_count' | 'selectable' | 'effective_visible'> & Partial<AssetAccessGroup>>,
  selectedKeys: string[],
): AssetAccessGroup[] {
  const selected = new Set(selectedKeys);
  return [...items]
    .sort(compareGroups)
    .map((item) => {
      const isSelected = selected.has(item.group_key);
      return {
        group_key: item.group_key,
        member_count: item.member_count,
        selectable: item.selectable,
        selected: isSelected,
        effective_visible: Boolean(item.effective_visible),
        callable_keys: item.callable_keys,
      };
    });
}
