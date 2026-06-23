import { Edit3, Plus, Save, Trash2, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { useMemo, useState } from 'react';
import type { TierCapacityPool } from '../../lib/api';
import {
  capacityPoolFormToPayload,
  capacityPoolToForm,
  emptyCapacityPoolForm,
  errorMessage,
  formatLimit,
  type TierCapacityPoolForm,
} from '../../lib/tiers';

type TierCapacityPoolEditorProps = {
  pools: TierCapacityPool[];
  callableOptions?: string[];
  readOnly: boolean;
  saving: boolean;
  error: string | null;
  onSave: (pools: TierCapacityPool[]) => Promise<void>;
};

export default function TierCapacityPoolEditor({
  pools,
  callableOptions = [],
  readOnly,
  saving,
  error,
  onSave,
}: TierCapacityPoolEditorProps) {
  const [editingIndex, setEditingIndex] = useState<number | 'new' | null>(null);
  const [form, setForm] = useState<TierCapacityPoolForm>(emptyCapacityPoolForm());
  const [localError, setLocalError] = useState<string | null>(null);
  const locked = readOnly || saving;
  const inputClassName = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500';

  const sortedPools = useMemo(
    () => [...pools].sort((a, b) => `${a.pool_key}:${a.callable_key}`.localeCompare(`${b.pool_key}:${b.callable_key}`)),
    [pools],
  );

  const openNew = () => {
    if (locked) return;
    setEditingIndex('new');
    setForm(emptyCapacityPoolForm());
    setLocalError(null);
  };

  const openEdit = (pool: TierCapacityPool) => {
    if (locked) return;
    setEditingIndex(pools.indexOf(pool));
    setForm(capacityPoolToForm(pool));
    setLocalError(null);
  };

  const saveForm = async () => {
    if (locked) return;
    try {
      const existing = typeof editingIndex === 'number' ? pools[editingIndex] : null;
      const payload = capacityPoolFormToPayload(form, existing);
      if (!payload.pool_key || !payload.callable_key) {
        setLocalError('Pool key and model key are required.');
        return;
      }
      setLocalError(null);
      const next = editingIndex === 'new'
        ? [...pools, payload]
        : pools.map((item, index) => index === editingIndex ? payload : item);
      await onSave(next);
      setEditingIndex(null);
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
      await onSave(pools.filter((item) => item !== pool));
    } catch (err: unknown) {
      setLocalError(errorMessage(err, 'Failed to remove capacity pool.'));
    }
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
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
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
                      title="Edit pool"
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removePool(pool)}
                      disabled={locked}
                      className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      title="Remove pool"
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

      {editingIndex !== null ? (
        <div className="border-t border-gray-100 bg-gray-50 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-gray-900">{editingIndex === 'new' ? 'Add pool' : 'Edit pool'}</h4>
            <button
              type="button"
              onClick={() => setEditingIndex(null)}
              disabled={saving}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <Field label="Pool key">
              <input
                value={form.pool_key}
                onChange={(event) => setForm({ ...form, pool_key: event.target.value })}
                placeholder="shared-chat"
                disabled={locked}
                className={inputClassName}
              />
            </Field>
            <Field label="Model or callable key">
              <input
                list="tier-pool-callables"
                value={form.callable_key}
                onChange={(event) => setForm({ ...form, callable_key: event.target.value })}
                disabled={locked}
                className={inputClassName}
              />
              <datalist id="tier-pool-callables">
                {callableOptions.map((option) => <option key={option} value={option} />)}
              </datalist>
            </Field>
            <Field label="Strategy">
              <select
                value={form.strategy}
                onChange={(event) => setForm({ ...form, strategy: event.target.value })}
                disabled={locked}
                className={inputClassName}
              >
                <option value="hard_cap">Hard cap</option>
                <option value="weighted_fair">Weighted fair</option>
                <option value="reserved_burst">Reserved burst</option>
              </select>
            </Field>
            <Field label="Parallel">
              <input
                value={form.max_parallel_requests}
                onChange={(event) => setForm({ ...form, max_parallel_requests: event.target.value })}
                disabled={locked}
                className={inputClassName}
              />
            </Field>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="RPM capacity">
              <input
                value={form.rpm_capacity}
                onChange={(event) => setForm({ ...form, rpm_capacity: event.target.value })}
                disabled={locked}
                className={inputClassName}
              />
            </Field>
            <Field label="TPM capacity">
              <input
                value={form.tpm_capacity}
                onChange={(event) => setForm({ ...form, tpm_capacity: event.target.value })}
                disabled={locked}
                className={inputClassName}
              />
            </Field>
            <Field label="Saturation threshold">
              <input
                value={form.saturation_threshold}
                onChange={(event) => setForm({ ...form, saturation_threshold: event.target.value })}
                placeholder="0.9"
                disabled={locked}
                className={inputClassName}
              />
            </Field>
            <Field label="Burst multiplier">
              <input
                value={form.burst_multiplier}
                onChange={(event) => setForm({ ...form, burst_multiplier: event.target.value })}
                placeholder="1.2"
                disabled={locked}
                className={inputClassName}
              />
            </Field>
          </div>
          <div className="mt-4 flex justify-end">
            <button
              type="button"
              onClick={saveForm}
              disabled={locked}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              Save pools
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-gray-500">{label}</span>
      {children}
    </label>
  );
}
