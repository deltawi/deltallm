import { Edit3, Plus, Save, Trash2, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { useMemo, useState } from 'react';
import type { TierModelPolicy } from '../../lib/api';
import {
  emptyModelPolicyForm,
  errorMessage,
  formatLimit,
  modelPolicyFormToPayload,
  modelPolicyToForm,
  poolOptionsForCallable,
  pricingProfileForModelMode,
  summarizePricing,
  type TierCapacityPoolOption,
  type TierModelPolicyForm,
} from '../../lib/tiers';
import TierPricingFields from './TierPricingFields';

type TierModelPolicyGridProps = {
  policies: TierModelPolicy[];
  poolOptions: TierCapacityPoolOption[];
  callableOptions?: string[];
  callableModes?: Record<string, string>;
  readOnly: boolean;
  saving: boolean;
  error: string | null;
  onSave: (policies: TierModelPolicy[]) => Promise<void>;
};

export default function TierModelPolicyGrid({
  policies,
  poolOptions,
  callableOptions = [],
  callableModes = {},
  readOnly,
  saving,
  error,
  onSave,
}: TierModelPolicyGridProps) {
  const [editingIndex, setEditingIndex] = useState<number | 'new' | null>(null);
  const [form, setForm] = useState<TierModelPolicyForm>(emptyModelPolicyForm());
  const [localError, setLocalError] = useState<string | null>(null);
  const [bulk, setBulk] = useState({ rpm_limit: '', tpm_limit: '' });
  const locked = readOnly || saving;
  const inputClassName = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500';

  const sortedPolicies = useMemo(
    () => [...policies].sort((a, b) => a.callable_key.localeCompare(b.callable_key)),
    [policies],
  );
  const matchingPoolOptions = useMemo(
    () => poolOptionsForCallable(poolOptions, form.callable_key),
    [form.callable_key, poolOptions],
  );
  const inferredMode = form.callable_key ? callableModes[form.callable_key] || null : null;

  const pricingProfileForCallable = (callableKey: string) => {
    const mode = callableModes[callableKey];
    return mode ? pricingProfileForModelMode(mode) : null;
  };

  const openNew = () => {
    if (locked) return;
    setEditingIndex('new');
    setForm(emptyModelPolicyForm());
    setLocalError(null);
  };

  const openEdit = (policy: TierModelPolicy) => {
    if (locked) return;
    setEditingIndex(policies.indexOf(policy));
    setForm(modelPolicyToForm(policy, pricingProfileForCallable(policy.callable_key)));
    setLocalError(null);
  };

  const saveForm = async () => {
    if (locked) return;
    try {
      const existing = typeof editingIndex === 'number' ? policies[editingIndex] : null;
      const payload = modelPolicyFormToPayload(form, existing);
      if (!payload.callable_key) {
        setLocalError('Model or callable key is required.');
        return;
      }
      setLocalError(null);
      const next = editingIndex === 'new'
        ? [...policies, payload]
        : policies.map((item, index) => index === editingIndex ? payload : item);
      await onSave(next);
      setEditingIndex(null);
      setForm(emptyModelPolicyForm());
    } catch (err: unknown) {
      setLocalError(errorMessage(err, 'Failed to save model policy.'));
    }
  };

  const removePolicy = async (policy: TierModelPolicy) => {
    if (locked) return;
    if (!confirm(`Remove policy for ${policy.callable_key}?`)) return;
    try {
      setLocalError(null);
      await onSave(policies.filter((item) => item !== policy));
    } catch (err: unknown) {
      setLocalError(errorMessage(err, 'Failed to remove model policy.'));
    }
  };

  const applyBulk = async () => {
    if (locked) return;
    try {
      const next = policies.map((policy) => {
        const policyForm = modelPolicyToForm(policy);
        return modelPolicyFormToPayload({
          ...policyForm,
          rpm_limit: bulk.rpm_limit.trim() ? bulk.rpm_limit : policyForm.rpm_limit,
          tpm_limit: bulk.tpm_limit.trim() ? bulk.tpm_limit : policyForm.tpm_limit,
        }, policy);
      });
      setLocalError(null);
      await onSave(next);
      setBulk({ rpm_limit: '', tpm_limit: '' });
    } catch (err: unknown) {
      setLocalError(errorMessage(err, 'Failed to apply bulk policy updates.'));
    }
  };

  const updateCallableKey = (callableKey: string) => {
    if (locked) return;
    const matchingPools = poolOptionsForCallable(poolOptions, callableKey);
    const keepPool = matchingPools.some((option) => option.pool_key === form.capacity_pool_key);
    const inferredPricingProfile = pricingProfileForCallable(callableKey);
    setForm({
      ...form,
      callable_key: callableKey,
      pricing_profile: inferredPricingProfile || form.pricing_profile,
      capacity_pool_key: keepPool ? form.capacity_pool_key : '',
    });
  };

  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Model Policies</h3>
          <p className="mt-0.5 text-xs text-gray-500">Control access, customer pricing, RPM/TPM, and pool membership per model.</p>
        </div>
        {!readOnly ? (
          <button
            type="button"
            onClick={openNew}
            disabled={locked}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            Add policy
          </button>
        ) : null}
      </div>

      {error || localError ? (
        <div className="mx-4 mt-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
          {localError || error}
        </div>
      ) : null}

      {!readOnly && policies.length > 0 ? (
        <div className="m-4 rounded-xl border border-gray-100 bg-gray-50 p-3">
          <div className="mb-2 text-xs font-semibold uppercase text-gray-400">Bulk apply</div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
            <BulkInput label="RPM" value={bulk.rpm_limit} disabled={locked} onChange={(value) => setBulk({ ...bulk, rpm_limit: value })} />
            <BulkInput label="TPM" value={bulk.tpm_limit} disabled={locked} onChange={(value) => setBulk({ ...bulk, tpm_limit: value })} />
            <button
              type="button"
              onClick={applyBulk}
              disabled={locked || !Object.values(bulk).some((value) => value.trim())}
              className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Apply
            </button>
          </div>
        </div>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-400">
            <tr>
              <th className="px-4 py-2 text-left">Model</th>
              <th className="px-4 py-2 text-left">Access</th>
              <th className="px-4 py-2 text-left">RPM</th>
              <th className="px-4 py-2 text-left">TPM</th>
              <th className="px-4 py-2 text-left">Pricing</th>
              <th className="px-4 py-2 text-left">Pool</th>
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sortedPolicies.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-400">No model policies yet.</td>
              </tr>
            ) : sortedPolicies.map((policy) => (
              <tr key={`${policy.callable_key}:${policy.priority}`}>
                <td className="px-4 py-3 font-mono text-xs text-gray-700">{policy.callable_key}</td>
                <td className="px-4 py-3 text-xs font-semibold text-gray-700">{policy.access_mode}</td>
                <td className="px-4 py-3 text-xs text-gray-600">{formatLimit(policy.rpm_limit)}</td>
                <td className="px-4 py-3 text-xs text-gray-600">{formatLimit(policy.tpm_limit)}</td>
                <td className="max-w-xs px-4 py-3 text-xs text-gray-600">
                  {summarizePricing(policy.pricing, pricingProfileForCallable(policy.callable_key))}
                </td>
                <td className="px-4 py-3 text-xs text-gray-500">{policy.capacity_pool_key || '-'}</td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-1">
                    <button
                      type="button"
                      onClick={() => openEdit(policy)}
                      disabled={locked}
                      className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40"
                      title="Edit policy"
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removePolicy(policy)}
                      disabled={locked}
                      className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      title="Remove policy"
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
            <h4 className="text-sm font-semibold text-gray-900">{editingIndex === 'new' ? 'Add policy' : 'Edit policy'}</h4>
            <button type="button" onClick={() => setEditingIndex(null)} disabled={saving} className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="space-y-4">
            <FormSection
              title="Target and Access"
              description="Choose the callable target and whether this tier allows or denies it."
            >
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_120px_120px]">
                <Field label="Model or callable key">
                  <input
                    list="tier-policy-callables"
                    value={form.callable_key}
                    onChange={(event) => updateCallableKey(event.target.value)}
                    disabled={locked}
                    className={inputClassName}
                  />
                  <datalist id="tier-policy-callables">
                    {callableOptions.map((option) => <option key={option} value={option} />)}
                  </datalist>
                </Field>
                <Field label="Access">
                  <select
                    value={form.access_mode}
                    onChange={(event) => setForm({ ...form, access_mode: event.target.value })}
                    disabled={locked}
                    className={inputClassName}
                  >
                    <option value="allow">Allow</option>
                    <option value="deny">Deny</option>
                  </select>
                </Field>
                <Field label="Priority">
                  <input value={form.priority} onChange={(event) => setForm({ ...form, priority: event.target.value })} disabled={locked} className={inputClassName} />
                </Field>
                <label className={`flex items-center justify-between rounded-lg border border-gray-200 bg-white px-3 py-2 ${locked ? 'cursor-not-allowed opacity-70' : ''}`}>
                  <span className="text-xs font-semibold text-gray-500">Enabled</span>
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
                    disabled={locked}
                    className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                </label>
              </div>
            </FormSection>

            <FormSection
              title="Capacity"
              description="Attach this model policy to a shared pool when provider or GPU capacity is scarce."
            >
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
                <Field label="Capacity pool">
                  <input
                    list="tier-policy-pools"
                    value={form.capacity_pool_key}
                    onChange={(event) => setForm({ ...form, capacity_pool_key: event.target.value })}
                    disabled={locked}
                    className={inputClassName}
                  />
                  <datalist id="tier-policy-pools">
                    {matchingPoolOptions.map((option) => (
                      <option
                        key={`${option.pool_key}:${option.callable_key}`}
                        value={option.pool_key}
                      />
                    ))}
                  </datalist>
                </Field>
                <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-500">
                  {form.callable_key && matchingPoolOptions.length > 0
                    ? `${matchingPoolOptions.length} compatible pool${matchingPoolOptions.length === 1 ? '' : 's'} for this model.`
                    : form.callable_key
                      ? 'No compatible pool is defined for this model. Leave empty or create a matching pool above.'
                      : 'Select a model to see compatible pools.'}
                </div>
              </div>
            </FormSection>

            <FormSection
              title="Rate Limits"
              description="Set model-level limits for organizations assigned to this tier. Blank means unlimited at this tier layer."
            >
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
                {[
                  ['RPM', 'rpm_limit'],
                  ['TPM', 'tpm_limit'],
                  ['RPH', 'rph_limit'],
                  ['RPD', 'rpd_limit'],
                  ['TPD', 'tpd_limit'],
                  ['Parallel', 'max_parallel_requests'],
                  ['Batch RPM', 'batch_rpm_limit'],
                  ['Batch TPM', 'batch_tpm_limit'],
                ].map(([label, key]) => (
                  <Field key={key} label={label}>
                    <input
                      value={form[key as keyof TierModelPolicyForm] as string}
                      onChange={(event) => setForm({ ...form, [key]: event.target.value })}
                      disabled={locked}
                      className={inputClassName}
                    />
                  </Field>
                ))}
              </div>
            </FormSection>

            <TierPricingFields
              form={form}
              locked={locked}
              inputClassName={inputClassName}
              inferredMode={inferredMode}
              onChange={setForm}
            />
          </div>

          <div className="mt-4 flex justify-end">
            <button
              type="button"
              onClick={saveForm}
              disabled={locked}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              Save policies
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3 border-b border-gray-200 pb-4 last:border-b-0">
      <div>
        <h5 className="text-sm font-semibold text-gray-900">{title}</h5>
        <p className="mt-0.5 text-xs text-gray-500">{description}</p>
      </div>
      {children}
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

function BulkInput({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="sr-only">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={label}
        disabled={disabled}
        className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500"
      />
    </label>
  );
}
