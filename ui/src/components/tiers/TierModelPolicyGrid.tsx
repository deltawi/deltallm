import { Edit3, Plus, Save, Search, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { Pagination, TierModelPolicy, TierModelPolicyPayload } from '../../lib/api';
import {
  emptyModelPolicyForm,
  errorMessage,
  formatLimit,
  modelPolicyFormToPayload,
  modelPolicyToForm,
  parsePositiveIntegerInput,
  poolOptionsForCallable,
  pricingProfileForModelMode,
  pricingProfileLabel,
  summarizePricing,
  TIER_PRICING_FIELDS,
  type TierCapacityPoolOption,
  type TierModelPolicyForm,
} from '../../lib/tiers';
import { TierEditorAccordion, TierField } from './TierEditorControls';
import TierEditorDrawer from './TierEditorDrawer';
import TierPricingFields from './TierPricingFields';
import TierPagination from './TierPagination';

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
  pagination: Pagination;
  pageSize: number;
  searchInput: string;
  enabledFilter: 'all' | 'enabled' | 'disabled';
  accessFilter: 'all' | 'allow' | 'deny';
  view?: 'limits' | 'pricing';
  poolOptions: TierCapacityPoolOption[];
  callableOptions?: string[];
  callableModes?: Record<string, string>;
  callableModeConflicts?: Record<string, boolean>;
  readOnly: boolean;
  saving: boolean;
  error: string | null;
  conflict?: string | null;
  onSearchInputChange: (value: string) => void;
  onEnabledFilterChange: (value: 'all' | 'enabled' | 'disabled') => void;
  onAccessFilterChange: (value: 'all' | 'allow' | 'deny') => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onCreate: (policy: TierModelPolicyPayload) => Promise<void>;
  onUpdate: (existing: TierModelPolicy, policy: TierModelPolicyPayload) => Promise<void>;
  onDelete: (policy: TierModelPolicy) => Promise<void>;
  onBulkLimits: (limits: { rpm_limit?: number; tpm_limit?: number }) => Promise<void>;
  onLoadPoolOptions?: (
    callableKey: string,
    search: string,
  ) => Promise<{ options: TierCapacityPoolOption[]; hasMore: boolean }>;
  onReviewLatest?: () => void;
  onDiscardConflict?: () => void;
};

