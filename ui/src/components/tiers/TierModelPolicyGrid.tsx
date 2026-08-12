import { Edit3, Plus, Save, Trash2, X } from 'lucide-react';
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
  pricingProfileLabel,
  summarizePricing,
  TIER_PRICING_FIELDS,
  type TierCapacityPoolOption,
  type TierModelPolicyForm,
} from '../../lib/tiers';
import { TierEditorAccordion, TierField } from './TierEditorControls';
import TierPricingFields from './TierPricingFields';

type PolicyEditorSection = 'limits' | 'pricing' | 'capacity';
type PolicyEditorSections = Record<PolicyEditorSection, boolean>;

type RateLimitFormKey =
  | 'rpm_limit'
  | 'tpm_limit'
  | 'rph_limit'
  | 'rpd_limit'
  | 'tpd_limit'
  | 'max_parallel_requests'
  | 'batch_rpm_limit'
  | 'batch_tpm_limit';

const CORE_RATE_LIMIT_FIELDS: Array<{ label: string; key: RateLimitFormKey; help: string }> = [
  {
    label: 'RPM',
    key: 'rpm_limit',
    help: 'Maximum requests per minute for an organization using this model through the tier. Blank means unlimited at this tier layer. Example: 60 allows up to 60 requests in each minute.',
  },
  {
    label: 'TPM',
    key: 'tpm_limit',
    help: 'Maximum token usage per minute for an organization using this model through the tier. Blank means unlimited at this tier layer. Example: 100000 allows up to 100,000 tokens per minute.',
  },
];

