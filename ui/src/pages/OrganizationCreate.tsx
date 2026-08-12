import { useEffect, useMemo, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  ArrowLeft,
  Building2,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  DollarSign,
  ExternalLink,
  Gauge,
  Info,
  RefreshCw,
  Shield,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import ToggleSwitch from '../components/ToggleSwitch';
import {
  callableTargets,
  organizations,
  settings,
  tiers,
  type OrganizationCreatePayload,
  type Tier,
  type TierVersionDetail,
} from '../lib/api';
import {
  assetAccessLoadErrorMessage,
  buildCatalogAccessGroups,
  buildCatalogAssetTargets,
} from '../lib/assetAccess';
import { useAuth } from '../lib/auth';
import { dateTimeLocalUtcInputToIso, defaultMonthlyResetUtcInputValue } from '../lib/format';
import { useApi } from '../lib/hooks';
import {
  normalizeTierPolicyMode,
  organizationUsesTier,
} from '../lib/organizationPolicy';

type HardCapField = 'rpm_limit' | 'tpm_limit' | 'rph_limit' | 'rpd_limit' | 'tpd_limit';

const HARD_CAPS: Array<{
  field: HardCapField;
  label: string;
  unit: string;
  placeholder: string;
  help: string;
}> = [
  {
    field: 'rpm_limit',
    label: 'Requests per minute',
    unit: 'RPM',
    placeholder: '1,000',
    help: 'A global minute-level request ceiling across every model, team, and key in this organization. Example: 1,000 blocks request 1,001 in the same minute even if its tier still has capacity.',
  },
  {
    field: 'tpm_limit',
    label: 'Tokens per minute',
    unit: 'TPM',
    placeholder: '500,000',
    help: 'A global minute-level token ceiling across input and output tokens. It is evaluated in addition to each model limit in the service tier.',
  },
  {
    field: 'rph_limit',
    label: 'Requests per hour',
    unit: 'RPH',
    placeholder: '25,000',
    help: 'A rolling organization-wide hourly request ceiling. Use this to smooth sustained traffic that may fit inside the minute limit.',
  },
  {
    field: 'rpd_limit',
    label: 'Requests per day',
    unit: 'RPD',
    placeholder: '250,000',
    help: 'An organization-wide daily request ceiling. This protects against unexpectedly high aggregate usage across teams and models.',
  },
  {
    field: 'tpd_limit',
    label: 'Tokens per day',
    unit: 'TPD',
    placeholder: '10,000,000',
    help: 'An organization-wide daily ceiling across input and output tokens. Requests must pass both this cap and the selected tier limits.',
  },
];

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  if (error && typeof error === 'object' && 'message' in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
}

function optionalNumber(value: string): number | undefined {
  return value.trim() ? Number(value) : undefined;
}

function HelpTip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={`About ${label}`}
        className="rounded-full text-gray-400 transition-colors hover:text-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <Info className="h-3.5 w-3.5" />
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-0 top-full z-50 mt-2 hidden w-72 rounded-lg border border-gray-200 bg-gray-900 px-3 py-2 text-xs font-normal leading-relaxed text-white shadow-xl group-hover:block group-focus-within:block"
      >
        {children}
      </span>
    </span>
  );
}