export default function TierModelPolicyGrid({
  policies,
  pagination,
  pageSize,
  searchInput,
  enabledFilter,
  accessFilter,
  view = 'limits',
  poolOptions,
  callableOptions = [],
  callableModes = {},
  callableModeConflicts = {},
  readOnly,
  saving,
  error,
  conflict,
  onSearchInputChange,
  onEnabledFilterChange,
  onAccessFilterChange,
  onPageChange,
  onPageSizeChange,
  onCreate,
  onUpdate,
  onDelete,
  onBulkLimits,
  onLoadPoolOptions,
  onReviewLatest,
  onDiscardConflict,
}: TierModelPolicyGridProps) {
  const [editingPolicy, setEditingPolicy] = useState<TierModelPolicy | 'new' | null>(null);
  const [form, setForm] = useState<TierModelPolicyForm>(emptyModelPolicyForm());
  const [localError, setLocalError] = useState<string | null>(null);
  const [bulk, setBulk] = useState({ rpm_limit: '', tpm_limit: '' });
  const [openSections, setOpenSections] = useState<PolicyEditorSections>({
    limits: true,
    pricing: false,
    capacity: false,
  });
  const [advancedLimitsOpen, setAdvancedLimitsOpen] = useState(false);
  const [remotePoolOptions, setRemotePoolOptions] = useState<TierCapacityPoolOption[]>([]);
  const [poolLookupLoading, setPoolLookupLoading] = useState(false);
  const [poolLookupHasMore, setPoolLookupHasMore] = useState(false);
  const [poolLookupError, setPoolLookupError] = useState<string | null>(null);
  const locked = readOnly || saving;
  const inputClassName = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500';

  const sortedPolicies = useMemo(
    () => [...policies].sort((a, b) => a.callable_key.localeCompare(b.callable_key)),
    [policies],
  );
  const matchingPoolOptions = useMemo(
    () => poolOptionsForCallable([...poolOptions, ...remotePoolOptions], form.callable_key),
    [form.callable_key, poolOptions, remotePoolOptions],
  );
  const inferredMode = form.callable_key ? callableModes[form.callable_key] || null : null;
  const modeInferenceUnavailable = form.callable_key && !inferredMode
    ? callableModeConflicts[form.callable_key]
      ? 'Deployments for this callable report conflicting modes. Choose the pricing profile explicitly.'
      : 'This callable does not report a model mode. Choose the pricing profile explicitly.'
    : null;

  const pricingProfileForCallable = (callableKey: string) => {
    const mode = callableModes[callableKey];
    return mode ? pricingProfileForModelMode(mode) : null;
  };
  const latestEditingPolicy = editingPolicy && typeof editingPolicy === 'object'
    ? policies.find((policy) => policy.tier_model_policy_id === editingPolicy.tier_model_policy_id) || null
    : null;
  const conflictDifferences = latestEditingPolicy
    ? formDifferences(
        modelPolicyToForm(
          latestEditingPolicy,
          pricingProfileForCallable(latestEditingPolicy.callable_key),
        ),
        form,
      )
    : [];

  useEffect(() => {
    const callableKey = form.callable_key.trim();
    if (!editingPolicy || !callableKey || !onLoadPoolOptions) return undefined;

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setPoolLookupLoading(true);
      setPoolLookupError(null);
      try {
        const result = await onLoadPoolOptions(callableKey, form.capacity_pool_key.trim());
        if (cancelled) return;
        setRemotePoolOptions(result.options);
        setPoolLookupHasMore(result.hasMore);
      } catch (err: unknown) {
        if (cancelled) return;
        setRemotePoolOptions([]);
        setPoolLookupHasMore(false);
        setPoolLookupError(errorMessage(err, 'Compatible pools could not be loaded.'));
      } finally {
        if (!cancelled) setPoolLookupLoading(false);
      }
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [editingPolicy, form.callable_key, form.capacity_pool_key, onLoadPoolOptions]);

  const openNew = () => {
    if (locked) return;
    const nextForm = emptyModelPolicyForm();
    setEditingPolicy('new');
    setForm(nextForm);
    setOpenSections(editorOpenSections(nextForm, true, view));
    setAdvancedLimitsOpen(false);
    setRemotePoolOptions([]);
    setPoolLookupHasMore(false);
    setPoolLookupError(null);
    setLocalError(null);
  };

  const openEdit = (policy: TierModelPolicy) => {
    if (locked) return;
    const nextForm = modelPolicyToForm(policy, pricingProfileForCallable(policy.callable_key));
    setEditingPolicy(policy);
    setForm(nextForm);
    setOpenSections(editorOpenSections(nextForm, false, view));
    setAdvancedLimitsOpen(hasAdvancedLimitValues(nextForm));
    setRemotePoolOptions([]);
    setPoolLookupHasMore(false);
    setPoolLookupError(null);
    setLocalError(null);
  };

  const saveForm = async () => {
    if (locked) return;
    try {
      const existing = editingPolicy && typeof editingPolicy === 'object'
        ? policies.find((policy) => policy.tier_model_policy_id === editingPolicy.tier_model_policy_id) || editingPolicy
        : null;
      const payload = modelPolicyFormToPayload(form, existing);
      if (!payload.callable_key) {
        setLocalError('Model or callable key is required.');
        return;
      }
      setLocalError(null);
      if (editingPolicy === 'new') {
        await onCreate(payload);
      } else if (existing) {
        await onUpdate(existing, payload);
      }
      setEditingPolicy(null);
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
      await onDelete(policy);
    } catch (err: unknown) {
      setLocalError(errorMessage(err, 'Failed to remove model policy.'));
    }
  };

  const applyBulk = async () => {
    if (locked) return;
    try {
      const limits: { rpm_limit?: number; tpm_limit?: number } = {};
      if (bulk.rpm_limit.trim()) limits.rpm_limit = parsePositiveIntegerInput(bulk.rpm_limit, 'RPM');
      if (bulk.tpm_limit.trim()) limits.tpm_limit = parsePositiveIntegerInput(bulk.tpm_limit, 'TPM');
      setLocalError(null);
      await onBulkLimits(limits);
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
          <h3 className="text-sm font-semibold text-gray-900">{view === 'pricing' ? 'Pricing' : 'Models & limits'}</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            {view === 'pricing'
              ? 'Review and edit customer prices without duplicating model-policy data.'
              : 'Control model access, request limits, capacity binding, and precedence.'}
          </p>
        </div>
        {!readOnly ? (
          <button
            type="button"
            onClick={openNew}
            disabled={locked}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-xs font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            Add model policy
          </button>
        ) : null}
      </div>

      {conflict ? (
        <div className="mx-4 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900" role="alert">
          <p className="font-semibold">Another admin changed this draft.</p>
          <p className="mt-0.5 text-xs">{conflict} Your unsaved fields are still open; review the latest row before trying again.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {onReviewLatest ? (
              <button type="button" onClick={onReviewLatest} className="rounded-md bg-amber-900 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-amber-950">
                Review latest
              </button>
            ) : null}
            {onDiscardConflict ? (
              <button
                type="button"
                onClick={() => {
                  setEditingPolicy(null);
                  setForm(emptyModelPolicyForm());
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

      {error || localError ? (
        <div className="mx-4 mt-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
          {localError || error}
        </div>
      ) : null}

      <div className="flex flex-col gap-2 border-b border-gray-100 px-4 py-3 lg:flex-row lg:items-center">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">Search model policies</span>
          <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
          <input
            value={searchInput}
            onChange={(event) => onSearchInputChange(event.target.value)}
            placeholder="Search models or capacity pools"
            className="w-full rounded-lg border border-gray-300 py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
          />
        </label>
        <select
          aria-label="Filter policies by enabled state"
          value={enabledFilter}
          onChange={(event) => onEnabledFilterChange(event.target.value as 'all' | 'enabled' | 'disabled')}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
        >
          <option value="all">All states</option>
          <option value="enabled">Enabled</option>
          <option value="disabled">Disabled</option>
        </select>
        <select
          aria-label="Filter policies by access"
          value={accessFilter}
          onChange={(event) => onAccessFilterChange(event.target.value as 'all' | 'allow' | 'deny')}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
        >
          <option value="all">Allow and deny</option>
          <option value="allow">Allow</option>
          <option value="deny">Deny</option>
        </select>
      </div>

      {view === 'limits' && !readOnly && pagination.total > 0 ? (
        <div className="m-4 rounded-xl border border-gray-100 bg-gray-50 p-3">
          <div className="mb-2">
            <p className="text-xs font-semibold uppercase text-gray-500">Bulk limits</p>
            <p className="mt-0.5 text-xs text-gray-500">Applies to all {pagination.total} policies matching the current search and filters, including other pages.</p>
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
            <BulkInput label="RPM" value={bulk.rpm_limit} disabled={locked} onChange={(value) => setBulk({ ...bulk, rpm_limit: value })} />
            <BulkInput label="TPM" value={bulk.tpm_limit} disabled={locked} onChange={(value) => setBulk({ ...bulk, tpm_limit: value })} />
            <button
              type="button"
              onClick={applyBulk}
              disabled={locked || !Object.values(bulk).some((value) => value.trim())}
              className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Apply to all filtered
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
              {view === 'limits' ? (
                <>
                  <th className="px-4 py-2 text-left">RPM</th>
                  <th className="px-4 py-2 text-left">TPM</th>
                  <th className="px-4 py-2 text-left">Advanced</th>
                  <th className="px-4 py-2 text-left">Pool</th>
                  <th className="px-4 py-2 text-left">Priority</th>
                </>
              ) : (
                <>
                  <th className="px-4 py-2 text-left">Profile</th>
                  <th className="px-4 py-2 text-left">Configured pricing</th>
                  <th className="px-4 py-2 text-left">State</th>
                </>
              )}
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sortedPolicies.length === 0 ? (
              <tr>
                <td colSpan={view === 'limits' ? 8 : 6} className="px-4 py-8 text-center text-sm text-gray-400">No model policies match this view.</td>
              </tr>
            ) : sortedPolicies.map((policy) => (
              <tr key={`${policy.callable_key}:${policy.priority}`}>
                <td className="px-4 py-3 font-mono text-xs text-gray-700">{policy.callable_key}</td>
                <td className="px-4 py-3 text-xs font-semibold">
                  <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 ${!policy.enabled ? 'bg-gray-100 text-gray-600' : policy.access_mode === 'allow' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
                    {!policy.enabled ? 'Disabled' : policy.access_mode === 'allow' ? 'Allow' : 'Deny'}
                  </span>
                </td>
                {view === 'limits' ? (
                  <>
                    <td className="px-4 py-3 text-xs text-gray-600">{formatLimit(policy.rpm_limit)}</td>
                    <td className="px-4 py-3 text-xs text-gray-600">{formatLimit(policy.tpm_limit)}</td>
                    <td className="px-4 py-3 text-xs text-gray-500">{policyAdvancedLimitCount(policy) || '—'}</td>
                    <td className="px-4 py-3 text-xs text-gray-500">{policy.capacity_pool_key || '—'}</td>
                    <td className="px-4 py-3 text-xs text-gray-600">{policy.priority}</td>
                  </>
                ) : (
                  <>
                    <td className="px-4 py-3 text-xs text-gray-600">{pricingProfileLabel(pricingProfileForCallable(policy.callable_key) || modelPolicyToForm(policy).pricing_profile)}</td>
                    <td className="max-w-md px-4 py-3 text-xs text-gray-600">{summarizePricing(policy.pricing, pricingProfileForCallable(policy.callable_key))}</td>
                    <td className="px-4 py-3 text-xs text-gray-500">{pricingConfigurationState(policy.pricing)}</td>
                  </>
                )}
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-1">
                    <button
                      type="button"
                      onClick={() => openEdit(policy)}
                      disabled={locked}
                      className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40"
                      aria-label={`Edit policy for ${policy.callable_key}`}
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removePolicy(policy)}
                      disabled={locked}
                      className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      aria-label={`Remove policy for ${policy.callable_key}`}
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
        itemLabel="model policies"
      />

      {editingPolicy !== null ? (
        <TierEditorDrawer
          title={editingPolicy === 'new' ? 'Add model policy' : `Edit ${editingPolicy.callable_key}`}
          description="Configure access, request limits, customer pricing, and shared capacity in one place."
          saving={saving}
          onClose={() => setEditingPolicy(null)}
        >
          {conflict ? (
            <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900" role="alert">
              <p className="font-semibold">Another admin changed this draft.</p>
              <p className="mt-0.5 text-xs">{conflict} Your unsaved fields remain in this editor.</p>
              {editingPolicy !== 'new' ? (
                latestEditingPolicy ? (
                  <ConflictDifferences differences={conflictDifferences} />
                ) : (
                  <p className="mt-2 rounded-md bg-white/70 px-2 py-1.5 text-xs">The policy is not present on this server page. It may have been removed or moved out of the current filters.</p>
                )
              ) : null}
              <div className="mt-2 flex flex-wrap gap-2">
                {onReviewLatest ? <button type="button" onClick={onReviewLatest} className="rounded-md bg-amber-900 px-2.5 py-1.5 text-xs font-semibold text-white">Review latest</button> : null}
                {onDiscardConflict ? (
                  <button
                    type="button"
                    onClick={() => {
                      setEditingPolicy(null);
                      setForm(emptyModelPolicyForm());
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
                    disabled={locked || editingPolicy !== 'new'}
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
                      className="h-4 w-4 rounded border-gray-300 text-brand-primary-ink focus:ring-brand-primary"
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
                key={`${editingPolicy === 'new' ? 'new' : editingPolicy?.tier_model_policy_id}-advanced-limits`}
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
              {modeInferenceUnavailable ? (
                <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                  {modeInferenceUnavailable}
                </p>
              ) : null}
              <TierPricingFields
                key={`${editingPolicy === 'new' ? 'new' : editingPolicy?.tier_model_policy_id}-pricing`}
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
                    {poolLookupLoading
                      ? 'Searching compatible pools…'
                      : poolLookupError
                        ? `${poolLookupError} You can still enter a known pool key.`
                        : form.callable_key && matchingPoolOptions.length > 0
                          ? `${matchingPoolOptions.length} matching pool${matchingPoolOptions.length === 1 ? '' : 's'}${poolLookupHasMore ? '; type more of the pool key to narrow the results' : ''}.`
                      : form.callable_key
                        ? 'No matching compatible pool was found. Leave blank or create one in Capacity Pools.'
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
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              {editingPolicy === 'new' ? 'Add policy' : 'Save policy'}
            </button>
          </div>
        </TierEditorDrawer>
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

function editorOpenSections(
  form: TierModelPolicyForm,
  isNew: boolean,
  view: 'limits' | 'pricing',
): PolicyEditorSections {
  const initial = initialOpenSections(form, isNew);
  return view === 'pricing'
    ? { ...initial, limits: false, pricing: true }
    : initial;
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

function policyAdvancedLimitCount(policy: TierModelPolicy): number {
  return [
    policy.rph_limit,
    policy.rpd_limit,
    policy.tpd_limit,
    policy.max_parallel_requests,
    policy.batch_rpm_limit,
    policy.batch_tpm_limit,
  ].filter((value) => value != null).length;
}

function pricingConfigurationState(pricing?: Record<string, number> | null): string {
  const values = Object.values(pricing || {}).filter((value) => typeof value === 'number');
  if (values.length === 0) return 'Not configured';
  if (values.every((value) => value === 0)) return 'Explicitly free';
  return `${values.length} configured`;
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

type FormDifference = { field: string; server: string; local: string };

function formDifferences(server: TierModelPolicyForm, local: TierModelPolicyForm): FormDifference[] {
  return (Object.keys(local) as Array<keyof TierModelPolicyForm>).flatMap((field) => {
    if (server[field] === local[field]) return [];
    return [{
      field: humanizeField(field),
      server: displayConflictValue(server[field]),
      local: displayConflictValue(local[field]),
    }];
  });
}

function ConflictDifferences({ differences }: { differences: FormDifference[] }) {
  if (differences.length === 0) {
    return <p className="mt-2 text-xs">The visible row now matches your open fields; refresh may have affected another policy.</p>;
  }
  const visible = differences.slice(0, 8);
  return (
    <div className="mt-2 overflow-hidden rounded-md border border-amber-200 bg-white/80">
      <div className="grid grid-cols-[minmax(100px,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 border-b border-amber-100 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-amber-800">
        <span>Field</span><span>Server now</span><span>Your value</span>
      </div>
      {visible.map((difference) => (
        <div key={difference.field} className="grid grid-cols-[minmax(100px,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 border-b border-amber-100 px-2 py-1.5 text-[11px] last:border-b-0">
          <span className="font-semibold">{difference.field}</span>
          <span className="truncate" title={difference.server}>{difference.server}</span>
          <span className="truncate" title={difference.local}>{difference.local}</span>
        </div>
      ))}
      {differences.length > visible.length ? (
        <p className="border-t border-amber-100 px-2 py-1 text-[11px]">And {differences.length - visible.length} more changed fields.</p>
      ) : null}
    </div>
  );
}

function humanizeField(field: string): string {
  return field.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

function displayConflictValue(value: string | boolean): string {
  if (typeof value === 'boolean') return value ? 'Enabled' : 'Disabled';
  return value.trim() || 'Blank';
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
        className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500"
      />
    </label>
  );
}
