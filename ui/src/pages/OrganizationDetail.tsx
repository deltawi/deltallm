import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link, useLocation } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { callableTargets, organizations } from '../lib/api';
import { buildCatalogAssetTargets } from '../lib/assetAccess';
import { useAuth } from '../lib/auth';
import {
  dateTimeLocalUtcInputToIso,
  defaultMonthlyResetUtcInputValue,
  fmtUtcDateTime,
  toUtcDateTimeLocalInputValue,
} from '../lib/format';
import Modal from '../components/Modal';
import UserSearchSelect from '../components/UserSearchSelect';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import {
  DetailMetricCard,
  EntityDetailShell,
  TextTabs,
} from '../components/admin/shells';
import {
  ArrowLeft, Building2, Users, DollarSign, Gauge, TrendingUp, Pencil, Plus,
  UserPlus, Trash2, ChevronRight, Shield, CheckCircle2, AlertTriangle,
  MoreHorizontal, ExternalLink, Info, CalendarDays,
} from 'lucide-react';

/* ─────────────── helpers ─────────────── */

const AVATAR_COLORS = [
  'bg-violet-100 text-violet-700',
  'bg-blue-100 text-blue-700',
  'bg-emerald-100 text-emerald-700',
  'bg-amber-100 text-amber-700',
  'bg-pink-100 text-pink-700',
  'bg-teal-100 text-teal-700',
  'bg-rose-100 text-rose-700',
  'bg-cyan-100 text-cyan-700',
  'bg-orange-100 text-orange-700',
  'bg-indigo-100 text-indigo-700',
];

