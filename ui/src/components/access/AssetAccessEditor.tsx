import { useMemo, useState } from 'react';
import type { AssetAccessTarget } from '../../lib/api';

type AssetAccessMode = 'grant' | 'inherit' | 'restrict';
type TargetFilter = 'all' | 'model' | 'route_group';

type Props = {
  title?: string;
  description?: string;
  mode: AssetAccessMode;
  allowModeSelection?: boolean;
  onModeChange?: (mode: AssetAccessMode) => void;
  targets: AssetAccessTarget[];
  selectedKeys: string[];
  onSelectedKeysChange: (keys: string[]) => void;
  loading?: boolean;
  disabled?: boolean;
  searchValue?: string;
  onSearchValueChange?: (value: string) => void;
  targetTypeFilter?: TargetFilter;
  onTargetTypeFilterChange?: (value: TargetFilter) => void;
  pagination?: {
    total: number;
    limit: number;
    offset: number;
    has_more: boolean;
  };
  onPageChange?: (offset: number) => void;
  primaryActionLabel?: string;
  onPrimaryAction?: () => void;
  secondaryActionLabel?: string;
  onSecondaryAction?: () => void;
};

function badgeClasses(kind: 'model' | 'route_group') {
  if (kind === 'route_group') return 'bg-amber-50 text-amber-700 border-amber-200';
  return 'bg-blue-50 text-blue-700 border-blue-200';
}

