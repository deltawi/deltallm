import { Edit3, Plus, Save, Search, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { Pagination, TierCapacityPool, TierCapacityPoolPayload } from '../../lib/api';
import {
  capacityPoolFormWithStrategy,
  capacityPoolFormToPayload,
  capacityPoolToForm,
  emptyCapacityPoolForm,
  errorMessage,
  formatLimit,
  type TierCapacityPoolForm,
} from '../../lib/tiers';
import { TierField } from './TierEditorControls';
import TierEditorDrawer from './TierEditorDrawer';
import TierPagination from './TierPagination';

type TierCapacityPoolEditorProps = {
  pools: TierCapacityPool[];
  pagination: Pagination;
  pageSize: number;
  searchInput: string;
  strategyFilter: 'all' | 'hard_cap' | 'weighted_fair' | 'reserved_burst';
  callableOptions?: string[];
  readOnly: boolean;
  saving: boolean;
  error: string | null;
  conflict?: string | null;
  onSearchInputChange: (value: string) => void;
  onStrategyFilterChange: (value: 'all' | 'hard_cap' | 'weighted_fair' | 'reserved_burst') => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onCreate: (pool: TierCapacityPoolPayload) => Promise<void>;
  onUpdate: (existing: TierCapacityPool, pool: TierCapacityPoolPayload) => Promise<void>;
  onDelete: (pool: TierCapacityPool) => Promise<void>;
  onReviewLatest?: () => void;
  onDiscardConflict?: () => void;
};

export default function TierCapacityPoolEditor({
  pools,
  pagination,
  pageSize,
  searchInput,
  strategyFilter,
  callableOptions = [],
  readOnly,
  saving,
  error,
  conflict,
  onSearchInputChange,
  onStrategyFilterChange,
  onPageChange,
  onPageSizeChange,
  onCreate,
  onUpdate,
  onDelete,
  onReviewLatest,
  onDiscardConflict,
}: TierCapacityPoolEditorProps) {
  const [editingPool, setEditingPool] = useState<TierCapacityPool | 'new' | null>(null);
  const [form, setForm] = useState<TierCapacityPoolForm>(emptyCapacityPoolForm());
  const [localError, setLocalError] = useState<string | null>(null);
  const locked = readOnly || saving;
  const inputClassName = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500';

  const sortedPools = useMemo(
    () => [...pools].sort((a, b) => `${a.pool_key}:${a.callable_key}`.localeCompare(`${b.pool_key}:${b.callable_key}`)),
    [pools],
  );
  const latestEditingPool = editingPool && typeof editingPool === 'object'
    ? pools.find((pool) => pool.tier_capacity_pool_id === editingPool.tier_capacity_pool_id) || null
    : null;
  const conflictDifferences = latestEditingPool
    ? poolFormDifferences(capacityPoolToForm(latestEditingPool), form)
    : [];

  const openNew = () => {
    if (locked) return;
    setEditingPool('new');
    setForm(emptyCapacityPoolForm());
    setLocalError(null);
  };

  const openEdit = (pool: TierCapacityPool) => {
    if (locked) return;
    setEditingPool(pool);
    setForm(capacityPoolToForm(pool));
    setLocalError(null);
  };

  const saveForm = async () => {
    if (locked) return;
    try {
      const existing = editingPool && typeof editingPool === 'object'
        ? pools.find((pool) => pool.tier_capacity_pool_id === editingPool.tier_capacity_pool_id) || editingPool
        : null;
      const payload = capacityPoolFormToPayload(form, existing);
      if (!payload.pool_key || !payload.callable_key) {
        setLocalError('Pool key and model key are required.');
        return;
      }
      setLocalError(null);
      if (editingPool === 'new') {
        await onCreate(payload);
      } else if (existing) {
        await onUpdate(existing, payload);
      }
      setEditingPool(null);
      setForm(emptyCapacityPoolForm());
    } catch (err: unknown) {
      setLocalError(errorMessage(err, 'Failed to save capacity pool.'));
    }
  };

  const removePool = async (pool: TierCapacityPool) => {
    if (locked) return;
    if (!confirm(`Remove capacity pool ${pool.pool_key} for ${pool.callable_key}?`)) return;
    try {
      setLocalError(null);
      await onDelete(pool);
    } catch (err: unknown) {
      setLocalError(errorMessage(err, 'Failed to remove capacity pool.'));
    }
  };

  const updateStrategy = (strategy: string) => {
    setForm(capacityPoolFormWithStrategy(form, strategy));
  };

  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Capacity Pools</h3>
          <p className="mt-0.5 text-xs text-gray-500">Define shared capacity envelopes that model policies can reference.</p>
        </div>
        {!readOnly ? (
          <button
            type="button"
            onClick={openNew}
            disabled={locked}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-xs font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            Add pool
          </button>
        ) : null}
      </div>

      {error || localError ? (
        <div className="mx-4 mt-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
          {localError || error}
        </div>
      ) : null}

      {conflict ? (
        <div className="mx-4 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900" role="alert">
          <p className="font-semibold">Another admin changed this draft.</p>
          <p className="mt-0.5 text-xs">{conflict} Your unsaved pool fields are still open.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {onReviewLatest ? (
              <button type="button" onClick={onReviewLatest} className="rounded-md bg-amber-900 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-amber-950">Review latest</button>
            ) : null}
            {onDiscardConflict ? (
              <button
                type="button"
                onClick={() => {
                  setEditingPool(null);
                  setForm(emptyCapacityPoolForm());
                  setLocalError(null);
                  onDiscardConflict();
                }}
                className="rounded-md border border-amber-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100"
              >
                Discard my unsaved changes
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="flex flex-col gap-2 border-b border-gray-100 px-4 py-3 sm:flex-row">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">Search capacity pools</span>
          <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
          <input
            value={searchInput}
            onChange={(event) => onSearchInputChange(event.target.value)}
            placeholder="Search pool or model"
            className="w-full rounded-lg border border-gray-300 py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
          />
        </label>
        <select
          aria-label="Filter capacity pools by strategy"
          value={strategyFilter}
          onChange={(event) => onStrategyFilterChange(event.target.value as typeof strategyFilter)}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
        >
          <option value="all">All strategies</option>
          <option value="hard_cap">Hard cap</option>
          <option value="weighted_fair">Weighted fair</option>
          <option value="reserved_burst">Reserved burst</option>
        </select>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-400">
            <tr>
              <th className="px-4 py-2 text-left">Pool</th>
              <th className="px-4 py-2 text-left">Model</th>
              <th className="px-4 py-2 text-left">RPM</th>
              <th className="px-4 py-2 text-left">TPM</th>
              <th className="px-4 py-2 text-left">Parallel</th>
              <th className="px-4 py-2 text-left">Strategy</th>
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sortedPools.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-400">No capacity pools yet.</td>
              </tr>
            ) : sortedPools.map((pool) => (
              <tr key={`${pool.pool_key}:${pool.callable_key}`}>
                <td className="px-4 py-3 font-mono text-xs text-gray-700">{pool.pool_key}</td>
                <td className="px-4 py-3 font-mono text-xs text-gray-700">{pool.callable_key}</td>
                <td className="px-4 py-3 text-xs text-gray-600">{formatLimit(pool.rpm_capacity)}</td>
                <td className="px-4 py-3 text-xs text-gray-600">{formatLimit(pool.tpm_capacity)}</td>
                <td className="px-4 py-3 text-xs text-gray-600">{formatLimit(pool.max_parallel_requests)}</td>
                <td className="px-4 py-3 text-xs font-semibold text-gray-700">{pool.strategy}</td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-1">
                    <button
                      type="button"
                      onClick={() => openEdit(pool)}
                      disabled={locked}
                      className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40"
                      aria-label={`Edit ${pool.pool_key} for ${pool.callable_key}`}
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removePool(pool)}
                      disabled={locked}
                      className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      aria-label={`Remove ${pool.pool_key} for ${pool.callable_key}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <TierPagination
        pagination={pagination}
        pageSize={pageSize}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        disabled={saving}
        itemLabel="capacity pools"
      />

      {editingPool !== null ? (
        <TierEditorDrawer
          title={editingPool === 'new' ? 'Add capacity pool' : `Edit ${editingPool.pool_key}`}
          description="Set the shared model capacity envelope and its fair-share behavior."
          saving={saving}
          onClose={() => setEditingPool(null)}
        >
          {conflict ? (
            <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900" role="alert">
              <p className="font-semibold">Another admin changed this draft.</p>
              <p className="mt-0.5 text-xs">{conflict} Your unsaved pool fields remain in this editor.</p>
              {editingPool !== 'new' ? (
                latestEditingPool ? (
                  <PoolConflictDifferences differences={conflictDifferences} />
                ) : (
                  <p className="mt-2 rounded-md bg-white/70 px-2 py-1.5 text-xs">The pool is not present on this server page. It may have been removed or moved out of the current filters.</p>
                )
              ) : null}
              <div className="mt-2 flex flex-wrap gap-2">
                {onReviewLatest ? <button type="button" onClick={onReviewLatest} className="rounded-md bg-amber-900 px-2.5 py-1.5 text-xs font-semibold text-white">Review latest</button> : null}
                {onDiscardConflict ? (
                  <button
                    type="button"
                    onClick={() => {
                      setEditingPool(null);
                      setForm(emptyCapacityPoolForm());
                      setLocalError(null);
                      onDiscardConflict();
                    }}
                    className="rounded-md border border-amber-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-amber-900"
                  >
                    Discard my unsaved changes
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
          {error || localError ? (
            <div className="mb-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {localError || error}
            </div>
          ) : null}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
            <TierField
              id="tier-pool-key"
              label="Pool key"
              help="Stable name referenced by model policies. Example: shared-premium-chat. Pool keys may be reused for different models because the model key is part of the pool identity."
            >
              <input
                id="tier-pool-key"
                value={form.pool_key}
                onChange={(event) => setForm({ ...form, pool_key: event.target.value })}
                placeholder="shared-chat"
                disabled={locked || editingPool !== 'new'}
                className={inputClassName}
              />
            </TierField>
            <TierField
              id="tier-pool-callable-key"
              label="Model or callable key"
              help="The model whose provider or GPU capacity is shared by this pool. It must match the callable key used by attached model policies."
            >
              <input
                id="tier-pool-callable-key"
                list="tier-pool-callables"
                value={form.callable_key}
                onChange={(event) => setForm({ ...form, callable_key: event.target.value })}
                placeholder="Select or enter a callable"
                disabled={locked || editingPool !== 'new'}
                className={inputClassName}
              />
              <datalist id="tier-pool-callables">
                {callableOptions.map((option) => <option key={option} value={option} />)}
              </datalist>
            </TierField>
            <TierField
              id="tier-pool-strategy"
              label="Strategy"
              help="Hard cap enforces only the shared ceiling. Weighted fair starts protecting each active organization's share near saturation. Reserved burst adds a bounded multiplier to that fair share while the pool hard cap still wins."
            >
              <select
                id="tier-pool-strategy"
                value={form.strategy}
                onChange={(event) => updateStrategy(event.target.value)}
                disabled={locked}
                className={inputClassName}
              >
                <option value="hard_cap">Hard cap</option>
                <option value="weighted_fair">Weighted fair</option>
                <option value="reserved_burst">Reserved burst</option>
              </select>
            </TierField>
            <TierField
              id="tier-pool-parallel"
              label="Parallel capacity"
              help="Maximum in-flight requests shared by every organization attached to this model pool. Example: 20 allows twenty concurrent requests across the whole pool. Blank means unlimited."
            >
              <input
                id="tier-pool-parallel"
                value={form.max_parallel_requests}
                onChange={(event) => setForm({ ...form, max_parallel_requests: event.target.value })}
                inputMode="numeric"
                placeholder="Unlimited"
                disabled={locked}
                className={inputClassName}
              />
            </TierField>
          </div>
          <div className={`mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 ${form.strategy === 'hard_cap' ? 'xl:grid-cols-2' : form.strategy === 'reserved_burst' ? 'xl:grid-cols-4' : 'xl:grid-cols-3'}`}>
            <TierField
              id="tier-pool-rpm-capacity"
              label="RPM capacity"
              help="Provider-facing requests-per-minute ceiling shared across attached organizations. Example: 1000 means the whole pool can accept up to 1,000 requests per minute. Blank means unlimited."
            >
              <input
                id="tier-pool-rpm-capacity"
                value={form.rpm_capacity}
                onChange={(event) => setForm({ ...form, rpm_capacity: event.target.value })}
                inputMode="numeric"
                placeholder="Unlimited"
                disabled={locked}
                className={inputClassName}
              />
            </TierField>
            <TierField
              id="tier-pool-tpm-capacity"
              label="TPM capacity"
              help="Provider-facing tokens-per-minute ceiling shared across attached organizations. Example: 2000000 limits the whole pool to two million tokens per minute. Blank means unlimited."
            >
              <input
                id="tier-pool-tpm-capacity"
                value={form.tpm_capacity}
                onChange={(event) => setForm({ ...form, tpm_capacity: event.target.value })}
                inputMode="numeric"
                placeholder="Unlimited"
                disabled={locked}
                className={inputClassName}
              />
            </TierField>
            {form.strategy === 'weighted_fair' || form.strategy === 'reserved_burst' ? (
              <TierField
                id="tier-pool-saturation-threshold"
                label="Saturation threshold"
                help="Pool-utilization ratio above which per-organization fair shares begin to apply. The default 0.85 means organizations can borrow idle capacity until the pool is more than 85% utilized."
              >
                <input
                  id="tier-pool-saturation-threshold"
                  value={form.saturation_threshold}
                  onChange={(event) => setForm({ ...form, saturation_threshold: event.target.value })}
                  inputMode="decimal"
                  placeholder="0.85"
                  disabled={locked}
                  className={inputClassName}
                />
              </TierField>
            ) : null}
            {form.strategy === 'reserved_burst' ? (
              <TierField
                id="tier-pool-burst-multiplier"
                label="Burst multiplier"
                help="Multiplier applied to an organization's saturated fair share. The default 1.2 permits up to 20% above that share, but never above the shared pool hard cap."
              >
                <input
                  id="tier-pool-burst-multiplier"
                  value={form.burst_multiplier}
                  onChange={(event) => setForm({ ...form, burst_multiplier: event.target.value })}
                  inputMode="decimal"
                  placeholder="1.2"
                  disabled={locked}
                  className={inputClassName}
                />
              </TierField>
            ) : null}
          </div>
          <p className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-800">
            {capacityStrategyDescription(
              form.strategy,
              form.saturation_threshold,
              form.burst_multiplier,
            )}
          </p>
          <div className="mt-4 flex justify-end">
            <button
              type="button"
              onClick={saveForm}
              disabled={locked}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              {editingPool === 'new' ? 'Add pool' : 'Save pool'}
            </button>
          </div>
        </TierEditorDrawer>
      ) : null}
    </section>
  );
}

function capacityStrategyDescription(
  strategy: string,
  saturationThreshold: string,
  burstMultiplier: string,
): string {
  const threshold = saturationThreshold.trim() || '0.85';
  const thresholdPercent = Number.isFinite(Number(threshold))
    ? `${Math.round(Number(threshold) * 100)}%`
    : threshold;
  const multiplier = burstMultiplier.trim() || '1.2';
  if (strategy === 'weighted_fair') {
    return `Weighted fair: active organizations may borrow idle capacity below ${thresholdPercent} utilization; above it, assignment weights determine protected shares.`;
  }
  if (strategy === 'reserved_burst') {
    return `Reserved burst: weighted fair sharing applies at ${thresholdPercent} utilization, with a ${multiplier}× burst entitlement that still cannot exceed the pool ceiling.`;
  }
  return 'Hard cap: only the shared RPM, TPM, and parallel ceilings are enforced; no per-organization fair-share calculation is applied.';
}

type PoolFormDifference = { field: string; server: string; local: string };

function poolFormDifferences(
  server: TierCapacityPoolForm,
  local: TierCapacityPoolForm,
): PoolFormDifference[] {
  return (Object.keys(local) as Array<keyof TierCapacityPoolForm>).flatMap((field) => {
    if (server[field] === local[field]) return [];
    return [{
      field: field.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase()),
      server: server[field].trim() || 'Blank',
      local: local[field].trim() || 'Blank',
    }];
  });
}

function PoolConflictDifferences({ differences }: { differences: PoolFormDifference[] }) {
  if (differences.length === 0) {
    return <p className="mt-2 text-xs">The visible row now matches your open fields; refresh may have affected another pool.</p>;
  }
  return (
    <div className="mt-2 overflow-hidden rounded-md border border-amber-200 bg-white/80">
      <div className="grid grid-cols-[minmax(100px,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 border-b border-amber-100 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-amber-800">
        <span>Field</span><span>Server now</span><span>Your value</span>
      </div>
      {differences.map((difference) => (
        <div key={difference.field} className="grid grid-cols-[minmax(100px,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 border-b border-amber-100 px-2 py-1.5 text-[11px] last:border-b-0">
          <span className="font-semibold">{difference.field}</span>
          <span className="truncate" title={difference.server}>{difference.server}</span>
          <span className="truncate" title={difference.local}>{difference.local}</span>
        </div>
      ))}
    </div>
  );
}