function StepMarker({ number, label, active, complete }: {
  number: number;
  label: string;
  active: boolean;
  complete: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ${
        complete
          ? 'bg-emerald-100 text-emerald-700'
          : active
            ? 'bg-blue-600 text-white'
            : 'bg-gray-100 text-gray-400'
      }`}>
        {complete ? <Check className="h-3.5 w-3.5" /> : number}
      </span>
      <span className={`text-xs font-medium ${active ? 'text-gray-900' : 'text-gray-500'}`}>{label}</span>
    </div>
  );
}

function BackgroundList({ items }: { items: Array<Record<string, unknown>> }) {
  return (
    <div className="flex-1 overflow-hidden bg-gray-50 pointer-events-none select-none">
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <h1 className="flex items-center gap-2 text-xl font-bold text-gray-900">
            <Building2 className="h-5 w-5 text-blue-600" /> Organizations
          </h1>
          <span className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white opacity-50">
            + Create Organization
          </span>
        </div>
      </div>
      <div className="px-6 py-4">
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Organization</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Service policy</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Budget</th>
              </tr>
            </thead>
            <tbody>
              {(items.length ? items : Array.from({ length: 4 }, () => ({} as Record<string, unknown>))).map((row, index) => {
                const organizationId = typeof row.organization_id === 'string' ? row.organization_id : '';
                const name = typeof row.organization_name === 'string' ? row.organization_name : organizationId || '—';
                const servicePolicy = row.service_policy as { source?: string; primary_tier?: { tier_name?: string } } | undefined;
                return (
                  <tr key={organizationId || index} className="border-b border-gray-100 last:border-0">
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-100 text-xs font-bold text-blue-400">
                          {name[0]?.toUpperCase() || '?'}
                        </div>
                        <div>
                          <p className="font-semibold text-gray-400">{name}</p>
                          {organizationId && <p className="font-mono text-[10px] text-gray-300">{organizationId}</p>}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-xs text-gray-300">
                      {servicePolicy?.source === 'tier' ? servicePolicy.primary_tier?.tier_name || 'Tier managed' : 'Legacy'}
                    </td>
                    <td className="px-4 py-3.5 text-xs text-gray-300">—</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function TierSummary({ tier, detail, loading, policyMode }: {
  tier: Tier;
  detail: TierVersionDetail | null;
  loading: boolean;
  policyMode: 'disabled' | 'shadow' | 'enforce';
}) {
  const policies = (detail?.model_policies || []).filter((policy) => policy.enabled);
  const allowed = policies.filter((policy) => policy.access_mode === 'allow');
  const denied = policies.filter((policy) => policy.access_mode === 'deny');
  const limited = policies.filter((policy) => (
    policy.rpm_limit != null
    || policy.tpm_limit != null
    || policy.rph_limit != null
    || policy.rpd_limit != null
    || policy.tpd_limit != null
  ));

  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50/60 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-gray-900">{tier.name}</p>
            <code className="rounded bg-white px-1.5 py-0.5 text-[10px] text-gray-500">{tier.tier_key}</code>
            {detail?.tier_version && (
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                Active v{detail.tier_version.version_number}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs leading-relaxed text-gray-600">
            {tier.description || 'No tier description has been added yet.'}
          </p>
        </div>
        <Shield className="h-5 w-5 shrink-0 text-blue-600" />
      </div>

      {loading ? (
        <div className="mt-4 h-14 animate-pulse rounded-lg bg-blue-100" />
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ['Allowed', allowed.length],
              ['Denied', denied.length],
              ['With limits', limited.length],
              ['Capacity pools', detail?.capacity_pools.length || 0],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-lg border border-blue-100 bg-white px-3 py-2">
                <p className="text-[10px] uppercase tracking-wide text-gray-400">{label}</p>
                <p className="mt-0.5 text-sm font-semibold text-gray-800">{value}</p>
              </div>
            ))}
          </div>
          {allowed.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {allowed.slice(0, 5).map((policy) => (
                <span key={policy.callable_key} className="rounded-full border border-blue-200 bg-white px-2 py-0.5 text-[10px] text-blue-700">
                  {policy.callable_key}
                </span>
              ))}
              {allowed.length > 5 && <span className="text-[10px] text-gray-500">+{allowed.length - 5} more</span>}
            </div>
          )}
        </>
      )}

      <div className="mt-3 flex items-start gap-2 rounded-lg border border-blue-100 bg-white/80 px-3 py-2 text-xs text-blue-800">
        <RefreshCw className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {policyMode === 'shadow'
          ? "The staged assignment follows the tier's active version for observation. Its legacy access mirror captures this version so later tier changes appear as shadow mismatches before enforcement."
          : "This organization follows the tier's active version automatically. Publishing a new tier version updates its policy without editing the organization."}
      </div>
    </div>
  );
}

export default function OrganizationCreate() {
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';

  const { data: listResult } = useApi(
    () => isPlatformAdmin
      ? organizations.list({ limit: 8, offset: 0 })
      : Promise.resolve({ data: [], pagination: { total: 0, limit: 8, offset: 0, has_more: false } }),
    [isPlatformAdmin],
  );
  const { data: appSettings, error: settingsError, loading: settingsLoading } = useApi(
    () => isPlatformAdmin ? settings.get() : Promise.resolve({}),
    [isPlatformAdmin],
  );
  const {
    data: tierItems,
    error: tiersError,
    loading: tiersLoading,
    refetch: refetchTiers,
  } = useApi(
    () => isPlatformAdmin ? tiers.listAll({ enabled: true }) : Promise.resolve([]),
    [isPlatformAdmin],
  );

  const tierPolicyMode = normalizeTierPolicyMode(appSettings?.general_settings?.tier_policy_mode);
  const availableTiers = useMemo(
    () => (tierItems || []).filter((tier) => tier.enabled && tier.active_version_id),
    [tierItems],
  );
  const [legacyMigration, setLegacyMigration] = useState(false);
  const usesTier = organizationUsesTier(tierPolicyMode, legacyMigration);

  const [step, setStep] = useState<1 | 2>(1);
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState(false);
  const [selectedTierId, setSelectedTierId] = useState('');
  const selectedTier = availableTiers.find((tier) => tier.tier_id === selectedTierId) || null;

  useEffect(() => {
    if (!selectedTierId && availableTiers.length > 0) {
      setSelectedTierId(availableTiers[0].tier_id);
    } else if (selectedTierId && !availableTiers.some((tier) => tier.tier_id === selectedTierId)) {
      setSelectedTierId(availableTiers[0]?.tier_id || '');
    }
  }, [availableTiers, selectedTierId]);

  const {
    data: selectedTierDetail,
    error: selectedTierError,
    loading: selectedTierLoading,
  } = useApi(
    () => usesTier && selectedTier?.active_version_id
      ? tiers.getVersion(selectedTier.tier_id, selectedTier.active_version_id)
      : Promise.resolve(null),
    [usesTier, selectedTier?.tier_id, selectedTier?.active_version_id],
  );

  const [budgetEnabled, setBudgetEnabled] = useState(false);
  const [budgetValue, setBudgetValue] = useState('');
  const [softBudgetValue, setSoftBudgetValue] = useState('');
  const [monthlyResetEnabled, setMonthlyResetEnabled] = useState(false);
  const [budgetResetAt, setBudgetResetAt] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [hardCaps, setHardCaps] = useState<Record<HardCapField, string>>({
    rpm_limit: '',
    tpm_limit: '',
    rph_limit: '',
    rpd_limit: '',
    tpd_limit: '',
  });
  const [auditStorage, setAuditStorage] = useState(false);

  const [selectAll, setSelectAll] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [selectedAccessGroupKeys, setSelectedAccessGroupKeys] = useState<string[]>([]);
  const [assetSearchInput, setAssetSearchInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const [assetPageOffset, setAssetPageOffset] = useState(0);
  const [accessGroupPageOffset, setAccessGroupPageOffset] = useState(0);
  const [assetTargetType, setAssetTargetType] = useState<'all' | 'model' | 'route_group'>('all');
  const assetPageSize = 50;
  const accessGroupPageSize = 50;

  useEffect(() => {
    const timeout = setTimeout(() => {
      setAssetSearch(assetSearchInput);
      setAssetPageOffset(0);
      setAccessGroupPageOffset(0);
    }, 250);
    return () => clearTimeout(timeout);
  }, [assetSearchInput]);

  const shouldLoadLegacyAssets = isPlatformAdmin && step === 2 && !usesTier && !selectAll;
  const {
    data: callableTargetPage,
    error: callableTargetPageError,
    loading: callableTargetPageLoading,
  } = useApi(
    () => shouldLoadLegacyAssets
      ? callableTargets.list({
          search: assetSearch || undefined,
          target_type: assetTargetType === 'all' ? undefined : assetTargetType,
          limit: assetPageSize,
          offset: assetPageOffset,
        })
      : Promise.resolve({ data: [], pagination: { total: 0, limit: assetPageSize, offset: 0, has_more: false } }),
    [shouldLoadLegacyAssets, assetSearch, assetTargetType, assetPageOffset],
  );
  const {
    data: callableTargetAccessGroups,
    error: accessGroupError,
    loading: accessGroupLoading,
  } = useApi(
    () => shouldLoadLegacyAssets
      ? callableTargets.listAccessGroups({
          search: assetSearch || undefined,
          include_members: false,
          limit: accessGroupPageSize,
          offset: accessGroupPageOffset,
        })
      : Promise.resolve({ data: [], pagination: { total: 0, limit: accessGroupPageSize, offset: 0, has_more: false } }),
    [shouldLoadLegacyAssets, assetSearch, accessGroupPageOffset],
  );

  const assetTargets = buildCatalogAssetTargets(callableTargetPage?.data || [], selectedKeys);
  const assetAccessGroups = buildCatalogAccessGroups(callableTargetAccessGroups?.data || [], selectedAccessGroupKeys);
  const assetAccessLoading = shouldLoadLegacyAssets && (callableTargetPageLoading || accessGroupLoading);
  const assetAccessError = shouldLoadLegacyAssets
    ? assetAccessLoadErrorMessage(callableTargetPageError || accessGroupError)
    : null;

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleBudgetToggle = (checked: boolean) => {
    setBudgetEnabled(checked);
    if (checked) {
      setBudgetValue((current) => current || '1000');
      setSoftBudgetValue((current) => current || '800');
    }
  };

  const handleMonthlyResetToggle = (checked: boolean) => {
    setMonthlyResetEnabled(checked);
    if (checked && !budgetResetAt) setBudgetResetAt(defaultMonthlyResetUtcInputValue());
  };

  const canContinue = Boolean(
    name.trim()
    && !settingsLoading
    && !settingsError
    && (!usesTier || selectedTier?.active_version_id),
  );
  const canCreate = canContinue && !assetAccessLoading && !assetAccessError;

  const continueToGuardrails = () => {
    if (!name.trim()) {
      setNameError(true);
      return;
    }
    if (!canContinue) return;
    setError(null);
    setStep(2);
  };

  const handleCreate = async () => {
    if (!canCreate) return;
    setError(null);
    setSaving(true);
    try {
      const maxBudget = budgetEnabled ? optionalNumber(budgetValue) : undefined;
      const softBudget = budgetEnabled ? optionalNumber(softBudgetValue) : undefined;
      if (maxBudget !== undefined && softBudget !== undefined && softBudget > maxBudget) {
        setError('The soft budget alert must be less than or equal to the hard budget.');
        return;
      }
      const resetAtIso = budgetEnabled && monthlyResetEnabled
        ? dateTimeLocalUtcInputToIso(budgetResetAt)
        : null;
      if (budgetEnabled && monthlyResetEnabled && !resetAtIso) {
        setError('Choose a valid next reset date.');
        return;
      }

      const payload: OrganizationCreatePayload = {
        organization_name: name.trim(),
        audit_content_storage_enabled: auditStorage,
      };
      if (usesTier && selectedTier) {
        payload.primary_tier = { tier_id: selectedTier.tier_id, tier_version_id: null };
      } else if (!selectAll) {
        payload.callable_target_bindings = selectedKeys.map((callable_key) => ({ callable_key }));
      }
      if (tierPolicyMode === 'shadow' && legacyMigration) {
        payload.legacy_policy_exception = true;
      }
      if (maxBudget !== undefined) payload.max_budget = maxBudget;
      if (softBudget !== undefined) payload.soft_budget = softBudget;
      if (budgetEnabled && monthlyResetEnabled && resetAtIso) {
        payload.budget_duration = '1mo';
        payload.budget_reset_at = resetAtIso;
      }
      for (const { field } of HARD_CAPS) {
        const value = optionalNumber(hardCaps[field]);
        if (value !== undefined) payload[field] = value;
      }

      const created = await organizations.create(payload);
      let pageWarning: string | null = null;
      if (!usesTier && (selectAll || selectedAccessGroupKeys.length > 0)) {
        try {
          await organizations.updateAssetAccess(created.organization_id, {
            selected_callable_keys: selectAll ? [] : selectedKeys,
            selected_access_group_keys: selectAll ? [] : selectedAccessGroupKeys,
            select_all_selectable: selectAll,
          });
        } catch (assetError: unknown) {
          pageWarning = getErrorMessage(
            assetError,
            'Organization created, but legacy asset access could not be applied.',
          );
        }
      }

      navigate(`/organizations/${created.organization_id}`, {
        state: pageWarning ? { pageWarning } : undefined,
      });
    } catch (createError: unknown) {
      setError(getErrorMessage(createError, 'Failed to create organization'));
    } finally {
      setSaving(false);
    }
  };

  if (!isPlatformAdmin) return <Navigate to="/organizations" replace />;

  const configuredHardCaps = HARD_CAPS.filter(({ field }) => hardCaps[field].trim());
  const policyModeCopy = tierPolicyMode === 'enforce'
    ? 'A primary service tier is required. The tier controls model access, per-model limits, pricing, and capacity.'
    : tierPolicyMode === 'shadow'
      ? 'Tier policy is in shadow mode. New organizations stage a tier while legacy access remains authoritative until enforcement.'
      : 'Tier policy is disabled. This organization will use legacy asset access until tiers are enabled.';

  return (
    <div className="relative flex h-screen overflow-hidden">
      <div className="flex flex-1 flex-col opacity-30">
        <BackgroundList items={(listResult?.data || []) as Array<Record<string, unknown>>} />
      </div>
      <button
        type="button"
        aria-label="Cancel organization creation"
        className="absolute inset-0 bg-gray-900/20"
        onClick={() => navigate('/organizations')}
      />

      <div className="absolute right-0 top-0 z-10 flex h-full w-full max-w-[680px] flex-col bg-white shadow-2xl">
        <div className="shrink-0 border-b border-gray-200 px-7 py-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="mb-1 flex items-center gap-1.5 text-xs text-gray-400">
                <Building2 className="h-3.5 w-3.5" />
                <ChevronRight className="h-3 w-3" />
                <span className="font-medium text-gray-600">New organization</span>
              </div>
              <h2 className="text-lg font-bold text-gray-900">Create organization</h2>
              <p className="mt-0.5 text-xs text-gray-500">Choose the service policy first, then add optional organization guardrails.</p>
            </div>
            <button
              type="button"
              onClick={() => navigate('/organizations')}
              className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-4 flex items-center gap-4">
            <StepMarker number={1} label="Organization & tier" active={step === 1} complete={step === 2} />
            <div className="h-px flex-1 bg-gray-200" />
            <StepMarker number={2} label="Guardrails & review" active={step === 2} complete={false} />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-7 py-6">
          {(error || assetAccessError) && (
            <div className="mb-5 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error || assetAccessError}
            </div>
          )}

          {step === 1 ? (
            <div className="space-y-6">
              <section>
                <label className="mb-1.5 block text-sm font-medium text-gray-700" htmlFor="organization-name">
                  Organization name <span className="text-red-500">*</span>
                </label>
                <input
                  id="organization-name"
                  value={name}
                  onChange={(event) => { setName(event.target.value); setNameError(false); }}
                  onBlur={() => setNameError(!name.trim())}
                  placeholder="e.g. Acme Corp"
                  className={`w-full rounded-lg border px-3 py-2.5 text-sm focus:outline-none focus:ring-2 ${
                    nameError ? 'border-red-400 focus:ring-red-400' : 'border-gray-300 focus:ring-blue-500'
                  }`}
                />
                {nameError && <p className="mt-1 text-xs text-red-600">Organization name is required.</p>}
              </section>

              <section className="border-t border-gray-200 pt-6">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-1.5">
                      <h3 className="text-sm font-semibold text-gray-900">Service policy</h3>
                      <HelpTip label="service policy">
                        A tier is the reusable source for model access, per-model rate limits, pricing, and shared capacity. Organization hard caps are added separately on the next step.
                      </HelpTip>
                    </div>
                    <p className="mt-0.5 text-xs text-gray-500">{policyModeCopy}</p>
                  </div>
                  <span className={`rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${
                    tierPolicyMode === 'enforce'
                      ? 'bg-emerald-100 text-emerald-700'
                      : tierPolicyMode === 'shadow'
                        ? 'bg-amber-100 text-amber-700'
                        : 'bg-gray-100 text-gray-600'
                  }`}>
                    {settingsLoading ? 'Loading' : tierPolicyMode}
                  </span>
                </div>

                {Boolean(settingsError) && (
                  <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
                    Could not load the effective tier policy mode. Creation is disabled until settings can be verified.
                  </div>
                )}

                {tierPolicyMode === 'shadow' && (
                  <label className="mb-4 flex cursor-pointer items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
                    <input
                      type="checkbox"
                      checked={legacyMigration}
                      onChange={(event) => setLegacyMigration(event.target.checked)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-xs font-semibold text-amber-900">Create as legacy during migration</span>
                      <span className="mt-0.5 block text-xs text-amber-800">Use direct asset access instead of a tier. This should only be used for a deliberate migration exception.</span>
                    </span>
                  </label>
                )}

                {usesTier ? (
                  <div className="space-y-3">
                    <div className="flex items-end gap-2">
                      <div className="flex-1">
                        <label htmlFor="service-tier" className="mb-1.5 block text-xs font-medium text-gray-600">
                          Primary service tier <span className="text-red-500">*</span>
                        </label>
                        <select
                          id="service-tier"
                          value={selectedTierId}
                          onChange={(event) => setSelectedTierId(event.target.value)}
                          disabled={tiersLoading || availableTiers.length === 0}
                          className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50"
                        >
                          {availableTiers.length === 0 && <option value="">No active tiers available</option>}
                          {availableTiers.map((tier) => (
                            <option key={tier.tier_id} value={tier.tier_id}>{tier.name} — {tier.tier_key}</option>
                          ))}
                        </select>
                      </div>
                      <button
                        type="button"
                        onClick={refetchTiers}
                        disabled={tiersLoading}
                        className="rounded-lg border border-gray-300 p-2.5 text-gray-500 hover:bg-gray-50 disabled:opacity-50"
                        aria-label="Refresh tiers"
                      >
                        <RefreshCw className={`h-4 w-4 ${tiersLoading ? 'animate-spin' : ''}`} />
                      </button>
                    </div>

                    {Boolean(tiersError) && (
                      <p className="text-xs text-red-600">{getErrorMessage(tiersError, 'Could not load service tiers.')}</p>
                    )}
                    {!tiersLoading && availableTiers.length === 0 && (
                      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                        Create and publish at least one enabled tier before creating a tier-managed organization.
                      </div>
                    )}
                    {Boolean(selectedTierError) && (
                      <p className="text-xs text-red-600">{getErrorMessage(selectedTierError, 'Could not load the selected tier version.')}</p>
                    )}
                    {selectedTier && (
                      <TierSummary
                        tier={selectedTier}
                        detail={selectedTierDetail}
                        loading={selectedTierLoading}
                        policyMode={tierPolicyMode}
                      />
                    )}
                    {tierPolicyMode === 'shadow' && selectedTier && (
                      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-800">
                        <span className="font-semibold">Shadow compatibility:</span> the tier's allowed models are mirrored into legacy Asset Access in the same transaction. Requests work now while the tier decision is observed; the tier becomes authoritative only after enforcement is enabled.
                      </div>
                    )}
                    <a
                      href="/tiers"
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700"
                    >
                      Create or customize a tier <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                ) : (
                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <div className="flex items-start gap-3">
                      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                      <div>
                        <p className="text-sm font-semibold text-gray-800">Legacy service policy</p>
                        <p className="mt-1 text-xs leading-relaxed text-gray-600">
                          Model access will be configured directly on this organization. When tier enforcement is enabled, migrate it by assigning a primary tier.
                        </p>
                      </div>
                    </div>
                  </div>
                )}
              </section>
            </div>
          ) : (
            <div className="space-y-5">
              <section className="rounded-xl border border-gray-200 bg-white p-4">
                <div className="flex items-center justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <DollarSign className="mt-0.5 h-4 w-4 text-emerald-600" />
                    <div>
                      <p className="text-sm font-semibold text-gray-900">Organization budget</p>
                      <p className="mt-0.5 text-xs text-gray-500">Optional spend guardrail, independent from the service tier.</p>
                    </div>
                  </div>
                  <ToggleSwitch checked={budgetEnabled} onCheckedChange={handleBudgetToggle} aria-label="Toggle organization budget" />
                </div>
                {budgetEnabled && (
                  <div className="mt-4 grid grid-cols-1 gap-3 border-t border-gray-100 pt-4 sm:grid-cols-2">
                    <div>
                      <label className="mb-1 block text-xs font-medium text-gray-600" htmlFor="max-budget">Hard budget ($)</label>
                      <input
                        id="max-budget"
                        type="number"
                        min="0"
                        step="0.01"
                        value={budgetValue}
                        onChange={(event) => setBudgetValue(event.target.value)}
                        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-medium text-gray-600" htmlFor="soft-budget">Soft alert ($)</label>
                      <input
                        id="soft-budget"
                        type="number"
                        min="0"
                        step="0.01"
                        value={softBudgetValue}
                        onChange={(event) => setSoftBudgetValue(event.target.value)}
                        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div className="sm:col-span-2 rounded-lg border border-gray-200 bg-gray-50 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 text-sm font-medium text-gray-800">
                          <CalendarDays className="h-4 w-4 text-blue-600" /> Monthly reset
                        </div>
                        <ToggleSwitch checked={monthlyResetEnabled} onCheckedChange={handleMonthlyResetToggle} aria-label="Toggle monthly budget reset" />
                      </div>
                      {monthlyResetEnabled && (
                        <div className="mt-3">
                          <label className="mb-1 block text-xs font-medium text-gray-600" htmlFor="budget-reset">Next reset (UTC)</label>
                          <input
                            id="budget-reset"
                            type="datetime-local"
                            value={budgetResetAt}
                            onChange={(event) => setBudgetResetAt(event.target.value)}
                            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          />
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </section>

              <section className="overflow-visible rounded-xl border border-gray-200 bg-white">
                <button
                  type="button"
                  onClick={() => setAdvancedOpen((open) => !open)}
                  className="flex w-full items-start justify-between gap-4 p-4 text-left"
                >
                  <div className="flex items-start gap-3">
                    <SlidersHorizontal className="mt-0.5 h-4 w-4 text-purple-600" />
                    <div>
                      <div className="flex items-center gap-1.5">
                        <p className="text-sm font-semibold text-gray-900">Advanced organization-wide hard caps</p>
                        {configuredHardCaps.length > 0 && (
                          <span className="rounded-full bg-purple-100 px-2 py-0.5 text-[10px] font-semibold text-purple-700">
                            {configuredHardCaps.length} set
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs leading-relaxed text-gray-500">
                        Optional global safety ceilings. These do not replace tier limits; every request must pass both.
                      </p>
                    </div>
                  </div>
                  <ChevronDown className={`mt-0.5 h-4 w-4 shrink-0 text-gray-400 transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
                </button>
                {advancedOpen && (
                  <div className="border-t border-gray-100 px-4 pb-4 pt-4">
                    <div className="mb-4 rounded-lg border border-purple-100 bg-purple-50 px-3 py-2 text-xs leading-relaxed text-purple-800">
                      Leave these blank for no organization-wide ceiling. Configure model-specific limits, pricing, and capacity on the tier instead.
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {HARD_CAPS.map((cap) => (
                        <div key={cap.field}>
                          <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-gray-600" htmlFor={cap.field}>
                            {cap.label}
                            <HelpTip label={cap.unit}>{cap.help}</HelpTip>
                          </label>
                          <div className="relative">
                            <input
                              id={cap.field}
                              type="number"
                              min="1"
                              step="1"
                              value={hardCaps[cap.field]}
                              onChange={(event) => setHardCaps((current) => ({ ...current, [cap.field]: event.target.value }))}
                              placeholder={cap.placeholder}
                              className="w-full rounded-lg border border-gray-300 px-3 py-2 pr-14 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            />
                            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] font-semibold text-gray-400">{cap.unit}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </section>

              <section className="rounded-xl border border-gray-200 bg-white p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <Shield className="mt-0.5 h-4 w-4 text-blue-600" />
                    <div>
                      <p className="text-sm font-semibold text-gray-900">Audit content storage</p>
                      <p className="mt-0.5 text-xs leading-relaxed text-gray-500">Store request and response payloads for compliance review. Enable only when your retention policy permits it.</p>
                    </div>
                  </div>
                  <ToggleSwitch checked={auditStorage} onCheckedChange={setAuditStorage} aria-label="Toggle audit content storage" />
                </div>
              </section>

              {!usesTier && (
                <section className="rounded-xl border border-amber-200 bg-amber-50/40 p-4">
                  <div className="mb-3 flex items-center gap-2">
                    <Gauge className="h-4 w-4 text-amber-700" />
                    <div>
                      <p className="text-sm font-semibold text-gray-900">Legacy asset access</p>
                      <p className="text-xs text-gray-500">Used only because this organization is not tier-managed.</p>
                    </div>
                  </div>
                  <div className="mb-3 grid grid-cols-2 gap-2">
                    <label className={`cursor-pointer rounded-lg border p-3 text-xs ${selectAll ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                      <input
                        type="radio"
                        name="legacy-asset-strategy"
                        checked={selectAll}
                        onChange={() => { setSelectAll(true); setSelectedKeys([]); setSelectedAccessGroupKeys([]); }}
                        className="mr-2"
                      />
                      Allow all, including future assets
                    </label>
                    <label className={`cursor-pointer rounded-lg border p-3 text-xs ${!selectAll ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                      <input
                        type="radio"
                        name="legacy-asset-strategy"
                        checked={!selectAll}
                        onChange={() => setSelectAll(false)}
                        className="mr-2"
                      />
                      Choose a subset
                    </label>
                  </div>
                  {!selectAll && (
                    <AssetAccessEditor
                      title="Allowed assets"
                      description="Choose the models and route groups available to this legacy organization."
                      mode="grant"
                      targets={assetTargets}
                      selectedKeys={selectedKeys}
                      onSelectedKeysChange={setSelectedKeys}
                      accessGroups={assetAccessGroups}
                      selectedAccessGroupKeys={selectedAccessGroupKeys}
                      onSelectedAccessGroupKeysChange={setSelectedAccessGroupKeys}
                      targetsLoading={callableTargetPageLoading}
                      accessGroupsLoading={accessGroupLoading}
                      disabled={saving || Boolean(assetAccessError)}
                      searchValue={assetSearchInput}
                      onSearchValueChange={setAssetSearchInput}
                      targetTypeFilter={assetTargetType}
                      onTargetTypeFilterChange={(next) => { setAssetTargetType(next); setAssetPageOffset(0); }}
                      pagination={callableTargetPage?.pagination}
                      onPageChange={setAssetPageOffset}
                      accessGroupPagination={callableTargetAccessGroups?.pagination}
                      onAccessGroupPageChange={setAccessGroupPageOffset}
                      primaryActionLabel="Allow all assets"
                      onPrimaryAction={() => { setSelectAll(true); setSelectedKeys([]); setSelectedAccessGroupKeys([]); }}
                      secondaryActionLabel={selectedKeys.length || selectedAccessGroupKeys.length ? 'Clear selection' : undefined}
                      onSecondaryAction={selectedKeys.length || selectedAccessGroupKeys.length
                        ? () => { setSelectedKeys([]); setSelectedAccessGroupKeys([]); }
                        : undefined}
                    />
                  )}
                </section>
              )}

              <section className="rounded-xl border border-blue-200 bg-blue-50 p-4">
                <div className="mb-3 flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-blue-700" />
                  <h3 className="text-sm font-semibold text-blue-950">Review</h3>
                </div>
                <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-2 text-xs">
                  <dt className="text-blue-700">Organization</dt>
                  <dd className="font-medium text-blue-950">{name.trim()}</dd>
                  <dt className="text-blue-700">Service policy</dt>
                  <dd className="font-medium text-blue-950">
                    {usesTier ? `${selectedTier?.name || '—'} · follows active version` : 'Legacy direct access'}
                  </dd>
                  <dt className="text-blue-700">Budget</dt>
                  <dd className="font-medium text-blue-950">{budgetEnabled ? `$${budgetValue || '—'}` : 'No organization budget'}</dd>
                  <dt className="text-blue-700">Global hard caps</dt>
                  <dd className="font-medium text-blue-950">
                    {configuredHardCaps.length ? configuredHardCaps.map((cap) => cap.unit).join(', ') : 'None'}
                  </dd>
                  <dt className="text-blue-700">Audit payloads</dt>
                  <dd className="font-medium text-blue-950">{auditStorage ? 'Stored' : 'Not stored'}</dd>
                </dl>
              </section>
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-gray-200 bg-white px-7 py-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs">
              {!canContinue ? (
                <span className="flex items-center gap-1 text-amber-700">
                  <AlertCircle className="h-3.5 w-3.5" /> Complete the required policy fields
                </span>
              ) : step === 2 && canCreate ? (
                <span className="flex items-center gap-1 text-emerald-700">
                  <Check className="h-3.5 w-3.5" /> Ready to create
                </span>
              ) : (
                <span className="text-gray-500">Optional guardrails can be added next</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {step === 1 ? (
                <>
                  <button
                    type="button"
                    onClick={() => navigate('/organizations')}
                    className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={continueToGuardrails}
                    disabled={!canContinue}
                    className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Continue <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => setStep(1)}
                    disabled={saving}
                    className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" /> Back
                  </button>
                  <button
                    type="button"
                    onClick={handleCreate}
                    disabled={!canCreate || saving}
                    className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {saving ? 'Creating…' : 'Create organization'}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
