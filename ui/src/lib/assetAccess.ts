import type {
  AssetAccessTarget,
  AssetVisibilityTarget,
  CallableTargetListItem,
  ScopedAssetAccess,
} from './api';

type AssetAccessMode = ScopedAssetAccess['mode'];

function compareTargets(a: { callable_key: string }, b: { callable_key: string }) {
  return a.callable_key.localeCompare(b.callable_key);
}

export function buildCatalogAssetTargets(
  items: Array<Pick<CallableTargetListItem, 'callable_key' | 'target_type'>>,
  selectedKeys: string[],
): AssetAccessTarget[] {
  const selected = new Set(selectedKeys);
  return [...items]
    .sort(compareTargets)
    .map((item) => ({
      callable_key: item.callable_key,
      target_type: item.target_type,
      selectable: true,
      selected: selected.has(item.callable_key),
      effective_visible: selected.has(item.callable_key),
      inherited_only: false,
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
      effective_visible: mode === 'inherit' ? true : selected.has(item.callable_key),
      inherited_only: mode === 'inherit',
    }));
}

export function buildScopedSelectableTargets(
  items: Array<Pick<AssetAccessTarget, 'callable_key' | 'target_type' | 'selectable'>>,
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
      effective_visible: mode === 'inherit' ? item.selectable : selected.has(item.callable_key),
      inherited_only: mode === 'inherit' && item.selectable,
    }));
}
