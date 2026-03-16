import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { callableTargets, organizations } from '../lib/api';
import { buildCatalogAssetTargets } from '../lib/assetAccess';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import {
  Building2, X, DollarSign, Gauge, TrendingUp, Info,
  ChevronRight, Check, AlertCircle, Shield,
} from 'lucide-react';

/* ─────────────── helpers ─────────────── */

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 mt-1">
      {children}
    </p>
  );
}

function FieldLabel({ label, required, hint }: { label: string; required?: boolean; hint?: string }) {
  return (
    <div className="flex items-center gap-1.5 mb-1.5">
      <label className="text-sm font-medium text-gray-700">{label}</label>
      {required && <span className="text-red-500 text-xs">*</span>}
      {hint && <Info className="w-3.5 h-3.5 text-gray-400" />}
    </div>
  );
}

function Toggle({ enabled, onToggle }: { enabled: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`relative rounded-full transition-colors shrink-0 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1`}
      style={{ height: 22, width: 40, background: enabled ? '#2563eb' : '#d1d5db' }}
      aria-checked={enabled}
      role="switch"
    >
      <span
        className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
          enabled ? 'translate-x-5' : 'translate-x-0.5'
        }`}
      />
    </button>
  );
}

/* ─────────────── faded background list ─────────────── */

function BgList({ items }: { items: any[] }) {
  return (
    <div className="flex-1 bg-gray-50 overflow-hidden pointer-events-none select-none">
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Building2 className="w-5 h-5 text-blue-600" /> Organizations
            {items.length > 0 && (
              <span className="inline-flex items-center justify-center min-w-[1.5rem] h-5 px-1.5 rounded-full bg-gray-100 text-xs font-semibold text-gray-600 ml-1">
                {items.length}
              </span>
            )}
          </h1>
          <span className="px-3 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg opacity-50">
            + Create Organization
          </span>
        </div>
      </div>
      <div className="px-6 py-4">
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Organization</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Budget Usage</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Members</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {(items.length > 0 ? items : Array.from({ length: 4 })).map((r: any, i: number) => {
                const name = r?.organization_name || r?.organization_id || '—';
                const pct = r?.max_budget ? Math.min(100, ((r.spend || 0) / r.max_budget) * 100) : null;
                return (
                  <tr key={r?.organization_id || i} className="border-b border-gray-100 last:border-b-0">
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-100 to-blue-200 flex items-center justify-center shrink-0">
                          <span className="text-xs font-bold text-blue-400">{name[0]?.toUpperCase() ?? '?'}</span>
                        </div>
                        <div>
                          <span className="font-semibold text-gray-400 text-sm">{name}</span>
                          {r?.organization_id && (
                            <p className="text-[10px] text-gray-300 font-mono">{r.organization_id}</p>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      {pct !== null ? (
                        <div className="w-28 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div className="h-full bg-gray-300 rounded-full" style={{ width: `${pct}%` }} />
                        </div>
                      ) : (
                        <span className="text-xs text-gray-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3.5 text-sm text-gray-300">—</td>
                    <td className="px-4 py-3.5">
                      <span className="text-xs text-gray-300">View</span>
                    </td>
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

/* ─────────────── page ─────────────── */

export default function OrganizationCreate() {
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';

  /* live list for background */
  const { data: listResult } = useApi(
    () => organizations.list({ limit: 8, offset: 0 }),
    [],
  );
  const bgItems: any[] = listResult?.data || [];

  /* form state */
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState(false);
  const [budgetEnabled, setBudgetEnabled] = useState(false);
  const [rpmEnabled, setRpmEnabled] = useState(false);
  const [tpmEnabled, setTpmEnabled] = useState(false);
  const [budgetValue, setBudgetValue] = useState('');
  const [rpmValue, setRpmValue] = useState('');
  const [tpmValue, setTpmValue] = useState('');
  const [auditStorage, setAuditStorage] = useState(false);
  const [selectAll, setSelectAll] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [assetSearchInput, setAssetSearchInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const [assetPageOffset, setAssetPageOffset] = useState(0);
  const [assetTargetType, setAssetTargetType] = useState<'all' | 'model' | 'route_group'>('all');
  const assetPageSize = 50;

  useEffect(() => {
    const t = setTimeout(() => { setAssetSearch(assetSearchInput); setAssetPageOffset(0); }, 250);
    return () => clearTimeout(t);
  }, [assetSearchInput]);

  const { data: callableTargetPage, loading: callableTargetPageLoading } = useApi(
    () => (
      isPlatformAdmin && !selectAll
        ? callableTargets.list({
            search: assetSearch || undefined,
            target_type: assetTargetType === 'all' ? undefined : assetTargetType,
            limit: assetPageSize,
            offset: assetPageOffset,
          })
        : Promise.resolve({ data: [], pagination: { total: 0, limit: assetPageSize, offset: 0, has_more: false } })
    ),
    [isPlatformAdmin, selectAll, assetSearch, assetTargetType, assetPageOffset],
  );

  const assetTargets = buildCatalogAssetTargets(
    (callableTargetPage?.data || []) as any[],
    selectedKeys,
  );
  const assetPagePagination = callableTargetPage?.pagination;

  /* submit */
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isReady = name.trim().length > 0;

  const handleCreate = async () => {
    if (!name.trim()) { setNameError(true); return; }
    setError(null);
    setSaving(true);
    try {
      const payload = {
        organization_name: name.trim(),
        max_budget: budgetEnabled && budgetValue ? Number(budgetValue) : undefined,
        rpm_limit: rpmEnabled && rpmValue ? Number(rpmValue) : undefined,
        tpm_limit: tpmEnabled && tpmValue ? Number(tpmValue) : undefined,
        audit_content_storage_enabled: auditStorage,
        callable_target_bindings: selectAll
          ? []
          : selectedKeys.map((callable_key) => ({ callable_key })),
      };
      const created = await organizations.create(payload);

      if (isPlatformAdmin && selectAll) {
        try {
          await organizations.updateAssetAccess(created.organization_id, {
            selected_callable_keys: [],
            select_all_selectable: true,
          });
        } catch {
          /* non-fatal — org created */
        }
      }

      navigate(`/organizations/${created.organization_id}`);
    } catch (err: any) {
      setError(err?.message || 'Failed to create organization');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => navigate('/organizations');

  /* ─────────────── render ─────────────── */
  return (
    <div className="flex h-screen overflow-hidden relative">
      {/* Faded background list */}
      <div className="flex-1 flex flex-col opacity-30">
        <BgList items={bgItems} />
      </div>

      {/* Backdrop */}
      <div className="absolute inset-0 bg-gray-900/20" onClick={handleCancel} />

      {/* Slide-over drawer */}
      <div className="absolute right-0 top-0 h-full w-[500px] bg-white shadow-2xl flex flex-col z-10">
        {/* Drawer header */}
        <div className="flex items-start justify-between px-6 py-5 border-b border-gray-200 shrink-0">
          <div>
            <div className="flex items-center gap-1.5 text-xs text-gray-400 mb-1">
              <Building2 className="w-3.5 h-3.5" />
              <ChevronRight className="w-3 h-3" />
              <span className="text-gray-600 font-medium">New Organization</span>
            </div>
            <h2 className="text-lg font-bold text-gray-900">Create Organization</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Set up a new top-level tenant with its own budget and access controls.
            </p>
          </div>
          <button
            onClick={handleCancel}
            className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400 mt-0.5 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">

          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" /> {error}
            </div>
          )}

          {/* ── Basic Info ── */}
          <div>
            <SectionHeading>Basic Info</SectionHeading>
            <div>
              <FieldLabel label="Organization Name" required />
              <input
                value={name}
                onChange={(e) => { setName(e.target.value); setNameError(false); }}
                onBlur={() => setNameError(!name.trim())}
                placeholder="e.g. Acme Corp"
                className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-colors ${
                  nameError ? 'border-red-400 focus:ring-red-400' : 'border-gray-300'
                }`}
              />
              {nameError && (
                <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" /> Name is required
                </p>
              )}
              <p className="text-xs text-gray-400 mt-1">Must be unique across the platform.</p>
            </div>
          </div>

          <div className="border-t border-gray-200" />

          {/* ── Budget & Spend Limits ── */}
          <div>
            <SectionHeading>Budget &amp; Spend Limits</SectionHeading>
            <div className="space-y-3">
              {/* Budget */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <DollarSign className="w-4 h-4 text-green-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">Budget limit</p>
                      <p className="text-xs text-gray-500">Cap total spend for this org</p>
                    </div>
                  </div>
                  <Toggle enabled={budgetEnabled} onToggle={() => setBudgetEnabled((v) => !v)} />
                </div>
                {budgetEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-green-200">
                    <FieldLabel label="Budget amount" />
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm font-medium">$</span>
                      <input
                        value={budgetValue}
                        onChange={(e) => setBudgetValue(e.target.value)}
                        placeholder="5000.00"
                        type="number"
                        min="0"
                        step="0.01"
                        className="w-full pl-7 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <p className="text-xs text-gray-400 mt-1">Requests will be rejected once spend reaches this amount.</p>
                  </div>
                )}
              </div>

              {/* RPM */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <Gauge className="w-4 h-4 text-purple-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">RPM limit</p>
                      <p className="text-xs text-gray-500">Max requests per minute</p>
                    </div>
                  </div>
                  <Toggle enabled={rpmEnabled} onToggle={() => setRpmEnabled((v) => !v)} />
                </div>
                {rpmEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-purple-200">
                    <FieldLabel label="Requests per minute" />
                    <input
                      value={rpmValue}
                      onChange={(e) => setRpmValue(e.target.value)}
                      placeholder="500"
                      type="number"
                      min="1"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <p className="text-xs text-gray-400 mt-1">Shared across all teams and API keys in this org.</p>
                  </div>
                )}
              </div>

              {/* TPM */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <TrendingUp className="w-4 h-4 text-indigo-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">TPM limit</p>
                      <p className="text-xs text-gray-500">Max tokens per minute</p>
                    </div>
                  </div>
                  <Toggle enabled={tpmEnabled} onToggle={() => setTpmEnabled((v) => !v)} />
                </div>
                {tpmEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-indigo-200">
                    <FieldLabel label="Tokens per minute" />
                    <input
                      value={tpmValue}
                      onChange={(e) => setTpmValue(e.target.value)}
                      placeholder="200000"
                      type="number"
                      min="1"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <p className="text-xs text-gray-400 mt-1">Applies across input + output tokens.</p>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="border-t border-gray-200" />

          {/* ── Settings ── */}
          <div>
            <SectionHeading>Settings</SectionHeading>
            <div className="p-3 rounded-lg bg-gray-50 border border-gray-200">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-medium text-gray-800">Audit content storage</p>
                  <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">
                    Store request and response payloads in audit logs for compliance review.
                  </p>
                </div>
                <Toggle enabled={auditStorage} onToggle={() => setAuditStorage((v) => !v)} />
              </div>
              {auditStorage && (
                <div className="mt-3 flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                  <Info className="w-3.5 h-3.5 shrink-0" />
                  <span>Enabling this increases storage usage. Ensure it aligns with your data retention policy.</span>
                </div>
              )}
            </div>
          </div>

          {/* ── Asset Access (platform admins only) ── */}
          {isPlatformAdmin && (
            <>
              <div className="border-t border-gray-200" />
              <div>
                <SectionHeading>Asset Access</SectionHeading>
                <div className="space-y-3">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <label className={`rounded-lg border px-3 py-2.5 text-sm cursor-pointer transition-colors ${selectAll ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                      <div className="flex items-start gap-2">
                        <input
                          type="radio"
                          name="org-create-asset-strategy"
                          checked={selectAll}
                          onChange={() => { setSelectAll(true); setSelectedKeys([]); }}
                          disabled={saving}
                          className="mt-0.5"
                        />
                        <span>
                          <span className="block font-medium text-gray-900 text-xs">
                            <Shield className="w-3 h-3 text-blue-600 inline mr-1" />Allow all
                          </span>
                          <span className="block text-xs text-gray-500 mt-0.5">Grant every current model and route group.</span>
                        </span>
                      </div>
                    </label>
                    <label className={`rounded-lg border px-3 py-2.5 text-sm cursor-pointer transition-colors ${!selectAll ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                      <div className="flex items-start gap-2">
                        <input
                          type="radio"
                          name="org-create-asset-strategy"
                          checked={!selectAll}
                          onChange={() => setSelectAll(false)}
                          disabled={saving}
                          className="mt-0.5"
                        />
                        <span>
                          <span className="block font-medium text-gray-900 text-xs">Choose a subset</span>
                          <span className="block text-xs text-gray-500 mt-0.5">Pick specific models and route groups.</span>
                        </span>
                      </div>
                    </label>
                  </div>

                  {selectAll ? (
                    <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-3 text-xs text-blue-800">
                      Saving will grant every currently available model and route group to this organization.
                    </div>
                  ) : (
                    <AssetAccessEditor
                      title="Allowed Assets"
                      description="Choose the models and route groups this organization is allowed to use."
                      mode="grant"
                      targets={assetTargets}
                      selectedKeys={selectedKeys}
                      onSelectedKeysChange={setSelectedKeys}
                      loading={callableTargetPageLoading}
                      disabled={saving}
                      searchValue={assetSearchInput}
                      onSearchValueChange={setAssetSearchInput}
                      targetTypeFilter={assetTargetType}
                      onTargetTypeFilterChange={(next) => { setAssetTargetType(next); setAssetPageOffset(0); }}
                      pagination={assetPagePagination}
                      onPageChange={setAssetPageOffset}
                      primaryActionLabel="Allow all current assets"
                      onPrimaryAction={() => { setSelectAll(true); setSelectedKeys([]); }}
                      secondaryActionLabel={selectedKeys.length > 0 ? 'Clear selection' : undefined}
                      onSecondaryAction={selectedKeys.length > 0 ? () => setSelectedKeys([]) : undefined}
                    />
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-gray-200 px-6 py-4 bg-white shrink-0">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs">
              {!isReady ? (
                <span className="flex items-center gap-1 text-amber-600">
                  <AlertCircle className="w-3 h-3" /> Fill in required fields to continue
                </span>
              ) : (
                <span className="flex items-center gap-1 text-green-600">
                  <Check className="w-3 h-3" /> Ready to create
                </span>
              )}
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleCancel}
                disabled={saving}
                className="px-3 py-1.5 text-xs font-medium text-gray-700 border border-gray-300 bg-white rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={saving || !isReady}
                className="px-3 py-1.5 text-xs font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {saving ? 'Creating…' : 'Create Organization'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