const ADVANCED_RATE_LIMIT_FIELDS: Array<{ label: string; key: RateLimitFormKey; help: string }> = [
  {
    label: 'RPH',
    key: 'rph_limit',
    help: 'Maximum requests per hour. Use this alongside RPM when short bursts are acceptable but sustained hourly usage must be bounded. Blank means unlimited.',
  },
  {
    label: 'RPD',
    key: 'rpd_limit',
    help: 'Maximum requests per day. Example: 10000 permits up to 10,000 requests in the daily window. Blank means unlimited.',
  },
  {
    label: 'TPD',
    key: 'tpd_limit',
    help: 'Maximum tokens per day. Example: 5000000 permits up to five million tokens in the daily window. Blank means unlimited.',
  },
  {
    label: 'Parallel requests',
    key: 'max_parallel_requests',
    help: 'Maximum requests that may be in flight at the same time for this organization and model. Example: 5 permits five concurrent requests. Blank means unlimited.',
  },
  {
    label: 'Batch RPM',
    key: 'batch_rpm_limit',
    help: 'Maximum batch requests per minute. This is separate from synchronous RPM. Blank means unlimited at this tier layer.',
  },
  {
    label: 'Batch TPM',
    key: 'batch_tpm_limit',
    help: 'Maximum tokens submitted through batch workloads per minute. This is separate from synchronous TPM. Blank means unlimited at this tier layer.',
  },
];

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
  const [openSections, setOpenSections] = useState<PolicyEditorSections>({
    limits: true,
    pricing: false,
    capacity: false,
  });
  const [advancedLimitsOpen, setAdvancedLimitsOpen] = useState(false);
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
    const nextForm = emptyModelPolicyForm();
    setEditingIndex('new');
    setForm(nextForm);
    setOpenSections(initialOpenSections(nextForm, true));
    setAdvancedLimitsOpen(false);
    setLocalError(null);
  };

  const openEdit = (policy: TierModelPolicy) => {
    if (locked) return;
    const nextForm = modelPolicyToForm(policy, pricingProfileForCallable(policy.callable_key));
    setEditingIndex(policies.indexOf(policy));
    setForm(nextForm);
    setOpenSections(initialOpenSections(nextForm, false));
    setAdvancedLimitsOpen(hasAdvancedLimitValues(nextForm));
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
      const message = errorMessage(err, 'Failed to save model policy.');
      const section = sectionForError(message);
      setLocalError(message);
      if (section) {
        setOpenSections((current) => ({ ...current, [section]: true }));
      }
      if (section === 'limits' && isAdvancedLimitError(message)) {
        setAdvancedLimitsOpen(true);
      }
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

  const toggleSection = (section: PolicyEditorSection) => {
    setOpenSections((current) => ({ ...current, [section]: !current[section] }));
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
          <div className="space-y-3">
            <section className="rounded-xl border border-gray-200 bg-white px-4 py-4">
              <div className="mb-3">
                <h5 className="text-sm font-semibold text-gray-900">Basics</h5>
                <p className="mt-0.5 text-xs text-gray-500">Choose the callable target and whether this tier can use it.</p>
              </div>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_140px]">
                <TierField
                  id="tier-policy-callable-key"
                  label="Model or callable key"
                  help="The configured model or route name this policy applies to. Example: gpt-4o-mini. It must match a callable key exposed by the gateway."
                >
                  <input
                    id="tier-policy-callable-key"
                    list="tier-policy-callables"
                    value={form.callable_key}
                    onChange={(event) => updateCallableKey(event.target.value)}
                    placeholder="Select or enter a callable"
                    disabled={locked}
                    className={inputClassName}
                  />
                  <datalist id="tier-policy-callables">
                    {callableOptions.map((option) => <option key={option} value={option} />)}
                  </datalist>
                </TierField>
                <TierField
                  id="tier-policy-access"
                  label="Access"
                  help="Allow makes the selected model available through this policy. Deny blocks it when this policy is the effective policy for the organization and model."
                >
                  <select
                    id="tier-policy-access"
                    value={form.access_mode}
                    onChange={(event) => setForm({ ...form, access_mode: event.target.value })}
                    disabled={locked}
                    className={inputClassName}
                  >
                    <option value="allow">Allow</option>
                    <option value="deny">Deny</option>
                  </select>
                </TierField>
                <TierField
                  id="tier-policy-enabled"
                  label="Enabled"
                  help="Disabled policies remain saved in the tier version but are ignored when effective policies are compiled."
                >
                  <div className={`flex h-[38px] items-center justify-between rounded-lg border border-gray-300 bg-white px-3 ${locked ? 'cursor-not-allowed bg-gray-50 opacity-70' : ''}`}>
                    <span className="text-sm text-gray-600">{form.enabled ? 'On' : 'Off'}</span>
                    <input
                      id="tier-policy-enabled"
                      type="checkbox"
                      checked={form.enabled}
                      onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
                      disabled={locked}
                      className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                  </div>
                </TierField>
              </div>
            </section>

            <TierEditorAccordion
              title="Usage limits"
              summary={summarizeLimitForm(form)}
              description="Set organization-level limits for this model. Blank fields are unlimited at the tier layer; stricter key, team, or provider limits can still apply."
              open={openSections.limits}
              onToggle={() => toggleSection('limits')}
            >
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {CORE_RATE_LIMIT_FIELDS.map((field) => (
                  <RateLimitField
                    key={field.key}
                    field={field}
                    form={form}
                    locked={locked}
                    inputClassName={inputClassName}
                    onChange={(value) => setForm({ ...form, [field.key]: value })}
                  />
                ))}
              </div>
              <details
                key={`${editingIndex}-advanced-limits`}
                className="rounded-lg border border-gray-200 bg-gray-50 p-3"
                open={advancedLimitsOpen}
                onToggle={(event) => setAdvancedLimitsOpen(event.currentTarget.open)}
              >
                <summary className="cursor-pointer text-xs font-semibold text-gray-600">
                  Advanced limits{advancedLimitCount(form) > 0 ? ` · ${advancedLimitCount(form)} configured` : ''}
                </summary>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {ADVANCED_RATE_LIMIT_FIELDS.map((field) => (
                    <RateLimitField
                      key={field.key}
                      field={field}
                      form={form}
                      locked={locked}
                      inputClassName={inputClassName}
                      onChange={(value) => setForm({ ...form, [field.key]: value })}
                    />
                  ))}
                </div>
              </details>
            </TierEditorAccordion>

            <TierEditorAccordion
              title="Pricing"
              summary={summarizePricingForm(form)}
              description="Choose the billing shape for this model, then set only the customer prices that apply."
              open={openSections.pricing}
              onToggle={() => toggleSection('pricing')}
            >
              <TierPricingFields
                key={`${editingIndex}-pricing`}
                form={form}
                locked={locked}
                inputClassName={inputClassName}
                inferredMode={inferredMode}
                onChange={setForm}
              />
            </TierEditorAccordion>

            <TierEditorAccordion
              title="Capacity and precedence"
              summary={summarizeCapacityForm(form)}
              description="Attach scarce provider or GPU capacity to a shared pool, and adjust precedence only when overlapping tier assignments require it."
              open={openSections.capacity}
              onToggle={() => toggleSection('capacity')}
            >
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(180px,1fr)]">
                <TierField
                  id="tier-policy-capacity-pool"
                  label="Capacity pool"
                  help="A shared provider-facing ceiling used across organizations. Per-organization model limits still apply. Example: three organizations may each have 500 RPM while sharing one 1,000 RPM pool."
                >
                  <input
                    id="tier-policy-capacity-pool"
                    list="tier-policy-pools"
                    value={form.capacity_pool_key}
                    onChange={(event) => setForm({ ...form, capacity_pool_key: event.target.value })}
                    placeholder="No shared pool"
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
                  <p className="mt-1 text-xs text-gray-500">
                    {form.callable_key && matchingPoolOptions.length > 0
                      ? `${matchingPoolOptions.length} compatible pool${matchingPoolOptions.length === 1 ? '' : 's'} for this model.`
                      : form.callable_key
                        ? 'No compatible pool exists for this model. Leave blank or create one in Capacity Pools.'
                        : 'Select a model to see compatible pools.'}
                  </p>
                </TierField>
                <TierField
                  id="tier-policy-priority"
                  label="Priority"
                  help="Tie-breaker when multiple assigned tiers provide a policy for the same model. Assignment type and weight are considered first; if those tie, the higher policy priority wins. Example: 10 beats 0."
                >
                  <input
                    id="tier-policy-priority"
                    value={form.priority}
                    onChange={(event) => setForm({ ...form, priority: event.target.value })}
                    inputMode="numeric"
                    placeholder="0"
                    disabled={locked}
                    className={inputClassName}
                  />
                </TierField>
              </div>
            </TierEditorAccordion>
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

