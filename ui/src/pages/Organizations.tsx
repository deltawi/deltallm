import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { resolveUiAccess } from '../lib/authorization';
import { callableTargets, organizations } from '../lib/api';
import {
  assetAccessLoadErrorMessage,
  buildCatalogAccessGroups,
  buildCatalogAssetTargets,
  isScopedAssetAccessFor,
} from '../lib/assetAccess';
import {
  dateTimeLocalUtcInputToIso,
  defaultMonthlyResetUtcInputValue,
  toUtcDateTimeLocalInputValue,
} from '../lib/format';
import RateLimitSummary from '../components/admin/RateLimitSummary';
import Modal from '../components/Modal';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import { ContentCard, IndexShell } from '../components/admin/shells';
import {
  Plus, Building2, Users, DollarSign,
  Shield, AlertCircle, Search, MoreHorizontal,
  CalendarDays,
} from 'lucide-react';

/* ─────────────── sub-components ─────────────── */

function BudgetRing({ spend, budget }: { spend: number; budget: number | null }) {
  if (!budget) return <span className="text-xs text-gray-400 font-medium">Unlimited</span>;
  const pct = Math.min(100, (spend / budget) * 100);
  const r = 18;
  const circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;
  const color = pct > 95 ? '#ef4444' : pct > 80 ? '#f59e0b' : '#3b82f6';
  return (
    <div className="flex items-center gap-2.5">
      <div className="relative w-11 h-11 shrink-0">
        <svg viewBox="0 0 44 44" className="w-11 h-11 -rotate-90">
          <circle cx="22" cy="22" r={r} fill="none" stroke="#e5e7eb" strokeWidth="3.5" />
          <circle cx="22" cy="22" r={r} fill="none" stroke={color} strokeWidth="3.5"
            strokeDasharray={`${dash} ${circ}`} strokeLinecap="round" />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-gray-700">
          {Math.round(pct)}%
        </span>
      </div>
      <div>
        <p className="text-xs font-semibold text-gray-800">
          ${spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}
        </p>
        <p className="text-[10px] text-gray-400">of ${budget.toLocaleString()}</p>
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const map: Record<string, string> = {
    healthy: 'bg-emerald-500',
    warning: 'bg-amber-500',
    over: 'bg-red-500',
    idle: 'bg-gray-300',
  };
  return <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${map[status] ?? 'bg-gray-300'}`} />;
}

function getStatus(row: any): string {
  const spend = row.spend || 0;
  const budget = row.max_budget ?? null;
  if (budget !== null && spend > budget) return 'over';
  if (budget !== null && spend >= budget * 0.8) return 'warning';
  if (spend === 0) return 'idle';
  return 'healthy';
}

const STATUS_TABS = ['All', 'Healthy', 'Over budget', 'Idle'] as const;
type StatusTab = typeof STATUS_TABS[number];

/* ─────────────── page ─────────────── */

export default function Organizations() {
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';
  const uiAccess = resolveUiAccess(authMode, session);

  /* ── list state ── */
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const [statusTab, setStatusTab] = useState<StatusTab>('All');
  const pageSize = 20;

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPageOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const { data: result, loading, refetch } = useApi(
    () => organizations.list({ search, limit: pageSize, offset: pageOffset }),
    [search, pageOffset],
  );
  const rawItems: any[] = result?.data || [];
  const pagination = result?.pagination;

  /* client-side status filter (only filters the current page) */
  const items = statusTab === 'All'
    ? rawItems
    : rawItems.filter((r) => {
        const s = getStatus(r);
        if (statusTab === 'Healthy') return s === 'healthy' || s === 'warning';
        if (statusTab === 'Over budget') return s === 'over';
        if (statusTab === 'Idle') return s === 'idle';
        return true;
      });

  /* ── summary strip (computed from current page) ── */
  const totalSpend = rawItems.reduce((a, r) => a + (r.spend || 0), 0);
  const totalTeams = rawItems.reduce((a, r) => a + (r.team_count || 0), 0);
  const overCount = rawItems.filter((r) => getStatus(r) === 'over').length;

  /* ── asset editor state ── */
  const [assetSearchInput, setAssetSearchInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const [assetPageOffset, setAssetPageOffset] = useState(0);
  const [accessGroupPageOffset, setAccessGroupPageOffset] = useState(0);
  const [assetTargetType, setAssetTargetType] = useState<'all' | 'model' | 'route_group'>('all');
  const assetPageSize = 50;
  const accessGroupPageSize = 50;

  useEffect(() => {
    const t = setTimeout(() => {
      setAssetSearch(assetSearchInput);
      setAssetPageOffset(0);
      setAccessGroupPageOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [assetSearchInput]);

  /* ── modal state ── */
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState({
    organization_name: '',
    max_budget: '',
    soft_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    rph_limit: '',
    rpd_limit: '',
    tpd_limit: '',
    monthly_reset_enabled: false,
    budget_reset_at: '',
    existing_budget_duration: '',
    existing_budget_reset_at: '',
    audit_content_storage_enabled: false,
    select_all_current_assets: false,
    selected_callable_keys: [] as string[],
    selected_access_group_keys: [] as string[],
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);

  const { data: editAssetAccess, error: editAssetAccessError, loading: editAssetAccessLoading } = useApi(
    () => (editItem ? organizations.assetAccess(editItem.organization_id, { include_targets: false }) : Promise.resolve(null)),
    [editItem?.organization_id],
  );
  const currentEditAssetAccess = editItem && isScopedAssetAccessFor(editAssetAccess, {
    scopeType: 'organization',
    scopeId: editItem.organization_id,
    organizationId: editItem.organization_id,
  })
    ? editAssetAccess
    : null;
  const editAssetAccessPending = isPlatformAdmin && !!editItem && (editAssetAccessLoading || !currentEditAssetAccess);
  const { data: callableTargetPage, error: callableTargetPageError, loading: callableTargetPageLoading } = useApi(
    () => (
      isPlatformAdmin && !!editItem && !form.select_all_current_assets
        ? callableTargets.list({
            search: assetSearch || undefined,
            target_type: assetTargetType === 'all' ? undefined : assetTargetType,
            limit: assetPageSize,
            offset: assetPageOffset,
          })
        : Promise.resolve({ data: [], pagination: { total: 0, limit: assetPageSize, offset: 0, has_more: false } })
    ),
    [isPlatformAdmin, editItem?.organization_id, form.select_all_current_assets, assetSearch, assetTargetType, assetPageOffset],
  );
  const { data: callableTargetAccessGroups, error: accessGroupError, loading: accessGroupLoading } = useApi(
    () => (
      isPlatformAdmin && !!editItem && !form.select_all_current_assets
        ? callableTargets.listAccessGroups({
            search: assetSearch || undefined,
            include_members: false,
            limit: accessGroupPageSize,
            offset: accessGroupPageOffset,
          })
        : Promise.resolve({ data: [], pagination: { total: 0, limit: accessGroupPageSize, offset: 0, has_more: false } })
    ),
    [isPlatformAdmin, editItem?.organization_id, form.select_all_current_assets, assetSearch, accessGroupPageOffset],
  );

  const resetForm = () => {
    setForm({
      organization_name: '',
      max_budget: '',
      soft_budget: '',
      rpm_limit: '',
      tpm_limit: '',
      rph_limit: '',
      rpd_limit: '',
      tpd_limit: '',
      monthly_reset_enabled: false,
      budget_reset_at: '',
      existing_budget_duration: '',
      existing_budget_reset_at: '',
      audit_content_storage_enabled: false,
      select_all_current_assets: false,
      selected_callable_keys: [],
      selected_access_group_keys: [],
    });
    setError(null);
    setAssetSearchInput('');
    setAssetSearch('');
    setAssetPageOffset(0);
    setAccessGroupPageOffset(0);
    setAssetTargetType('all');
  };

  useEffect(() => {
    if (!editItem || !currentEditAssetAccess) return;
    setForm((c) => ({
      ...c,
      select_all_current_assets: !!currentEditAssetAccess.auto_follow_catalog,
      selected_callable_keys: currentEditAssetAccess.selected_callable_keys || [],
      selected_access_group_keys: currentEditAssetAccess.selected_access_group_keys || [],
    }));
  }, [editItem, currentEditAssetAccess]);

  const handleMonthlyResetToggle = (checked: boolean) => {
    setForm((c) => ({
      ...c,
      monthly_reset_enabled: checked,
      budget_reset_at: checked && (!c.budget_reset_at || c.existing_budget_duration !== '1mo')
        ? defaultMonthlyResetUtcInputValue()
        : c.budget_reset_at,
    }));
  };

  const handleSave = async () => {
    if (!editItem) return;
    if (assetAccessLoadError) {
      setError(assetAccessLoadError);
      return;
    }
    if (editAssetAccessPending || assetAccessLoading) {
      setError('Wait for asset access options to finish loading before saving the organization.');
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const resetAtIso = form.monthly_reset_enabled
        ? dateTimeLocalUtcInputToIso(form.budget_reset_at)
        : null;
      if (form.monthly_reset_enabled && !resetAtIso) {
        setError('Choose a valid next reset date.');
        return;
      }
      const payload: Record<string, unknown> = {
        organization_name: form.organization_name || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        soft_budget: form.soft_budget ? Number(form.soft_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        rph_limit: form.rph_limit ? Number(form.rph_limit) : undefined,
        rpd_limit: form.rpd_limit ? Number(form.rpd_limit) : undefined,
        tpd_limit: form.tpd_limit ? Number(form.tpd_limit) : undefined,
        audit_content_storage_enabled: !!form.audit_content_storage_enabled,
      };
      if (form.monthly_reset_enabled) {
        payload.budget_duration = '1mo';
        payload.budget_reset_at = resetAtIso;
      } else if (form.existing_budget_duration === '1mo') {
        payload.budget_duration = null;
        payload.budget_reset_at = null;
      }
      await organizations.update(editItem.organization_id, payload);
      if (isPlatformAdmin) {
        await organizations.updateAssetAccess(editItem.organization_id, {
          selected_callable_keys: form.select_all_current_assets ? [] : form.selected_callable_keys,
          selected_access_group_keys: form.select_all_current_assets ? [] : form.selected_access_group_keys,
          select_all_selectable: form.select_all_current_assets,
        });
      }
      setPageError(null);
      setEditItem(null);
      resetForm();
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to save organization');
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (row: any) => {
    setPageError(null);
    setForm({
      organization_name: row.organization_name || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      soft_budget: row.soft_budget != null ? String(row.soft_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      rph_limit: row.rph_limit != null ? String(row.rph_limit) : '',
      rpd_limit: row.rpd_limit != null ? String(row.rpd_limit) : '',
      tpd_limit: row.tpd_limit != null ? String(row.tpd_limit) : '',
      monthly_reset_enabled: row.budget_duration === '1mo' && !!row.budget_reset_at,
      budget_reset_at: toUtcDateTimeLocalInputValue(row.budget_reset_at),
      existing_budget_duration: row.budget_duration || '',
      existing_budget_reset_at: row.budget_reset_at || '',
      audit_content_storage_enabled: !!row.audit_content_storage_enabled,
      select_all_current_assets: false,
      selected_callable_keys: [],
      selected_access_group_keys: [],
    });
    setAssetSearchInput('');
    setAssetSearch('');
    setAssetPageOffset(0);
    setAccessGroupPageOffset(0);
    setAssetTargetType('all');
    setEditItem(row);
  };

  const assetTargets = buildCatalogAssetTargets(
    (callableTargetPage?.data || []) as any[],
    form.selected_callable_keys,
    currentEditAssetAccess?.selected_callable_keys || [],
  );
  const assetAccessGroups = buildCatalogAccessGroups(
    callableTargetAccessGroups?.data || [],
    form.selected_access_group_keys,
    currentEditAssetAccess?.selected_access_group_keys || [],
  );
  const assetPagePagination = callableTargetPage?.pagination;
  const accessGroupPagination = callableTargetAccessGroups?.pagination;
  const assetAccessLoading = !form.select_all_current_assets && (callableTargetPageLoading || accessGroupLoading);
  const assetAccessLoadError = isPlatformAdmin && !!editItem
    ? assetAccessLoadErrorMessage(
        editAssetAccessError || (!form.select_all_current_assets ? callableTargetPageError || accessGroupError : null),
      )
    : null;

  /* ── pagination helpers ── */
  const total = pagination?.total ?? 0;
  const currentPage = Math.floor(pageOffset / pageSize) + 1;
  const totalPages = Math.ceil(total / pageSize);
  const hasPrev = pageOffset > 0;
  const hasNext = pagination?.has_more ?? false;

  /* ─────────────── render ─────────────── */
  return (
    <IndexShell
      title="Organizations"
      titleIcon={Building2}
      count={total}
      action={uiAccess.organization_create ? (
        <button
          onClick={() => navigate('/organizations/new')}
          className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" /> Create Organization
        </button>
      ) : undefined}
      toolbar={(
        <div className="flex items-center gap-3">
          <div className="relative w-72">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search organizations…"
              className="h-8 w-full rounded-lg border border-gray-300 pl-8 pr-3 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="ml-auto flex gap-1.5">
            {STATUS_TABS.map((f) => (
              <button
                key={f}
                onClick={() => setStatusTab(f)}
                className={`rounded-full px-3 py-1 text-xs transition-colors ${
                  statusTab === f
                    ? 'bg-blue-50 font-medium text-blue-700'
                    : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
      )}
      summaryItems={[
        {
          label: 'Total spend',
          value: `$${totalSpend.toLocaleString(undefined, { maximumFractionDigits: 0 })}`,
          icon: DollarSign,
          iconClassName: 'text-green-600',
        },
        {
          label: 'Active teams',
          value: String(totalTeams),
          icon: Users,
          iconClassName: 'text-blue-600',
        },
        {
          label: 'Scoped assets',
          value: '—',
          icon: Shield,
          iconClassName: 'text-purple-600',
        },
        {
          label: 'Over budget',
          value: String(overCount),
          icon: AlertCircle,
          iconClassName: 'text-red-500',
        },
      ]}
      notice={pageError ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {pageError}
        </div>
      ) : undefined}
    >
      <ContentCard>
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Organization</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Budget Usage</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Rate Limits</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Members</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="border-b border-gray-100">
                    {[1, 2, 3, 4, 5].map((j) => (
                      <td key={j} className="px-4 py-4">
                        <div className="h-4 bg-gray-100 rounded animate-pulse w-24" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-16 text-center text-sm text-gray-400">
                    {statusTab !== 'All'
                      ? `No organizations match the "${statusTab}" filter.`
                      : search
                      ? 'No organizations match your search.'
                      : 'No organizations created yet.'}
                    {statusTab === 'All' && !search && uiAccess.organization_create && (
                      <button
                        onClick={() => navigate('/organizations/new')}
                        className="ml-1 text-blue-600 hover:underline"
                      >
                        Create one
                      </button>
                    )}
                  </td>
                </tr>
              ) : (
                items.map((row: any, i: number) => {
                  const status = getStatus(row);
                  const name = row.organization_name || row.organization_id;
                  return (
                    <tr
                      key={row.organization_id}
                      onClick={() => navigate(`/organizations/${row.organization_id}`)}
                      className={`border-b border-gray-100 hover:bg-blue-50/40 cursor-pointer transition-colors ${
                        i === items.length - 1 ? 'border-b-0' : ''
                      }`}
                    >
                      {/* Organization */}
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-100 to-blue-200 flex items-center justify-center shrink-0">
                            <span className="text-xs font-bold text-blue-700">{name[0].toUpperCase()}</span>
                          </div>
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="font-semibold text-gray-900 text-sm">{name}</span>
                              <StatusDot status={status} />
                            </div>
                            <code className="text-[10px] text-gray-400 font-mono">{row.organization_id}</code>
                          </div>
                        </div>
                      </td>

                      {/* Budget ring */}
                      <td className="px-4 py-3.5">
                        <BudgetRing spend={row.spend || 0} budget={row.max_budget ?? null} />
                      </td>

                      {/* Rate limits */}
                      <td className="px-4 py-3.5">
                        <RateLimitSummary
                          rpm_limit={row.rpm_limit}
                          tpm_limit={row.tpm_limit}
                          rph_limit={row.rph_limit}
                          rpd_limit={row.rpd_limit}
                          tpd_limit={row.tpd_limit}
                        />
                      </td>

                      {/* Members + teams */}
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className="flex items-center gap-1 text-gray-700">
                            <Users className="w-3.5 h-3.5 text-gray-400" />
                            <span className="text-sm font-medium">{row.member_count ?? row.user_count ?? 0}</span>
                          </div>
                          <span className="text-gray-300">·</span>
                          <div className="flex items-center gap-1 text-gray-500 text-xs">
                            <Building2 className="w-3.5 h-3.5 text-gray-400" />
                            {row.team_count ?? 0} teams
                          </div>
                        </div>
                      </td>

                      {/* Actions */}
                      <td className="px-4 py-3.5" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => navigate(`/organizations/${row.organization_id}`)}
                            className="px-2.5 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
                          >
                            View
                          </button>
                          {row.capabilities?.edit ? (
                            <button
                              onClick={(e) => { e.stopPropagation(); openEdit(row); }}
                              className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 transition-colors"
                            >
                              <MoreHorizontal className="w-4 h-4" />
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>

          {/* Pagination footer */}
          <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-t border-gray-200">
            <span className="text-xs text-gray-500">
              {loading
                ? 'Loading…'
                : statusTab !== 'All'
                ? `${items.length} result${items.length !== 1 ? 's' : ''} (filtered)`
                : `Showing ${Math.min(pageOffset + 1, total)}–${Math.min(pageOffset + pageSize, total)} of ${total}`}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setPageOffset(Math.max(0, pageOffset - pageSize))}
                disabled={!hasPrev || statusTab !== 'All'}
                className="px-3 py-1 text-xs text-gray-500 border border-gray-200 rounded-md hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                Previous
              </button>
              {totalPages > 0 && (
                <button className="px-3 py-1 text-xs text-blue-600 border border-blue-200 bg-blue-50 rounded-md font-medium">
                  {currentPage}
                </button>
              )}
              <button
                onClick={() => setPageOffset(pageOffset + pageSize)}
                disabled={!hasNext || statusTab !== 'All'}
                className="px-3 py-1 text-xs text-gray-500 border border-gray-200 rounded-md hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                Next
              </button>
            </div>
          </div>
      </ContentCard>

      {/* ─────────────── Create / Edit Modal ─────────────── */}
      <Modal
        open={!!editItem}
        onClose={() => { setPageError(null); setEditItem(null); resetForm(); }}
        title="Edit Organization"
      >
        <div className="space-y-4">
          {(error || assetAccessLoadError) && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error || assetAccessLoadError}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Organization Name</label>
            <input
              value={form.organization_name}
              onChange={(e) => setForm({ ...form, organization_name: e.target.value })}
              placeholder="Acme Corp"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
            <input
              type="number"
              value={form.max_budget}
              onChange={(e) => setForm({ ...form, max_budget: e.target.value })}
              placeholder="1000"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Soft Budget Alert ($)</label>
            <input
              type="number"
              value={form.soft_budget}
              onChange={(e) => setForm({ ...form, soft_budget: e.target.value })}
              placeholder="800"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-400 mt-1">Notification threshold. Must be less than or equal to max budget.</p>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <CalendarDays className="h-4 w-4 text-blue-600" />
                <span className="text-sm font-medium text-gray-800">Monthly reset</span>
              </div>
              <input
                type="checkbox"
                checked={!!form.monthly_reset_enabled}
                onChange={(e) => handleMonthlyResetToggle(e.target.checked)}
              />
            </div>
            {form.monthly_reset_enabled && (
              <div className="mt-3">
                <label className="block text-sm font-medium text-gray-700 mb-1">Next reset (UTC)</label>
                <input
                  type="datetime-local"
                  value={form.budget_reset_at}
                  onChange={(e) => setForm({ ...form, budget_reset_at: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input
                type="number"
                value={form.rpm_limit}
                onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })}
                placeholder="100"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-400 mt-1">Requests per minute</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input
                type="number"
                value={form.tpm_limit}
                onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })}
                placeholder="100000"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-400 mt-1">Tokens per minute</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPH Limit</label>
              <input
                type="number"
                value={form.rph_limit}
                onChange={(e) => setForm({ ...form, rph_limit: e.target.value })}
                placeholder="5000"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-400 mt-1">Requests per hour</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPD Limit</label>
              <input
                type="number"
                value={form.rpd_limit}
                onChange={(e) => setForm({ ...form, rpd_limit: e.target.value })}
                placeholder="50000"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-400 mt-1">Requests per day</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPD Limit</label>
              <input
                type="number"
                value={form.tpd_limit}
                onChange={(e) => setForm({ ...form, tpd_limit: e.target.value })}
                placeholder="2000000"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-400 mt-1">Tokens per day</p>
            </div>
          </div>
          <label className="flex items-start gap-3 p-3 border border-gray-200 rounded-lg bg-gray-50 cursor-pointer">
            <input
              type="checkbox"
              checked={!!form.audit_content_storage_enabled}
              onChange={(e) => setForm({ ...form, audit_content_storage_enabled: e.target.checked })}
              className="mt-0.5"
            />
            <span className="text-sm text-gray-700">
              Store request and response payload content in audit logs for this organization.
            </span>
          </label>
          {isPlatformAdmin && (
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <label className={`rounded-lg border px-3 py-2 text-sm cursor-pointer ${form.select_all_current_assets ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                  <div className="flex items-start gap-2">
                    <input
                      type="radio"
                      name="organization-asset-strategy"
                      checked={form.select_all_current_assets}
                      onChange={() => setForm((c) => ({
                        ...c,
                        select_all_current_assets: true,
                        selected_callable_keys: [],
                        selected_access_group_keys: [],
                      }))}
                      disabled={saving}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block font-medium text-gray-900">Allow all assets, including future additions</span>
                      <span className="block text-xs text-gray-500">Grant every asset now and automatically include newly added models and route groups.</span>
                    </span>
                  </div>
                </label>
                <label className={`rounded-lg border px-3 py-2 text-sm cursor-pointer ${!form.select_all_current_assets ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                  <div className="flex items-start gap-2">
                    <input
                      type="radio"
                      name="organization-asset-strategy"
                      checked={!form.select_all_current_assets}
                      onChange={() => setForm((c) => ({ ...c, select_all_current_assets: false }))}
                      disabled={saving}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block font-medium text-gray-900">Choose a subset</span>
                      <span className="block text-xs text-gray-500">Manually pick the models and route groups this org can use.</span>
                    </span>
                  </div>
                </label>
              </div>
              {form.select_all_current_assets ? (
                <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-3 text-xs text-blue-800">
                  {editItem && currentEditAssetAccess
                    ? `This organization currently has ${currentEditAssetAccess.summary.effective_total} of ${currentEditAssetAccess.summary.selectable_total} assets granted. Saving will align it to all current assets and automatically include future additions.`
                    : 'Saving will grant every currently available model and route group to this organization and automatically include future additions.'}
                </div>
              ) : (
                <AssetAccessEditor
                  title="Allowed Assets"
                  description="Choose the models and route groups this organization is allowed to use. Teams, keys, and users can only narrow from this set."
                  mode="grant"
                  targets={assetTargets}
                  selectedKeys={form.selected_callable_keys}
                  onSelectedKeysChange={(selected_callable_keys) => setForm({ ...form, selected_callable_keys })}
                  accessGroups={assetAccessGroups}
                  selectedAccessGroupKeys={form.selected_access_group_keys}
                  onSelectedAccessGroupKeysChange={(selected_access_group_keys) => setForm({ ...form, selected_access_group_keys })}
                  targetsLoading={callableTargetPageLoading || editAssetAccessPending}
                  accessGroupsLoading={accessGroupLoading || editAssetAccessPending}
                  disabled={saving || editAssetAccessPending || Boolean(assetAccessLoadError)}
                  searchValue={assetSearchInput}
                  onSearchValueChange={setAssetSearchInput}
                  targetTypeFilter={assetTargetType}
                  onTargetTypeFilterChange={(next) => { setAssetTargetType(next); setAssetPageOffset(0); }}
                  pagination={assetPagePagination}
                  onPageChange={setAssetPageOffset}
                  accessGroupPagination={accessGroupPagination}
                  onAccessGroupPageChange={setAccessGroupPageOffset}
                  primaryActionLabel="Allow all assets"
                  onPrimaryAction={() => setForm((c) => ({
                    ...c,
                    select_all_current_assets: true,
                    selected_callable_keys: [],
                    selected_access_group_keys: [],
                  }))}
                  secondaryActionLabel={form.selected_callable_keys.length > 0 || form.selected_access_group_keys.length > 0 ? 'Clear selection' : undefined}
                  onSecondaryAction={
                    form.selected_callable_keys.length > 0 || form.selected_access_group_keys.length > 0
                      ? () => setForm((c) => ({ ...c, selected_callable_keys: [], selected_access_group_keys: [] }))
                      : undefined
                  }
                />
              )}
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button
              onClick={() => { setPageError(null); setEditItem(null); resetForm(); }}
              className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving || editAssetAccessPending || assetAccessLoading || Boolean(assetAccessLoadError)}
              className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving…' : editItem ? 'Save Changes' : 'Create'}
            </button>
          </div>
        </div>
      </Modal>
    </IndexShell>
  );
}