export default function AssetAccessEditor({
  title = 'Asset Access',
  description,
  mode,
  allowModeSelection = false,
  onModeChange,
  targets,
  selectedKeys,
  onSelectedKeysChange,
  loading = false,
  disabled = false,
  searchValue,
  onSearchValueChange,
  targetTypeFilter,
  onTargetTypeFilterChange,
  pagination,
  onPageChange,
  primaryActionLabel,
  onPrimaryAction,
  secondaryActionLabel,
  onSecondaryAction,
}: Props) {
  const [localSearch, setLocalSearch] = useState('');
  const [localTargetType, setLocalTargetType] = useState<TargetFilter>('all');
  const search = searchValue ?? localSearch;
  const targetType = targetTypeFilter ?? localTargetType;
  const usesRemoteFiltering = !!onSearchValueChange || !!onTargetTypeFilterChange || !!pagination;

  const filteredTargets = useMemo(() => {
    if (usesRemoteFiltering) return targets;
    const query = search.trim().toLowerCase();
    return targets.filter((target) => {
      if (targetType !== 'all' && target.target_type !== targetType) return false;
      if (!query) return true;
      return target.callable_key.toLowerCase().includes(query);
    });
  }, [search, targetType, targets]);

  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);

  const toggleKey = (callableKey: string) => {
    if (disabled || mode === 'inherit') return;
    const next = new Set(selectedSet);
    if (next.has(callableKey)) {
      next.delete(callableKey);
    } else {
      next.add(callableKey);
    }
    onSelectedKeysChange(Array.from(next).sort());
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        {description && <p className="mt-1 text-xs text-gray-500">{description}</p>}
      </div>

      {allowModeSelection && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <label className={`rounded-lg border px-3 py-2 text-sm ${mode === 'inherit' ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
            <div className="flex items-start gap-2">
              <input
                type="radio"
                name={`${title}-mode`}
                checked={mode === 'inherit'}
                onChange={() => onModeChange?.('inherit')}
                disabled={disabled}
                className="mt-0.5"
              />
              <span>
                <span className="block font-medium text-gray-900">Inherit parent access</span>
                <span className="block text-xs text-gray-500">Use the full allowed set from the parent scope.</span>
              </span>
            </div>
          </label>
          <label className={`rounded-lg border px-3 py-2 text-sm ${mode === 'restrict' ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
            <div className="flex items-start gap-2">
              <input
                type="radio"
                name={`${title}-mode`}
                checked={mode === 'restrict'}
                onChange={() => onModeChange?.('restrict')}
                disabled={disabled}
                className="mt-0.5"
              />
              <span>
                <span className="block font-medium text-gray-900">Restrict to selected assets</span>
                <span className="block text-xs text-gray-500">Only the assets selected below remain callable.</span>
              </span>
            </div>
          </label>
        </div>
      )}

      {mode === 'inherit' && allowModeSelection && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          Direct asset selection is disabled while this scope inherits access from its parent.
        </div>
      )}

      {(primaryActionLabel || secondaryActionLabel) && (
        <div className="flex flex-wrap items-center gap-2">
          {primaryActionLabel && onPrimaryAction && (
            <button
              type="button"
              onClick={onPrimaryAction}
              disabled={disabled}
              className="rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {primaryActionLabel}
            </button>
          )}
          {secondaryActionLabel && onSecondaryAction && (
            <button
              type="button"
              onClick={onSecondaryAction}
              disabled={disabled}
              className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {secondaryActionLabel}
            </button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_11rem] gap-3">
        <input
          value={search}
          onChange={(event) => {
            const next = event.target.value;
            if (onSearchValueChange) {
              onSearchValueChange(next);
            } else {
              setLocalSearch(next);
            }
          }}
          placeholder="Search models and route groups..."
          disabled={disabled}
          className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 disabled:text-gray-500"
        />
        <select
          value={targetType}
          onChange={(event) => {
            const next = event.target.value as TargetFilter;
            if (onTargetTypeFilterChange) {
              onTargetTypeFilterChange(next);
            } else {
              setLocalTargetType(next);
            }
          }}
          disabled={disabled}
          className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 disabled:text-gray-500"
        >
          <option value="all">All targets</option>
          <option value="model">Models</option>
          <option value="route_group">Route groups</option>
        </select>
      </div>

      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>{selectedKeys.length} selected</span>
        <span>
          {pagination ? `${pagination.total} total` : `${targets.filter((target) => target.selectable).length} selectable`}
        </span>
      </div>

      <div className="max-h-72 overflow-y-auto rounded-lg border border-gray-200 bg-white">
        {loading ? (
          <div className="px-4 py-6 text-sm text-gray-500">Loading assets...</div>
        ) : filteredTargets.length === 0 ? (
          <div className="px-4 py-6 text-sm text-gray-500">No assets match the current filter.</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {filteredTargets.map((target) => {
              const checked = selectedSet.has(target.callable_key);
              const checkboxDisabled = disabled || mode === 'inherit' || (!target.selectable && !checked);
              return (
                <label
                  key={target.callable_key}
                  className={`flex items-start gap-3 px-4 py-3 text-sm ${checkboxDisabled ? 'cursor-not-allowed bg-gray-50/60' : 'cursor-pointer hover:bg-gray-50'}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleKey(target.callable_key)}
                    disabled={checkboxDisabled}
                    className="mt-0.5"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-gray-900 break-all">{target.callable_key}</span>
                      <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${badgeClasses(target.target_type)}`}>
                        {target.target_type === 'route_group' ? 'Route Group' : 'Model'}
                      </span>
                      {target.inherited_only && (
                        <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                          Inherited
                        </span>
                      )}
                      {checked && target.selectable && (
                        <span className="inline-flex items-center rounded-full bg-green-50 px-2 py-0.5 text-[11px] font-medium text-green-700">
                          Selected
                        </span>
                      )}
                      {!target.selectable && (
                        <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                          Outside parent scope
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-gray-500">
                      {target.effective_visible
                        ? 'Currently visible at runtime.'
                        : target.selectable
                          ? 'Available to assign.'
                          : 'Not currently selectable from the parent scope.'}
                    </p>
                  </div>
                </label>
              );
            })}
          </div>
        )}
      </div>

      {pagination && onPageChange && (
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>
            {pagination.total === 0
              ? 'No assets'
              : `${pagination.offset + 1}-${Math.min(pagination.offset + pagination.limit, pagination.total)} of ${pagination.total}`}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onPageChange(Math.max(0, pagination.offset - pagination.limit))}
              disabled={disabled || pagination.offset <= 0}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              onClick={() => onPageChange(pagination.offset + pagination.limit)}
              disabled={disabled || !pagination.has_more}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