function RateLimitField({
  field,
  form,
  locked,
  inputClassName,
  onChange,
}: {
  field: { label: string; key: RateLimitFormKey; help: string };
  form: TierModelPolicyForm;
  locked: boolean;
  inputClassName: string;
  onChange: (value: string) => void;
}) {
  const inputId = `tier-policy-${field.key}`;
  return (
    <TierField id={inputId} label={field.label} help={field.help}>
      <input
        id={inputId}
        value={form[field.key]}
        onChange={(event) => onChange(event.target.value)}
        inputMode="numeric"
        placeholder="Unlimited"
        disabled={locked}
        className={inputClassName}
      />
    </TierField>
  );
}

function initialOpenSections(form: TierModelPolicyForm, isNew: boolean): PolicyEditorSections {
  return {
    limits: isNew || hasAnyLimitValues(form),
    pricing: pricingValueCount(form) > 0,
    capacity: Boolean(form.capacity_pool_key.trim()) || form.priority.trim() !== '0',
  };
}

function hasAnyLimitValues(form: TierModelPolicyForm): boolean {
  return [...CORE_RATE_LIMIT_FIELDS, ...ADVANCED_RATE_LIMIT_FIELDS]
    .some((field) => Boolean(form[field.key].trim()));
}

function hasAdvancedLimitValues(form: TierModelPolicyForm): boolean {
  return advancedLimitCount(form) > 0;
}

function advancedLimitCount(form: TierModelPolicyForm): number {
  return ADVANCED_RATE_LIMIT_FIELDS.filter((field) => Boolean(form[field.key].trim())).length;
}

function pricingValueCount(form: TierModelPolicyForm): number {
  return TIER_PRICING_FIELDS.filter((field) => Boolean(String(form[field.formField] || '').trim())).length;
}

function summarizeLimitForm(form: TierModelPolicyForm): string {
  const primary = CORE_RATE_LIMIT_FIELDS.flatMap((field) => {
    const value = form[field.key].trim();
    return value ? [`${formatInputLimit(value)} ${field.label}`] : [];
  });
  const advancedCount = advancedLimitCount(form);
  if (primary.length === 0 && advancedCount === 0) return 'Unlimited at this tier layer';
  const parts = primary.length > 0 ? primary : ['RPM/TPM unlimited'];
  if (advancedCount > 0) parts.push(`${advancedCount} advanced`);
  return parts.join(' · ');
}

function summarizePricingForm(form: TierModelPolicyForm): string {
  const count = pricingValueCount(form);
  const configured = count === 0 ? 'Not configured' : `${count} price${count === 1 ? '' : 's'} configured`;
  return `${pricingProfileLabel(form.pricing_profile)} · ${configured}`;
}

function summarizeCapacityForm(form: TierModelPolicyForm): string {
  const pool = form.capacity_pool_key.trim() || 'No shared pool';
  return `${pool} · Priority ${form.priority.trim() || '0'}`;
}

function formatInputLimit(value: string): string {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed.toLocaleString() : value;
}

function sectionForError(message: string): PolicyEditorSection | null {
  const normalized = message.toLowerCase();
  if (['price', 'pricing', 'cost', 'character', 'audio token', 'image'].some((term) => normalized.includes(term))) {
    return 'pricing';
  }
  if (['capacity', 'pool', 'priority'].some((term) => normalized.includes(term))) {
    return 'capacity';
  }
  if (['rpm', 'tpm', 'rph', 'rpd', 'tpd', 'parallel', 'batch'].some((term) => normalized.includes(term))) {
    return 'limits';
  }
  return null;
}

function isAdvancedLimitError(message: string): boolean {
  const normalized = message.toLowerCase();
  return ['rph', 'rpd', 'tpd', 'parallel', 'batch'].some((term) => normalized.includes(term));
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