function getInitials(email?: string | null, accountId?: string): string {
  if (email) {
    const local = email.split('@')[0];
    const parts = local.split(/[._-]/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return local.slice(0, 2).toUpperCase();
  }
  return (accountId || '??').slice(0, 2).toUpperCase();
}

const ROLE_LABELS: Record<string, { label: string; cls: string }> = {
  org_owner:   { label: 'Owner',   cls: 'bg-purple-100 text-purple-700' },
  org_admin:   { label: 'Admin',   cls: 'bg-blue-100 text-blue-700' },
  org_member:  { label: 'Member',  cls: 'bg-gray-100 text-gray-700' },
  org_viewer:  { label: 'Viewer',  cls: 'bg-gray-50 text-gray-500' },
  org_billing: { label: 'Billing', cls: 'bg-amber-100 text-amber-700' },
  org_auditor: { label: 'Auditor', cls: 'bg-teal-100 text-teal-700' },
};

type TabId = 'overview' | 'teams' | 'members' | 'assets';

/* ─────────────── sub-components ─────────────── */

function SpendBar({ spend, budget }: { spend: number; budget: number | null }) {
  if (!budget) return <span className="text-xs text-gray-400">No limit</span>;
  const pct = Math.min(100, (spend / budget) * 100);
  const color = pct > 95 ? 'bg-red-500' : pct > 80 ? 'bg-amber-500' : 'bg-blue-500';
  return (
    <div className="w-28">
      <div className="flex justify-between text-xs mb-1">
        <span className="font-medium text-gray-700">${spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
        <span className="text-gray-400">/${budget.toLocaleString()}</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ─────────────── page ─────────────── */

export default function OrganizationDetail() {
  const { orgId } = useParams<{ orgId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';
  const [tab, setTab] = useState<TabId>('overview');

  useEffect(() => {
    const hashTab = location.hash.replace('#', '');
    if (hashTab === 'overview' || hashTab === 'teams' || hashTab === 'members' || hashTab === 'assets') {
      setTab(hashTab);
    }
  }, [location.hash]);

  /* ── data ── */
  const { data: org, loading: orgLoading, refetch: refetchOrg } = useApi(
    () => organizations.get(orgId!), [orgId],
  );
  const { data: orgTeams, loading: teamsLoading } = useApi(
    () => organizations.teams(orgId!), [orgId],
  );
  const { data: orgMembers, loading: membersLoading, refetch: refetchMembers } = useApi(
    () => organizations.members(orgId!), [orgId],
  );
  const { data: orgAssetAccess, loading: orgAssetAccessLoading, refetch: refetchOrgAssetAccess } = useApi(
    () => (isPlatformAdmin ? organizations.assetAccess(orgId!, { include_targets: false }) : Promise.resolve(null)),
    [orgId, isPlatformAdmin],
  );
  /* full targets: only loaded when assets tab is active */
  const { data: orgAssetTargetsFull, loading: orgAssetTargetsFullLoading } = useApi(
    () => (tab === 'assets' && isPlatformAdmin
      ? organizations.assetAccess(orgId!, { include_targets: true })
      : Promise.resolve(null)),
    [orgId, isPlatformAdmin, tab],
  );

  /* ── edit org modal ── */
  const [isEditingSettings, setIsEditingSettings] = useState(false);
  const [isEditingAssets, setIsEditingAssets] = useState(false);
  const [assetSearchInput, setAssetSearchInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const [assetPageOffset, setAssetPageOffset] = useState(0);
  const [assetTargetType, setAssetTargetType] = useState<'all' | 'model' | 'route_group'>('all');
  const assetPageSize = 50;
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
  });
  const [saving, setSaving] = useState(false);
  const [pageError, setPageError] = useState<string | null>(
    typeof location.state === 'object' && location.state && 'pageWarning' in location.state
      ? String((location.state as { pageWarning?: string }).pageWarning || '') || null
      : null,
  );
  const [orgError, setOrgError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => { setAssetSearch(assetSearchInput); setAssetPageOffset(0); }, 250);
    return () => clearTimeout(t);
  }, [assetSearchInput]);

  useEffect(() => {
    if (!isEditingAssets || !orgAssetAccess) return;
    setForm((c) => ({
      ...c,
      select_all_current_assets: !!orgAssetAccess.auto_follow_catalog,
      selected_callable_keys: orgAssetAccess.selected_callable_keys || [],
    }));
  }, [isEditingAssets, orgAssetAccess]);

  const { data: callableTargetPage, loading: callableTargetPageLoading } = useApi(
    () => (
      isPlatformAdmin && isEditingAssets && !form.select_all_current_assets
        ? callableTargets.list({
            search: assetSearch || undefined,
            target_type: assetTargetType === 'all' ? undefined : assetTargetType,
            limit: assetPageSize,
            offset: assetPageOffset,
          })
        : Promise.resolve({ data: [], pagination: { total: 0, limit: assetPageSize, offset: 0, has_more: false } })
    ),
    [isPlatformAdmin, isEditingAssets, form.select_all_current_assets, assetSearch, assetTargetType, assetPageOffset],
  );

  const openEditSettings = () => {
    if (!org) return;
    setOrgError(null);
    setForm((c) => ({
      ...c,
      organization_name: org.organization_name || '',
      max_budget: org.max_budget != null ? String(org.max_budget) : '',
      soft_budget: org.soft_budget != null ? String(org.soft_budget) : '',
      rpm_limit: org.rpm_limit != null ? String(org.rpm_limit) : '',
      tpm_limit: org.tpm_limit != null ? String(org.tpm_limit) : '',
      rph_limit: org.rph_limit != null ? String(org.rph_limit) : '',
      rpd_limit: org.rpd_limit != null ? String(org.rpd_limit) : '',
      tpd_limit: org.tpd_limit != null ? String(org.tpd_limit) : '',
      monthly_reset_enabled: org.budget_duration === '1mo' && !!org.budget_reset_at,
      budget_reset_at: toUtcDateTimeLocalInputValue(org.budget_reset_at),
      existing_budget_duration: org.budget_duration || '',
      existing_budget_reset_at: org.budget_reset_at || '',
      audit_content_storage_enabled: !!org.audit_content_storage_enabled,
    }));
    setIsEditingSettings(true);
  };

  const handleMonthlyResetToggle = (checked: boolean) => {
    setForm((c) => ({
      ...c,
      monthly_reset_enabled: checked,
      budget_reset_at: checked && (!c.budget_reset_at || c.existing_budget_duration !== '1mo')
        ? defaultMonthlyResetUtcInputValue()
        : c.budget_reset_at,
    }));
  };

  const openEditAssets = () => {
    if (!org) return;
    setOrgError(null);
    setForm((c) => ({
      ...c,
      select_all_current_assets: false,
      selected_callable_keys: orgAssetAccess?.selected_callable_keys || [],
    }));
    setAssetSearchInput('');
    setAssetSearch('');
    setAssetPageOffset(0);
    setAssetTargetType('all');
    setIsEditingAssets(true);
  };

  const handleSaveSettings = async () => {
    setSaving(true);
    setOrgError(null);
    try {
      const resetAtIso = form.monthly_reset_enabled
        ? dateTimeLocalUtcInputToIso(form.budget_reset_at)
        : null;
      if (form.monthly_reset_enabled && !resetAtIso) {
        setOrgError('Choose a valid next reset date.');
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
      await organizations.update(orgId!, payload);
      setIsEditingSettings(false);
      refetchOrg();
    } catch (err: any) {
      setOrgError(err?.message || 'Failed to update organization');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAssets = async () => {
    setSaving(true);
    setOrgError(null);
    try {
      await organizations.updateAssetAccess(orgId!, {
        selected_callable_keys: form.select_all_current_assets ? [] : form.selected_callable_keys,
        select_all_selectable: form.select_all_current_assets,
      });
      refetchOrgAssetAccess();
      setIsEditingAssets(false);
      refetchOrg();
    } catch (err: any) {
      setOrgError(err?.message || 'Failed to update asset access');
    } finally {
      setSaving(false);
    }
  };

  const openCreateTeam = () => {
    const params = new URLSearchParams();
    params.set('organization_id', orgId || '');
    params.set('return_to', `${location.pathname}#teams`);
    navigate(`/teams/new?${params.toString()}`);
  };

  /* ── add member modal ── */
  const [showAddMember, setShowAddMember] = useState(false);
  const [memberSearch, setMemberSearch] = useState('');
  const [memberForm, setMemberForm] = useState({ account_id: '', role: 'org_member' });
  const [memberError, setMemberError] = useState<string | null>(null);

  const { data: memberCandidates, loading: memberCandidatesLoading } = useApi(
    () => showAddMember ? organizations.memberCandidates(orgId!, { search: memberSearch, limit: 50 }) : Promise.resolve([]),
    [orgId, showAddMember, memberSearch],
  );

  const openAddMember = () => {
    setMemberError(null);
    setMemberSearch('');
    setMemberForm({ account_id: '', role: 'org_member' });
    setShowAddMember(true);
  };

  const handleAddMember = async () => {
    if (!memberForm.account_id) { setMemberError('Select an account to add.'); return; }
    setSaving(true);
    setMemberError(null);
    try {
      await organizations.addMember(orgId!, { account_id: memberForm.account_id, role: memberForm.role });
      setShowAddMember(false);
      setMemberForm({ account_id: '', role: 'org_member' });
      setMemberSearch('');
      refetchMembers();
    } catch (err: any) {
      setMemberError(err?.message || 'Failed to add member');
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveMember = async (membershipId: string) => {
    if (!confirm('Remove this organization member?')) return;
    setSaving(true);
    setPageError(null);
    try {
      await organizations.removeMember(orgId!, membershipId);
      refetchMembers();
    } catch (err: any) {
      setPageError(err?.message || 'Failed to remove member');
    } finally {
      setSaving(false);
    }
  };

  /* ── derived ── */
  const teamList: any[] = orgTeams || [];
  const memberList: any[] = orgMembers || [];
  const orgCapabilities = org?.capabilities || {};
  const canEditOrganization = Boolean(orgCapabilities.edit);
  const canAddTeam = Boolean(orgCapabilities.add_team);
  const canManageMembers = Boolean(orgCapabilities.manage_members);
  const canManageAssets = Boolean(orgCapabilities.manage_assets);
  const spend = org?.spend || 0;
  const budget = org?.max_budget ?? null;
  const spendPct = budget ? Math.min(100, Math.round((spend / budget) * 100)) : null;
  const orgAssetSummary = orgAssetAccess?.summary;
  const assetPct = orgAssetSummary && orgAssetSummary.selectable_total > 0
    ? Math.round((orgAssetSummary.selected_total / orgAssetSummary.selectable_total) * 100)
    : null;

  const orgAssetTargets = buildCatalogAssetTargets(
    (callableTargetPage?.data || []) as any[],
    form.selected_callable_keys,
  );
  const orgAssetPagination = callableTargetPage?.pagination;

  /* teams over 80% of budget = "warning" for alert card */
  const warningTeam = teamList.find((t: any) => {
    if (!t.max_budget || !t.spend) return false;
    return (t.spend / t.max_budget) >= 0.8;
  });

  /* ── loading / not found ── */
  if (orgLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!org) {
    return (
      <div className="p-6">
        <p className="text-gray-500">Organization not found.</p>
        <Link to="/organizations" className="text-blue-600 text-sm mt-2 inline-block">Back to Organizations</Link>
      </div>
    );
  }

  const orgName = org.organization_name || org.organization_id;

  return (
    <EntityDetailShell
      breadcrumbs={[
        { label: 'Organizations', onClick: () => navigate('/organizations'), icon: ArrowLeft },
        { label: orgName },
      ]}
      avatar={(
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-blue-700 shadow-sm">
          <span className="text-lg font-bold text-white">{orgName[0].toUpperCase()}</span>
        </div>
      )}
      title={orgName}
      badges={(
        <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
          <CheckCircle2 className="h-3.5 w-3.5" /> Active
        </span>
      )}
      meta={(
        <div className="flex items-center gap-3">
          <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-400">
            {org.organization_id}
          </code>
          {org.created_at && (
            <span className="text-xs text-gray-400">
              Created {new Date(org.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
            </span>
          )}
        </div>
      )}
      action={canEditOrganization ? (
        <button
          onClick={openEditSettings}
          className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
        >
          <Pencil className="h-3.5 w-3.5" /> Edit
        </button>
      ) : undefined}
      metrics={(
        <>
          <DetailMetricCard
            icon={DollarSign}
            label="Budget used"
            value={spendPct != null ? `${spendPct}%` : `$${spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
            sub={budget ? `$${spend.toLocaleString(undefined, { maximumFractionDigits: 0 })} of $${budget.toLocaleString()}` : 'No limit'}
            tone={spendPct != null && spendPct > 80 ? 'amber' : 'green'}
          />
          <DetailMetricCard
            icon={Building2}
            label="Teams"
            value={String(teamList.length)}
            sub={`${teamList.filter((t: any) => (t.spend || 0) > 0).length} active`}
            tone="blue"
          />
          <DetailMetricCard
            icon={Users}
            label="Members"
            value={String(memberList.length)}
            sub="across all teams"
            tone="violet"
          />
          <DetailMetricCard
            icon={Shield}
            label="Assets granted"
            value={
              orgAssetSummary
                ? `${orgAssetSummary.selected_total}/${orgAssetSummary.selectable_total}`
                : isPlatformAdmin ? '—' : 'N/A'
            }
            sub={assetPct != null ? `${assetPct}% of catalog` : 'of catalog'}
            tone="indigo"
          />
        </>
      )}
      tabs={(
        <TextTabs
          active={tab}
          onChange={setTab}
          items={[
            { id: 'overview', label: 'Overview' },
            {
              id: 'teams',
              label: (
                <>
                  Teams
                  {teamList.length > 0 && (
                    <span className="ml-1.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-gray-100 px-1.5 text-xs font-semibold text-gray-600">
                      {teamList.length}
                    </span>
                  )}
                </>
              ),
            },
            {
              id: 'members',
              label: (
                <>
                  Members
                  {memberList.length > 0 && (
                    <span className="ml-1.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-gray-100 px-1.5 text-xs font-semibold text-gray-600">
                      {memberList.length}
                    </span>
                  )}
                </>
              ),
            },
            ...(canManageAssets ? [{ id: 'assets' as const, label: 'Asset Access' }] : []),
          ]}
        />
      )}
      notice={pageError ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">{pageError}</div>
      ) : undefined}
    >
      {tab === 'overview' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div className="lg:col-span-2 space-y-5">
              {/* Budget & Spend */}
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-semibold text-gray-900">Budget &amp; Spend</h3>
                  {spendPct != null && (
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                      spendPct > 80 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'
                    }`}>
                      {spendPct}% used
                    </span>
                  )}
                </div>
                {budget && (
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden mb-4">
                    <div
                      className={`h-full rounded-full transition-all ${
                        spendPct! > 90 ? 'bg-red-500' : spendPct! > 80 ? 'bg-amber-500' : 'bg-blue-500'
                      }`}
                      style={{ width: `${spendPct}%` }}
                    />
                  </div>
                )}
                <div className="flex justify-between items-end mb-4">
                  <div>
                    <p className="text-2xl font-bold text-gray-900">
                      ${spend.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5">Current spend</p>
                  </div>
                  {budget && (
                    <div className="text-right">
                      <p className="text-lg font-semibold text-gray-500">
                        ${(budget - spend).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </p>
                      <p className="text-xs text-gray-400">Remaining budget</p>
                    </div>
                  )}
                  {!budget && <span className="text-sm text-gray-400">No budget limit</span>}
                </div>
                <div className="border-t border-gray-100 pt-4 grid grid-cols-2 gap-4">
                  <div>
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
                      <Gauge className="w-3.5 h-3.5" /> RPM Limit
                    </div>
                    {org.rpm_limit != null
                      ? <p className="text-sm font-semibold text-gray-800">{Number(org.rpm_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">req/min</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
                      <TrendingUp className="w-3.5 h-3.5" /> TPM Limit
                    </div>
                    {org.tpm_limit != null
                      ? <p className="text-sm font-semibold text-gray-800">{Number(org.tpm_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">tok/min</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
                      <Gauge className="w-3.5 h-3.5" /> RPH Limit
                    </div>
                    {org.rph_limit != null
                      ? <p className="text-sm font-semibold text-gray-800">{Number(org.rph_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">req/hr</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
                      <Gauge className="w-3.5 h-3.5" /> RPD Limit
                    </div>
                    {org.rpd_limit != null
                      ? <p className="text-sm font-semibold text-gray-800">{Number(org.rpd_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">req/day</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
                      <TrendingUp className="w-3.5 h-3.5" /> TPD Limit
                    </div>
                    {org.tpd_limit != null
                      ? <p className="text-sm font-semibold text-gray-800">{Number(org.tpd_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">tok/day</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                </div>
              </div>

              {/* Teams quick list */}
              <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                  <h3 className="text-sm font-semibold text-gray-900">Teams</h3>
                  {canAddTeam ? (
                    <button
                      onClick={openCreateTeam}
                      className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-blue-600 border border-blue-200 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
                    >
                      <Plus className="w-3 h-3" /> Add Team
                    </button>
                  ) : null}
                </div>
                {teamsLoading ? (
                  <div className="p-6 flex items-center justify-center">
                    <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-blue-600" />
                  </div>
                ) : teamList.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-8">No teams yet.</p>
                ) : (
                  <>
                    <table className="w-full text-sm">
                      <tbody>
                        {teamList.slice(0, 4).map((t: any, i: number) => (
                          <tr
                            key={t.team_id}
                            onClick={() => navigate(`/teams/${t.team_id}`)}
                            className={`hover:bg-gray-50 cursor-pointer ${i < Math.min(teamList.length, 4) - 1 ? 'border-b border-gray-100' : ''}`}
                          >
                            <td className="px-5 py-3">
                              <div className="flex items-center gap-2.5">
                                <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                                  <Users className="w-3.5 h-3.5 text-indigo-600" />
                                </div>
                                <div>
                                  <p className="font-medium text-gray-800 text-xs">{t.team_alias || t.team_id}</p>
                                  <p className="text-[10px] text-gray-400 font-mono">{t.team_id}</p>
                                </div>
                              </div>
                            </td>
                            <td className="px-5 py-3">
                              <span className="text-xs text-gray-600 flex items-center gap-1">
                                <Users className="w-3 h-3 text-gray-400" /> {t.member_count || 0}
                              </span>
                            </td>
                            <td className="px-5 py-3">
                              <SpendBar spend={t.spend || 0} budget={t.max_budget ?? null} />
                            </td>
                            <td className="px-5 py-3 text-right">
                              <button
                                onClick={(e) => { e.stopPropagation(); navigate(`/teams/${t.team_id}`); }}
                                className="text-xs text-blue-600 hover:underline flex items-center gap-1 ml-auto"
                              >
                                Open <ExternalLink className="w-3 h-3" />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {teamList.length > 4 && (
                      <div className="px-5 py-3 border-t border-gray-100 text-center">
                        <button
                          onClick={() => setTab('teams')}
                          className="text-xs text-blue-600 hover:underline"
                        >
                          View all {teamList.length} teams →
                        </button>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>

            {/* Sidebar */}
            <div className="space-y-4">
              {/* Settings */}
              <div className={`bg-white rounded-xl border p-5 transition-colors ${isEditingSettings ? 'border-blue-300 ring-1 ring-blue-200' : 'border-gray-200'}`}>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-gray-900">Settings</h3>
                  {!isEditingSettings && canEditOrganization && (
                    <button
                      onClick={openEditSettings}
                      className="p-1 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600 transition-colors"
                      title="Edit settings"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
                {isEditingSettings ? (
                  <div className="space-y-3">
                    {orgError && (
                      <div className="p-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700">{orgError}</div>
                    )}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Name</label>
                      <input
                        value={form.organization_name}
                        onChange={(e) => setForm({ ...form, organization_name: e.target.value })}
                        className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Max Budget ($)</label>
                      <input
                        type="number"
                        value={form.max_budget}
                        onChange={(e) => setForm({ ...form, max_budget: e.target.value })}
                        className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        placeholder="No limit"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Soft Budget Alert ($)</label>
                      <input
                        type="number"
                        value={form.soft_budget}
                        onChange={(e) => setForm({ ...form, soft_budget: e.target.value })}
                        className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        placeholder="Notify before cap"
                      />
                    </div>
                    <div className="rounded-lg border border-gray-200 bg-gray-50 p-2.5">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <CalendarDays className="h-3.5 w-3.5 text-blue-600" />
                          <span className="text-xs font-medium text-gray-700">Monthly reset</span>
                        </div>
                        <input
                          type="checkbox"
                          checked={!!form.monthly_reset_enabled}
                          onChange={(e) => handleMonthlyResetToggle(e.target.checked)}
                        />
                      </div>
                      {form.monthly_reset_enabled && (
                        <div className="mt-2">
                          <label className="block text-xs font-medium text-gray-600 mb-1">Next reset (UTC)</label>
                          <input
                            type="datetime-local"
                            value={form.budget_reset_at}
                            onChange={(e) => setForm({ ...form, budget_reset_at: e.target.value })}
                            className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          />
                        </div>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">RPM Limit</label>
                        <input
                          type="number"
                          value={form.rpm_limit}
                          onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">TPM Limit</label>
                        <input
                          type="number"
                          value={form.tpm_limit}
                          onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">RPH Limit</label>
                        <input
                          type="number"
                          value={form.rph_limit}
                          onChange={(e) => setForm({ ...form, rph_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">RPD Limit</label>
                        <input
                          type="number"
                          value={form.rpd_limit}
                          onChange={(e) => setForm({ ...form, rpd_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">TPD Limit</label>
                        <input
                          type="number"
                          value={form.tpd_limit}
                          onChange={(e) => setForm({ ...form, tpd_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          placeholder="Unlimited"
                        />
                      </div>
                    </div>
                    <label className="flex items-start gap-2 p-2.5 border border-gray-200 rounded-lg bg-gray-50 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={!!form.audit_content_storage_enabled}
                        onChange={(e) => setForm({ ...form, audit_content_storage_enabled: e.target.checked })}
                        className="mt-0.5"
                      />
                      <span className="text-xs text-gray-700">Store request/response payloads in audit logs</span>
                    </label>
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => { setIsEditingSettings(false); setOrgError(null); }}
                        className="flex-1 px-3 py-1.5 text-xs text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleSaveSettings}
                        disabled={saving}
                        className="flex-1 px-3 py-1.5 text-xs text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
                      >
                        {saving ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2.5 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-gray-500">Audit storage</span>
                      <span className={`shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                        org.audit_content_storage_enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                      }`}>
                        {org.audit_content_storage_enabled ? 'Enabled' : 'Disabled'}
                      </span>
                    </div>
                    {org.max_budget != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">Max budget</span>
                        <span className="text-xs font-semibold text-gray-800">${Number(org.max_budget).toLocaleString()}</span>
                      </div>
                    )}
                    {org.soft_budget != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">Soft budget alert</span>
                        <span className="text-xs font-semibold text-gray-800">${Number(org.soft_budget).toLocaleString()}</span>
                      </div>
                    )}
                    {org.budget_duration && org.budget_reset_at && (
                      <>
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-gray-500">Budget reset</span>
                          <span className="text-xs font-semibold text-gray-800">
                            {org.budget_duration === '1mo' ? 'Monthly' : org.budget_duration}
                          </span>
                        </div>
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs text-gray-500">Next reset (UTC)</span>
                          <span className="text-right text-xs font-semibold text-gray-800">
                            {fmtUtcDateTime(org.budget_reset_at)}
                          </span>
                        </div>
                      </>
                    )}
                    {org.rpm_limit != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">RPM limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(org.rpm_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {org.tpm_limit != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">TPM limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(org.tpm_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {org.rph_limit != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">RPH limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(org.rph_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {org.rpd_limit != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">RPD limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(org.rpd_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {org.tpd_limit != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">TPD limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(org.tpd_limit).toLocaleString()}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Asset Access sidebar */}
              {canManageAssets && (
                <div className="bg-white rounded-xl border border-gray-200 p-5">
                  <h3 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
                    Asset Access <Info className="w-3.5 h-3.5 text-gray-400" />
                  </h3>
                  {orgAssetSummary ? (
                    <>
                      <div className="mb-3">
                        <div className="flex justify-between text-xs mb-1.5">
                          <span className="text-gray-600">{orgAssetSummary.selected_total} models &amp; routes</span>
                          <span className="text-gray-400">of {orgAssetSummary.selectable_total}</span>
                        </div>
                        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-blue-500 rounded-full transition-all"
                            style={{ width: `${assetPct ?? 0}%` }}
                          />
                        </div>
                      </div>
                      <p className="text-[10px] text-gray-400 leading-relaxed">
                        Teams and API keys within this org can only use assets from this allowed set.
                      </p>
                      <button
                        onClick={() => setTab('assets')}
                        className="mt-3 text-xs text-blue-600 hover:underline flex items-center gap-1 font-medium"
                      >
                        Manage assets <ChevronRight className="w-3 h-3" />
                      </button>
                    </>
                  ) : (
                    <p className="text-xs text-gray-400">Loading asset access…</p>
                  )}
                </div>
              )}

              {/* Budget warning for a team */}
              {warningTeam && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
                    <div>
                      <p className="text-xs font-semibold text-amber-800">
                        {warningTeam.team_alias || warningTeam.team_id} at {Math.round(((warningTeam.spend || 0) / warningTeam.max_budget) * 100)}% budget
                      </p>
                      <p className="text-[10px] text-amber-700 mt-0.5">
                        This team is approaching its budget limit.
                      </p>
                      <button
                        onClick={() => navigate(`/teams/${warningTeam.team_id}`)}
                        className="text-[10px] text-amber-700 underline mt-1.5"
                      >
                        View team →
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── TEAMS ── */}
        {tab === 'teams' && (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-gray-50">
              <h3 className="text-sm font-semibold text-gray-900">
                All Teams {teamList.length > 0 && `(${teamList.length})`}
              </h3>
              {canAddTeam ? (
                <button
                  onClick={openCreateTeam}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" /> Add Team
                </button>
              ) : null}
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Name</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Members</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Budget Usage</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">RPM Limit</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {teamsLoading ? (
                  Array.from({ length: 3 }).map((_, i) => (
                    <tr key={i} className="border-b border-gray-100">
                      {[1, 2, 3, 4, 5].map((j) => (
                        <td key={j} className="px-5 py-4">
                          <div className="h-4 bg-gray-100 rounded animate-pulse w-24" />
                        </td>
                      ))}
                    </tr>
                  ))
                ) : teamList.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-5 py-12 text-center text-sm text-gray-400">
                      No teams yet.{' '}
                      {canAddTeam ? <button onClick={openCreateTeam} className="text-blue-600 hover:underline">Add the first one</button> : null}
                    </td>
                  </tr>
                ) : (
                  teamList.map((t: any, i: number) => (
                    <tr
                      key={t.team_id}
                      onClick={() => navigate(`/teams/${t.team_id}`)}
                      className={`hover:bg-blue-50/40 cursor-pointer ${i < teamList.length - 1 ? 'border-b border-gray-100' : ''}`}
                    >
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                            <Users className="w-4 h-4 text-indigo-600" />
                          </div>
                          <div>
                            <p className="font-semibold text-gray-900 text-sm">{t.team_alias || t.team_id}</p>
                            <code className="text-[10px] text-gray-400 font-mono">{t.team_id}</code>
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-3.5 text-sm text-gray-700">{t.member_count || 0}</td>
                      <td className="px-5 py-3.5">
                        <SpendBar spend={t.spend || 0} budget={t.max_budget ?? null} />
                      </td>
                      <td className="px-5 py-3.5">
                        {t.rpm_limit != null
                          ? <span className="text-xs bg-gray-100 px-2 py-0.5 rounded-full text-gray-600">{Number(t.rpm_limit).toLocaleString()}</span>
                          : <span className="text-xs text-gray-400">—</span>}
                      </td>
                      <td className="px-5 py-3.5 text-right" onClick={(e) => e.stopPropagation()}>
                        <button className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors">
                          <MoreHorizontal className="w-4 h-4 text-gray-400" />
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* ── MEMBERS ── */}
        {tab === 'members' && (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-gray-50">
              <h3 className="text-sm font-semibold text-gray-900">
                Organization Members {memberList.length > 0 && `(${memberList.length})`}
              </h3>
              {canManageMembers ? (
                <div className="flex items-center gap-2">
                  <Link
                    to={`/users?invite_org_id=${encodeURIComponent(orgId || '')}`}
                    className="flex items-center gap-1.5 rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-50"
                  >
                    Invite by Email
                  </Link>
                  <button
                    onClick={openAddMember}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                  >
                    <UserPlus className="w-3.5 h-3.5" /> Add Member
                  </button>
                </div>
              ) : null}
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Member</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Org Role</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Team memberships</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {membersLoading ? (
                  Array.from({ length: 3 }).map((_, i) => (
                    <tr key={i} className="border-b border-gray-100">
                      {[1, 2, 3, 4].map((j) => (
                        <td key={j} className="px-5 py-4">
                          <div className="h-4 bg-gray-100 rounded animate-pulse w-24" />
                        </td>
                      ))}
                    </tr>
                  ))
                ) : memberList.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-5 py-12 text-center text-sm text-gray-400">
                      No members yet.
                      {canManageMembers ? (
                        <>
                          {' '}
                          <button onClick={openAddMember} className="text-blue-600 hover:underline">Add the first one</button>
                          {' '}or{' '}
                          <Link to={`/users?invite_org_id=${encodeURIComponent(orgId || '')}`} className="text-blue-600 hover:underline">
                            invite by email
                          </Link>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ) : (
                  memberList.map((m: any, idx: number) => {
                    const role = ROLE_LABELS[m.org_role] ?? { label: m.org_role, cls: 'bg-gray-100 text-gray-700' };
                    return (
                      <tr key={m.membership_id || m.account_id} className={`hover:bg-gray-50 ${idx < memberList.length - 1 ? 'border-b border-gray-100' : ''}`}>
                        <td className="px-5 py-3.5">
                          <div className="flex items-center gap-3">
                            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${AVATAR_COLORS[idx % AVATAR_COLORS.length]}`}>
                              {getInitials(m.email, m.account_id)}
                            </div>
                            <div>
                              <p className="font-medium text-gray-900 text-sm">{m.email || m.account_id}</p>
                              {m.email && <p className="text-xs text-gray-400 font-mono">{m.account_id}</p>}
                            </div>
                          </div>
                        </td>
                        <td className="px-5 py-3.5">
                          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${role.cls}`}>{role.label}</span>
                        </td>
                        <td className="px-5 py-3.5">
                          <span className="text-sm text-gray-600">
                            {m.team_count || 0} {(m.team_count || 0) === 1 ? 'team' : 'teams'}
                          </span>
                          {m.teams?.length > 0 && (
                            <p className="text-[10px] text-gray-400 mt-0.5">{m.teams.slice(0, 3).join(', ')}{m.teams.length > 3 ? ` +${m.teams.length - 3}` : ''}</p>
                          )}
                        </td>
                        <td className="px-5 py-3.5 text-right">
                          {canManageMembers ? (
                            <button
                              onClick={() => handleRemoveMember(m.membership_id)}
                              className="p-1.5 hover:bg-red-50 rounded-lg text-gray-300 hover:text-red-400 transition-colors"
                              title="Remove member"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* ── ASSETS ── */}
        {tab === 'assets' && canManageAssets && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div className="lg:col-span-2 bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold text-gray-900">{isEditingAssets ? 'Edit Asset Access' : 'Allowed Assets'}</h3>
                {isEditingAssets && (
                  <button
                    onClick={() => { setIsEditingAssets(false); setOrgError(null); }}
                    className="text-xs text-gray-500 hover:text-gray-700 transition-colors"
                  >
                    ✕ Cancel
                  </button>
                )}
              </div>
              {isEditingAssets ? (
                <div className="space-y-4">
                  {orgError && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{orgError}</div>
                  )}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <label className={`rounded-lg border px-3 py-2 text-sm cursor-pointer ${form.select_all_current_assets ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                      <div className="flex items-start gap-2">
                        <input
                          type="radio"
                          name="org-detail-asset-strategy"
                          checked={form.select_all_current_assets}
                          onChange={() => setForm((c) => ({ ...c, select_all_current_assets: true, selected_callable_keys: [] }))}
                          disabled={saving}
                          className="mt-0.5"
                        />
                        <span>
                          <span className="block font-medium text-gray-900">Allow all assets, including future additions</span>
                          <span className="block text-xs text-gray-500">Grant every current asset now and automatically include newly added models and route groups.</span>
                        </span>
                      </div>
                    </label>
                    <label className={`rounded-lg border px-3 py-2 text-sm cursor-pointer ${!form.select_all_current_assets ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                      <div className="flex items-start gap-2">
                        <input
                          type="radio"
                          name="org-detail-asset-strategy"
                          checked={!form.select_all_current_assets}
                          onChange={() => setForm((c) => ({ ...c, select_all_current_assets: false }))}
                          disabled={saving}
                          className="mt-0.5"
                        />
                        <span>
                          <span className="block font-medium text-gray-900">Choose a subset</span>
                          <span className="block text-xs text-gray-500">Search and pick only the assets this organization should use.</span>
                        </span>
                      </div>
                    </label>
                  </div>
                  {form.select_all_current_assets ? (
                    <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-3 text-xs text-blue-800">
                      {orgAssetAccess
                        ? `This organization currently has ${orgAssetAccess.summary.selected_total} of ${orgAssetAccess.summary.selectable_total} assets granted. Saving will align it to all current assets and automatically include future additions.`
                        : 'Saving will grant every currently available model and route group to this organization and automatically include future additions.'}
                    </div>
                  ) : (
                    <AssetAccessEditor
                      title="Allowed Assets"
                      description="Choose the models and route groups this organization is allowed to use. Lower scopes can inherit or narrow from this set."
                      mode="grant"
                      targets={orgAssetTargets}
                      selectedKeys={form.selected_callable_keys}
                      onSelectedKeysChange={(selected_callable_keys) => setForm({ ...form, selected_callable_keys })}
                      loading={orgAssetAccessLoading || callableTargetPageLoading}
                      disabled={saving}
                      searchValue={assetSearchInput}
                      onSearchValueChange={setAssetSearchInput}
                      targetTypeFilter={assetTargetType}
                      onTargetTypeFilterChange={(next) => { setAssetTargetType(next); setAssetPageOffset(0); }}
                      pagination={orgAssetPagination}
                      onPageChange={setAssetPageOffset}
                      primaryActionLabel="Allow all assets"
                      onPrimaryAction={() => setForm((c) => ({ ...c, select_all_current_assets: true, selected_callable_keys: [] }))}
                      secondaryActionLabel={form.selected_callable_keys.length > 0 ? 'Clear selection' : undefined}
                      onSecondaryAction={form.selected_callable_keys.length > 0 ? () => setForm((c) => ({ ...c, selected_callable_keys: [] })) : undefined}
                    />
                  )}
                  <div className="flex justify-end gap-3 pt-2 border-t border-gray-100">
                    <button
                      onClick={() => { setIsEditingAssets(false); setOrgError(null); }}
                      className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleSaveAssets}
                      disabled={saving || (isPlatformAdmin && orgAssetAccessLoading)}
                      className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
                    >
                      {saving ? 'Saving…' : 'Save Changes'}
                    </button>
                  </div>
                </div>
              ) : orgAssetTargetsFullLoading ? (
                <div className="py-12 flex items-center justify-center">
                  <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600" />
                </div>
              ) : (orgAssetTargetsFull?.selectable_targets || []).length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-8">No assets configured for this organization.</p>
              ) : (
                <div className="space-y-2">
                  {(orgAssetTargetsFull?.selectable_targets || [])
                    .filter((t: any) => t.selected)
                    .map((t: any) => (
                      <div key={t.callable_key} className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-gray-50 border border-gray-200 hover:bg-blue-50/50 transition-colors">
                        <div className="flex items-center gap-2.5">
                          <Shield className="w-3.5 h-3.5 text-green-500 shrink-0" />
                          <span className="text-sm font-medium text-gray-800">{t.callable_key}</span>
                          <span className={`text-xs px-1.5 py-0.5 rounded ${
                            t.target_type === 'model' ? 'bg-blue-50 text-blue-600' : 'bg-purple-50 text-purple-600'
                          }`}>
                            {t.target_type === 'route_group' ? 'route group' : t.target_type}
                          </span>
                        </div>
                        <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />
                      </div>
                    ))}
                </div>
              )}
              {orgAssetSummary && (
                <p className="text-xs text-gray-400 mt-3 text-center">
                  Showing {orgAssetSummary.selected_total} of {orgAssetSummary.selectable_total} granted assets
                </p>
              )}
            </div>

            {/* Access summary sidebar */}
            <div className="space-y-4">
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3">Access Summary</h4>
                <div className="space-y-2.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Selected</span>
                    <span className="font-medium text-gray-800">{orgAssetSummary?.selected_total ?? '—'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Available</span>
                    <span className="font-medium text-gray-800">{orgAssetSummary?.selectable_total ?? '—'}</span>
                  </div>
                  {assetPct != null && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Coverage</span>
                      <span className="font-medium text-gray-800">{assetPct}%</span>
                    </div>
                  )}
                </div>
              </div>
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
                <p className="text-xs text-blue-800 leading-relaxed">
                  Teams, API keys, and users within this org can only use assets from this allowed set. Child scopes can narrow further but never expand beyond this ceiling.
                </p>
              </div>
              {!isEditingAssets && canManageAssets && (
                <button
                  onClick={openEditAssets}
                  className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium border border-gray-300 text-gray-700 bg-white rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <Pencil className="w-3.5 h-3.5" /> Edit Asset Access
                </button>
              )}
            </div>
          </div>
        )}

      {/* ── Add Member Modal ── */}
      <Modal open={showAddMember} onClose={() => setShowAddMember(false)} title="Add Organization Member">
        <div className="space-y-4">
          {memberError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{memberError}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Search Account</label>
            <UserSearchSelect
              search={memberSearch}
              onSearchChange={setMemberSearch}
              options={(memberCandidates || []) as any[]}
              loading={memberCandidatesLoading}
              selectedAccountId={memberForm.account_id}
              onSelect={(a: any) => setMemberForm({ ...memberForm, account_id: a.account_id })}
              searchPlaceholder="Type full email or exact account ID"
              helperText="For privacy, only exact match (case-insensitive) results are shown."
              emptyText={memberSearch.trim() ? 'No exact account match found.' : 'Start typing a full user email or account ID.'}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Organization Role</label>
            <select
              value={memberForm.role}
              onChange={(e) => setMemberForm({ ...memberForm, role: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="org_member">Member</option>
              <option value="org_admin">Admin</option>
              <option value="org_owner">Owner</option>
              <option value="org_billing">Billing</option>
              <option value="org_auditor">Auditor</option>
            </select>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowAddMember(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button
              onClick={handleAddMember}
              disabled={saving}
              className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {saving ? 'Adding…' : 'Add Member'}
            </button>
          </div>
        </div>
      </Modal>
    </EntityDetailShell>
  );
}
