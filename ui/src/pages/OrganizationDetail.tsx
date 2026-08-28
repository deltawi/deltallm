import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate, Link, useLocation } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import {
  useOrganizationResource,
  type OrganizationLifecycleTransition,
} from '../lib/useOrganizationResource';
import {
  callableTargets,
  organizations,
  type AssetAccessTarget,
  type CallableTargetListItem,
} from '../lib/api';
import {
  assetAccessLoadErrorMessage,
  buildCatalogAccessGroups,
  buildCatalogAssetTargets,
  isScopedAssetAccessFor,
} from '../lib/assetAccess';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession } from '../lib/authorization';
import {
  dateTimeLocalUtcInputToIso,
  defaultMonthlyResetUtcInputValue,
  fmtUtcDateTime,
  toUtcDateTimeLocalInputValue,
} from '../lib/format';
import Modal from '../components/Modal';
import UserSearchSelect from '../components/UserSearchSelect';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import OrganizationTierPanel from '../components/tiers/OrganizationTierPanel';
import {
  DetailMetricCard,
  EntityDetailShell,
  TextTabs,
} from '../components/admin/shells';
import {
  OrganizationLifecycleBadge,
  OrganizationLifecycleNotice,
} from '../components/admin/OrganizationLifecycleStatus';
import {
  ArrowLeft, Building2, Users, DollarSign, Gauge, TrendingUp, Pencil, Plus,
  UserPlus, Trash2, ChevronRight, Shield, CheckCircle2, AlertTriangle,
  MoreHorizontal, ExternalLink, Info, CalendarDays, LockKeyhole,
} from 'lucide-react';

const OrganizationDeletionPanel = lazy(
  () => import('../components/admin/OrganizationDeletionPanel'),
);

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

type TabId = 'overview' | 'teams' | 'members' | 'assets' | 'tiers';

type OrganizationTeamRow = {
  team_id: string;
  team_alias?: string | null;
  member_count?: number | null;
  max_budget?: number | null;
  spend?: number | null;
  rpm_limit?: number | null;
};

type OrganizationMemberRow = {
  membership_id?: string | null;
  account_id: string;
  email?: string | null;
  org_role: string;
  team_count?: number | null;
  teams?: string[];
};

type MemberCandidateOption = {
  account_id: string;
  email?: string | null;
  organization_role?: string | null;
  team_role?: string | null;
  already_member?: boolean;
};

/* ─────────────── sub-components ─────────────── */

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  if (error && typeof error === 'object' && 'message' in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
}

function isBudgetWarningTeam(
  team: OrganizationTeamRow,
): team is OrganizationTeamRow & { max_budget: number; spend: number } {
  return Boolean(team.max_budget && team.spend && team.spend / team.max_budget >= 0.8);
}

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
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const [tab, setTab] = useState<TabId>('overview');

  useEffect(() => {
    const hashTab = location.hash.replace('#', '');
    if (hashTab === 'tiers' && !isPlatformAdmin) {
      setTab('overview');
      return;
    }
    if (hashTab === 'overview' || hashTab === 'teams' || hashTab === 'members' || hashTab === 'assets' || hashTab === 'tiers') {
      setTab(hashTab);
    }
  }, [isPlatformAdmin, location.hash]);

  /* ── data ── */
  const {
    data: org,
    initialError: orgInitialError,
    initialLoading: orgLoading,
    refreshing: orgRefreshing,
    refreshError: orgRefreshError,
    refresh: refetchOrg,
    applyLifecycleTransition,
  } = useOrganizationResource(orgId);
  const handleLifecycleChange = useCallback(async (
    transition: OrganizationLifecycleTransition,
  ) => {
    applyLifecycleTransition(transition);
    await refetchOrg();
  }, [applyLifecycleTransition, refetchOrg]);
  const reconcileOrganizationAfterMutation = useCallback(async () => {
    try {
      await refetchOrg();
    } catch {
      // The mutation remains successful; the resource hook exposes refresh recovery.
    }
  }, [refetchOrg]);
  const servicePolicy = org?.service_policy;
  const hasTier = servicePolicy?.source === 'tier';
  const isTierAuthoritative = Boolean(servicePolicy?.tier_authoritative);
  const { data: orgTeams, loading: teamsLoading } = useApi(
    (signal) => organizations.teams(orgId!, signal), [orgId],
  );
  const { data: orgMembers, loading: membersLoading, refetch: refetchMembers } = useApi(
    (signal) => organizations.members(orgId!, signal), [orgId],
  );
  const { data: orgAssetAccess, error: orgAssetAccessError, loading: orgAssetAccessLoading, refetch: refetchOrgAssetAccess } = useApi(
    (signal) => (isPlatformAdmin && !isTierAuthoritative
      ? organizations.assetAccess(orgId!, { include_targets: false }, signal)
      : Promise.resolve(null)),
    [orgId, isPlatformAdmin, isTierAuthoritative],
  );
  /* full targets: only loaded when assets tab is active */
  const { data: orgAssetTargetsFull, loading: orgAssetTargetsFullLoading } = useApi(
    (signal) => (tab === 'assets' && isPlatformAdmin && !isTierAuthoritative
      ? organizations.assetAccess(orgId!, { include_targets: true }, signal)
      : Promise.resolve(null)),
    [orgId, isPlatformAdmin, isTierAuthoritative, tab],
  );

  useEffect(() => {
    if (isTierAuthoritative && tab === 'assets') setTab('tiers');
  }, [isTierAuthoritative, tab]);
  const currentOrgAssetAccess = orgId && isScopedAssetAccessFor(orgAssetAccess, {
    scopeType: 'organization',
    scopeId: orgId,
    organizationId: orgId,
  })
    ? orgAssetAccess
    : null;
  const currentOrgAssetTargetsFull = orgId && isScopedAssetAccessFor(orgAssetTargetsFull, {
    scopeType: 'organization',
    scopeId: orgId,
    organizationId: orgId,
  })
    ? orgAssetTargetsFull
    : null;

  /* ── edit org modal ── */
  const [isEditingSettings, setIsEditingSettings] = useState(false);
  const [isEditingAssets, setIsEditingAssets] = useState(false);
  const [assetSearchInput, setAssetSearchInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const [assetPageOffset, setAssetPageOffset] = useState(0);
  const [accessGroupPageOffset, setAccessGroupPageOffset] = useState(0);
  const [assetTargetType, setAssetTargetType] = useState<'all' | 'model' | 'route_group'>('all');
  const assetPageSize = 50;
  const accessGroupPageSize = 50;
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
  const [pageError, setPageError] = useState<string | null>(
    typeof location.state === 'object' && location.state && 'pageWarning' in location.state
      ? String((location.state as { pageWarning?: string }).pageWarning || '') || null
      : null,
  );
  const [orgError, setOrgError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      setAssetSearch(assetSearchInput);
      setAssetPageOffset(0);
      setAccessGroupPageOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [assetSearchInput]);

  useEffect(() => {
    if (!isEditingAssets || !currentOrgAssetAccess) return;
    setForm((c) => ({
      ...c,
      select_all_current_assets: !!currentOrgAssetAccess.auto_follow_catalog,
      selected_callable_keys: currentOrgAssetAccess.selected_callable_keys || [],
      selected_access_group_keys: currentOrgAssetAccess.selected_access_group_keys || [],
    }));
  }, [isEditingAssets, currentOrgAssetAccess]);

  const { data: callableTargetPage, error: callableTargetPageError, loading: callableTargetPageLoading } = useApi(
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
  const { data: callableTargetAccessGroups, error: accessGroupError, loading: accessGroupLoading } = useApi(
    () => (
      isPlatformAdmin && isEditingAssets && !form.select_all_current_assets
        ? callableTargets.listAccessGroups({
            search: assetSearch || undefined,
            include_members: false,
            limit: accessGroupPageSize,
            offset: accessGroupPageOffset,
          })
        : Promise.resolve({ data: [], pagination: { total: 0, limit: accessGroupPageSize, offset: 0, has_more: false } })
    ),
    [isPlatformAdmin, isEditingAssets, form.select_all_current_assets, assetSearch, accessGroupPageOffset],
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
      selected_callable_keys: currentOrgAssetAccess?.selected_callable_keys || [],
      selected_access_group_keys: currentOrgAssetAccess?.selected_access_group_keys || [],
    }));
    setAssetSearchInput('');
    setAssetSearch('');
    setAssetPageOffset(0);
    setAccessGroupPageOffset(0);
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
        max_budget: form.max_budget ? Number(form.max_budget) : null,
        soft_budget: form.soft_budget ? Number(form.soft_budget) : null,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : null,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : null,
        rph_limit: form.rph_limit ? Number(form.rph_limit) : null,
        rpd_limit: form.rpd_limit ? Number(form.rpd_limit) : null,
        tpd_limit: form.tpd_limit ? Number(form.tpd_limit) : null,
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
      await reconcileOrganizationAfterMutation();
    } catch (err: unknown) {
      setOrgError(getErrorMessage(err, 'Failed to update organization'));
    } finally {
      setSaving(false);
    }
  };

  const handleClearLegacyModelLimits = async () => {
    if (!orgId || saving) return;
    const confirmed = confirm(
      'Clear the legacy organization per-model RPM and TPM maps? Confirm the equivalent limits are already configured on the tier. This can increase allowed traffic if they are not.',
    );
    if (!confirmed) return;
    setSaving(true);
    setPageError(null);
    try {
      await organizations.update(orgId, {
        model_rpm_limit: null,
        model_tpm_limit: null,
      });
      await reconcileOrganizationAfterMutation();
    } catch (err: unknown) {
      setPageError(getErrorMessage(err, 'Failed to clear legacy per-model limits.'));
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAssets = async () => {
    if (assetAccessLoadError) {
      setOrgError(assetAccessLoadError);
      return;
    }
    if (orgAssetAccessPending || assetAccessLoading) {
      setOrgError('Wait for asset access options to finish loading before saving.');
      return;
    }
    setSaving(true);
    setOrgError(null);
    try {
      await organizations.updateAssetAccess(orgId!, {
        selected_callable_keys: form.select_all_current_assets ? [] : form.selected_callable_keys,
        selected_access_group_keys: form.select_all_current_assets ? [] : form.selected_access_group_keys,
        select_all_selectable: form.select_all_current_assets,
      });
      refetchOrgAssetAccess();
      setIsEditingAssets(false);
      await reconcileOrganizationAfterMutation();
    } catch (err: unknown) {
      setOrgError(getErrorMessage(err, 'Failed to update asset access'));
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
    (signal) => showAddMember
      ? organizations.memberCandidates(orgId!, { search: memberSearch, limit: 50 }, signal)
      : Promise.resolve([]),
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
    } catch (err: unknown) {
      setMemberError(getErrorMessage(err, 'Failed to add member'));
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
    } catch (err: unknown) {
      setPageError(getErrorMessage(err, 'Failed to remove member'));
    } finally {
      setSaving(false);
    }
  };

  /* ── derived ── */
  const teamList = (orgTeams || []) as OrganizationTeamRow[];
  const memberList = (orgMembers || []) as OrganizationMemberRow[];
  const memberCandidateList = (memberCandidates || []) as MemberCandidateOption[];
  const organizationIsActive = org?.lifecycle_state === 'active';
  const canEditOrganization = Boolean(org?.capabilities?.edit);
  const canAddTeam = Boolean(org?.capabilities?.add_team);
  const canManageMembers = Boolean(org?.capabilities?.manage_members);
  const canManageAssets = Boolean(org?.capabilities?.manage_assets) && !isTierAuthoritative;
  const canManageServicePolicy = Boolean(org?.capabilities?.manage_service_policy);
  const spend = org?.spend || 0;
  const budget = org?.max_budget ?? null;
  const spendPct = budget ? Math.min(100, Math.round((spend / budget) * 100)) : null;
  const orgAssetSummary = currentOrgAssetAccess?.summary;
  const orgAccessibleTargets: AssetAccessTarget[] = currentOrgAssetTargetsFull?.effective_targets ?? [];
  const assetPct = orgAssetSummary && orgAssetSummary.selectable_total > 0
    ? Math.round((orgAssetSummary.effective_total / orgAssetSummary.selectable_total) * 100)
    : null;

  const orgAssetTargets = buildCatalogAssetTargets(
    (callableTargetPage?.data || []) as CallableTargetListItem[],
    form.selected_callable_keys,
    currentOrgAssetAccess?.selected_callable_keys || [],
  );
  const orgAssetAccessGroups = buildCatalogAccessGroups(
    callableTargetAccessGroups?.data || [],
    form.selected_access_group_keys,
    currentOrgAssetAccess?.selected_access_group_keys || [],
  );
  const orgAssetPagination = callableTargetPage?.pagination;
  const accessGroupPagination = callableTargetAccessGroups?.pagination;
  const orgAssetAccessPending = isEditingAssets && isPlatformAdmin && (orgAssetAccessLoading || !currentOrgAssetAccess);
  const assetAccessLoading = !form.select_all_current_assets && (callableTargetPageLoading || accessGroupLoading);
  const assetAccessLoadError = isEditingAssets && isPlatformAdmin
    ? assetAccessLoadErrorMessage(
        orgAssetAccessError || (!form.select_all_current_assets ? callableTargetPageError || accessGroupError : null),
      )
    : null;

  useEffect(() => {
    if (!org || organizationIsActive) return;
    setIsEditingSettings(false);
    setIsEditingAssets(false);
    setShowAddMember(false);
    if (tab === 'assets') setTab('overview');
  }, [org, organizationIsActive, tab]);

  /* teams over 80% of budget = "warning" for alert card */
  const warningTeam = teamList.find(isBudgetWarningTeam);

  /* ── loading / not found ── */
  if (orgLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-brand-primary" />
      </div>
    );
  }

  if (!org) {
    return (
      <div className="p-6">
        <p className="text-gray-500">
          {orgInitialError
            ? getErrorMessage(orgInitialError, 'Unable to load organization.')
            : 'Organization not found.'}
        </p>
        {orgInitialError ? (
          <button
            type="button"
            className="mt-3 text-sm font-medium text-brand-primary-ink"
            onClick={() => void refetchOrg().catch(() => undefined)}
          >
            Try again
          </button>
        ) : null}
        <Link to="/organizations" className="text-brand-primary-ink text-sm mt-2 inline-block">Back to Organizations</Link>
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
        <OrganizationLifecycleBadge state={org.lifecycle_state} />
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
            sub={`${teamList.filter((t) => (t.spend || 0) > 0).length} active`}
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
            label="Service policy"
            value={hasTier
              ? servicePolicy?.primary_tier?.tier_name || servicePolicy?.primary_tier?.tier_key || 'Tier managed'
              : 'Legacy'}
            sub={hasTier
              ? isTierAuthoritative
                ? servicePolicy?.primary_tier?.follows_active_version ? 'Follows active version' : 'Pinned tier version'
                : `${servicePolicy?.tier_policy_mode || 'rollout'} · legacy runtime`
              : 'Direct asset access'}
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
            ...(isPlatformAdmin ? [{ id: 'tiers' as const, label: 'Service Policy' }] : []),
            ...(canManageAssets ? [{ id: 'assets' as const, label: 'Asset Access' }] : []),
          ]}
        />
      )}
      notice={!organizationIsActive || pageError || orgRefreshError ? (
        <div className="space-y-3">
          <OrganizationLifecycleNotice
            state={org.lifecycle_state}
            deletionNotBeforeAt={org.deletion_not_before_at}
          />
          {pageError ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">{pageError}</div>
          ) : null}
          {orgRefreshError ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              <p>
                The last action succeeded, but the latest organization state could not be loaded.
                Existing data is being kept until refresh succeeds.
              </p>
              <button
                type="button"
                className="mt-2 font-semibold text-brand-primary-ink disabled:opacity-50"
                disabled={orgRefreshing}
                onClick={() => void refetchOrg().catch(() => undefined)}
              >
                {orgRefreshing ? 'Refreshing…' : 'Retry refresh'}
              </button>
            </div>
          ) : null}
        </div>
      ) : undefined}
    >
      {tab === 'overview' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div className="lg:col-span-2 space-y-5">
              {/* Service policy */}
              <div className={`rounded-xl border p-5 ${
                hasTier ? 'border-blue-200 bg-blue-50/50' : 'border-amber-200 bg-amber-50/50'
              }`}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <Shield className={`mt-0.5 h-5 w-5 ${hasTier ? 'text-blue-700' : 'text-amber-700'}`} />
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-semibold text-gray-900">Service Policy</h3>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                          hasTier ? 'bg-blue-100 text-blue-700' : 'bg-amber-100 text-amber-700'
                        }`}>
                          {hasTier ? (isTierAuthoritative ? 'Tier managed' : 'Tier staged') : 'Legacy'}
                        </span>
                      </div>
                      {hasTier ? (
                        <>
                          <p className="mt-1 text-base font-semibold text-gray-900">
                            {servicePolicy?.primary_tier?.tier_name || servicePolicy?.primary_tier?.tier_key || 'Primary tier'}
                            {servicePolicy?.primary_tier?.tier_version_number != null && (
                              <span className="ml-2 text-xs font-medium text-gray-500">v{servicePolicy.primary_tier.tier_version_number}</span>
                            )}
                          </p>
                          <p className="mt-1 text-xs leading-relaxed text-gray-600">
                            {isTierAuthoritative
                              ? 'The tier controls model access, per-model limits, pricing, and capacity.'
                              : `The tier is staged in ${servicePolicy?.tier_policy_mode || 'rollout'} mode. Legacy Asset Access remains authoritative until tier enforcement.`}
                            {servicePolicy?.primary_tier?.follows_active_version && ' The assignment follows each newly published active version.'}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-2 text-[10px]">
                            {servicePolicy?.overlay_count ? (
                              <span className="rounded bg-purple-100 px-2 py-1 font-medium text-purple-700">
                                {servicePolicy.overlay_count} active overlay{servicePolicy.overlay_count === 1 ? '' : 's'}
                              </span>
                            ) : null}
                            {servicePolicy?.hard_caps_configured && (
                              <span className="rounded bg-amber-100 px-2 py-1 font-medium text-amber-700">Organization hard caps also apply</span>
                            )}
                          </div>
                          {servicePolicy?.legacy_model_limits_configured && (
                            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-relaxed text-red-800">
                              <p className="font-semibold">Legacy per-model RPM/TPM caps still apply</p>
                              <p className="mt-1">Move equivalent limits into the tier, then clear the legacy maps so the tier is the only per-model policy source.</p>
                              {canManageServicePolicy && (
                                <button
                                  type="button"
                                  onClick={handleClearLegacyModelLimits}
                                  disabled={saving}
                                  className="mt-2 rounded-md border border-red-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-red-700 hover:bg-red-100 disabled:opacity-50"
                                >
                                  Clear legacy model caps
                                </button>
                              )}
                            </div>
                          )}
                        </>
                      ) : (
                        <p className="mt-1 text-xs leading-relaxed text-gray-700">
                          This existing organization still uses direct asset access.{' '}
                          {isPlatformAdmin
                            ? 'Assign a primary tier to migrate model access and per-model controls to the tier policy.'
                            : 'A platform administrator must assign a primary tier to migrate it to tier-managed policy.'}
                        </p>
                      )}
                    </div>
                  </div>
                  {isPlatformAdmin ? (
                    <button
                      type="button"
                      onClick={() => setTab('tiers')}
                      className="shrink-0 rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-50"
                    >
                      {canManageServicePolicy
                        ? hasTier ? 'Manage policy' : 'Assign tier'
                        : 'View policy'}
                    </button>
                  ) : (
                    <span className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-medium text-gray-600">
                      <LockKeyhole className="h-3.5 w-3.5" />
                      Managed by platform administrator
                    </span>
                  )}
                </div>
              </div>

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
                <div className="border-t border-gray-100 pt-4">
                  <div className="mb-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Organization-wide hard caps</p>
                    <p className="mt-0.5 text-[10px] text-gray-400">Global ceilings applied in addition to the service tier.</p>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
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
              </div>

              {/* Teams quick list */}
              <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                  <h3 className="text-sm font-semibold text-gray-900">Teams</h3>
                  {canAddTeam ? (
                    <button
                      onClick={openCreateTeam}
                      className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-brand-primary-ink border border-blue-200 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
                    >
                      <Plus className="w-3 h-3" /> Add Team
                    </button>
                  ) : null}
                </div>
                {teamsLoading ? (
                  <div className="p-6 flex items-center justify-center">
                    <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-brand-primary" />
                  </div>
                ) : teamList.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-8">No teams yet.</p>
                ) : (
                  <>
                    <table className="w-full text-sm">
                      <tbody>
                        {teamList.slice(0, 4).map((t, i) => (
                          <tr
                            key={t.team_id}
                            onClick={() => navigate(`/teams/${t.team_id}`)}
                            className={`hover:bg-gray-50 cursor-pointer ${i < Math.min(teamList.length, 4) - 1 ? 'border-b border-gray-100' : ''}`}
                          >
                            <td className="px-5 py-3">
                              <div className="flex items-center gap-2.5">
                                <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                                  <Users className="w-3.5 h-3.5 text-brand-secondary-ink" />
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
                                className="text-xs text-brand-primary-ink hover:underline flex items-center gap-1 ml-auto"
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
                          className="text-xs text-brand-primary-ink hover:underline"
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
              <div className={`bg-white rounded-xl border p-5 transition-colors ${isEditingSettings ? 'border-blue-300 ring-1 ring-brand-primary/20' : 'border-gray-200'}`}>
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
                        className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Max Budget ($)</label>
                      <input
                        type="number"
                        value={form.max_budget}
                        onChange={(e) => setForm({ ...form, max_budget: e.target.value })}
                        className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                        placeholder="No limit"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Soft Budget Alert ($)</label>
                      <input
                        type="number"
                        value={form.soft_budget}
                        onChange={(e) => setForm({ ...form, soft_budget: e.target.value })}
                        className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                        placeholder="Notify before cap"
                      />
                    </div>
                    <div className="rounded-lg border border-gray-200 bg-gray-50 p-2.5">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <CalendarDays className="h-3.5 w-3.5 text-brand-primary-ink" />
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
                            className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                          />
                        </div>
                      )}
                    </div>
                    <div className="rounded-lg border border-purple-100 bg-purple-50 p-2.5">
                      <p className="text-xs font-semibold text-purple-900">Organization-wide hard caps</p>
                      <p className="mt-0.5 text-[10px] leading-relaxed text-purple-700">Optional global ceilings in addition to tier limits. Blank removes the organization cap.</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Org RPM</label>
                        <input
                          type="number"
                          value={form.rpm_limit}
                          onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Org TPM</label>
                        <input
                          type="number"
                          value={form.tpm_limit}
                          onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Org RPH</label>
                        <input
                          type="number"
                          value={form.rph_limit}
                          onChange={(e) => setForm({ ...form, rph_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Org RPD</label>
                        <input
                          type="number"
                          value={form.rpd_limit}
                          onChange={(e) => setForm({ ...form, rpd_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Org TPD</label>
                        <input
                          type="number"
                          value={form.tpd_limit}
                          onChange={(e) => setForm({ ...form, tpd_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
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
                        className="flex-1 px-3 py-1.5 text-xs text-brand-on-primary bg-brand-primary rounded-lg hover:bg-brand-primary-hover transition-colors disabled:opacity-50"
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
                          <span className="text-gray-600">{orgAssetSummary.effective_total} models &amp; routes</span>
                          <span className="text-gray-400">of {orgAssetSummary.selectable_total}</span>
                        </div>
                        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-blue-500 rounded-full transition-all"
                            style={{ width: `${assetPct ?? 0}%` }}
                          />
                        </div>
                      </div>
                      <p className="text-[10px] text-gray-400 leading-relaxed mb-1">
                        Direct targets: {orgAssetSummary.selected_total} · Access groups: {orgAssetSummary.selected_access_group_total ?? 0}
                      </p>
                      <p className="text-[10px] text-gray-400 leading-relaxed">
                        Teams and API keys within this org can only use assets from this allowed set.
                      </p>
                      <button
                        onClick={() => setTab('assets')}
                        className="mt-3 text-xs text-brand-primary-ink hover:underline flex items-center gap-1 font-medium"
                      >
                        Manage assets <ChevronRight className="w-3 h-3" />
                      </button>
                    </>
                  ) : (
                    <p className="text-xs text-gray-400">Loading asset access…</p>
                  )}
                </div>
              )}

              {isPlatformAdmin && orgId && (
                <Suspense fallback={<div className="rounded-xl border border-gray-200 bg-white p-4 text-xs text-gray-500">Loading deletion controls…</div>}>
                  <OrganizationDeletionPanel
                    organizationId={orgId}
                    organizationName={orgName}
                    onLifecycleChange={handleLifecycleChange}
                  />
                </Suspense>
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
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-brand-primary text-brand-on-primary rounded-lg hover:bg-brand-primary-hover transition-colors"
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
                      {canAddTeam ? <button onClick={openCreateTeam} className="text-brand-primary-ink hover:underline">Add the first one</button> : null}
                    </td>
                  </tr>
                ) : (
                  teamList.map((t, i) => (
                    <tr
                      key={t.team_id}
                      onClick={() => navigate(`/teams/${t.team_id}`)}
                      className={`hover:bg-blue-50/40 cursor-pointer ${i < teamList.length - 1 ? 'border-b border-gray-100' : ''}`}
                    >
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                            <Users className="w-4 h-4 text-brand-secondary-ink" />
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
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-brand-primary text-brand-on-primary rounded-lg hover:bg-brand-primary-hover transition-colors"
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
                          <button onClick={openAddMember} className="text-brand-primary-ink hover:underline">Add the first one</button>
                          {' '}or{' '}
                          <Link to={`/users?invite_org_id=${encodeURIComponent(orgId || '')}`} className="text-brand-primary-ink hover:underline">
                            invite by email
                          </Link>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ) : (
                  memberList.map((m, idx) => {
                    const role = ROLE_LABELS[m.org_role] ?? { label: m.org_role, cls: 'bg-gray-100 text-gray-700' };
                    const memberTeams = m.teams || [];
                    const membershipId = m.membership_id || null;
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
                          {memberTeams.length > 0 && (
                            <p className="text-[10px] text-gray-400 mt-0.5">{memberTeams.slice(0, 3).join(', ')}{memberTeams.length > 3 ? ` +${memberTeams.length - 3}` : ''}</p>
                          )}
                        </td>
                        <td className="px-5 py-3.5 text-right">
                          {canManageMembers && membershipId ? (
                            <button
                              onClick={() => handleRemoveMember(membershipId)}
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
        {tab === 'tiers' && isPlatformAdmin && (
          <OrganizationTierPanel
            organizationId={orgId!}
            canManage={canManageServicePolicy}
            readOnlyReason={organizationIsActive
              ? undefined
              : 'Tier assignments cannot be changed while organization access is disabled.'}
          />
        )}

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
                  {(orgError || assetAccessLoadError) && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{orgError || assetAccessLoadError}</div>
                  )}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <label className={`rounded-lg border px-3 py-2 text-sm cursor-pointer ${form.select_all_current_assets ? 'border-brand-primary bg-blue-50' : 'border-gray-200 bg-white'}`}>
                      <div className="flex items-start gap-2">
                        <input
                          type="radio"
                          name="org-detail-asset-strategy"
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
                          <span className="block text-xs text-gray-500">Grant every current asset now and automatically include newly added models and route groups.</span>
                        </span>
                      </div>
                    </label>
                    <label className={`rounded-lg border px-3 py-2 text-sm cursor-pointer ${!form.select_all_current_assets ? 'border-brand-primary bg-blue-50' : 'border-gray-200 bg-white'}`}>
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
                      {currentOrgAssetAccess
                        ? `This organization currently has ${currentOrgAssetAccess.summary.effective_total} of ${currentOrgAssetAccess.summary.selectable_total} assets granted. Saving will align it to all current assets and automatically include future additions.`
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
                      accessGroups={orgAssetAccessGroups}
                      selectedAccessGroupKeys={form.selected_access_group_keys}
                      onSelectedAccessGroupKeysChange={(selected_access_group_keys) => setForm({ ...form, selected_access_group_keys })}
                      targetsLoading={orgAssetAccessPending || callableTargetPageLoading}
                      accessGroupsLoading={orgAssetAccessPending || accessGroupLoading}
                      disabled={saving || orgAssetAccessPending || Boolean(assetAccessLoadError)}
                      searchValue={assetSearchInput}
                      onSearchValueChange={setAssetSearchInput}
                      targetTypeFilter={assetTargetType}
                      onTargetTypeFilterChange={(next) => { setAssetTargetType(next); setAssetPageOffset(0); }}
                      pagination={orgAssetPagination}
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
                  <div className="flex justify-end gap-3 pt-2 border-t border-gray-100">
                    <button
                      onClick={() => { setIsEditingAssets(false); setOrgError(null); }}
                      className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleSaveAssets}
                      disabled={saving || orgAssetAccessPending || assetAccessLoading || Boolean(assetAccessLoadError)}
                      className="px-4 py-2 text-sm bg-brand-primary text-brand-on-primary rounded-lg hover:bg-brand-primary-hover transition-colors disabled:opacity-50"
                    >
                      {saving ? 'Saving…' : 'Save Changes'}
                    </button>
                  </div>
                </div>
              ) : !currentOrgAssetTargetsFull && orgAssetTargetsFullLoading ? (
                <div className="py-12 flex items-center justify-center">
                  <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-brand-primary" />
                </div>
              ) : orgAccessibleTargets.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-8">No assets granted for this organization.</p>
              ) : (
                <div className="space-y-2">
                  {orgAccessibleTargets.map((t) => (
                    <div key={t.callable_key} className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-gray-50 border border-gray-200 hover:bg-blue-50/50 transition-colors">
                      <div className="flex items-center gap-2.5">
                        <Shield className="w-3.5 h-3.5 text-green-500 shrink-0" />
                        <span className="text-sm font-medium text-gray-800">{t.callable_key}</span>
                        <span className={`text-xs px-1.5 py-0.5 rounded ${
                          t.target_type === 'model' ? 'bg-blue-50 text-brand-primary-ink' : 'bg-purple-50 text-purple-600'
                        }`}>
                          {t.target_type === 'route_group' ? 'route group' : t.target_type}
                        </span>
                        {Array.isArray(t.via_access_groups) && t.via_access_groups.length > 0 && (
                          <span className="text-xs px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">
                            via {t.via_access_groups.join(', ')}
                          </span>
                        )}
                      </div>
                      <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />
                    </div>
                  ))}
                </div>
              )}
              {orgAssetSummary && (
                <p className="text-xs text-gray-400 mt-3 text-center">
                  Showing {orgAssetSummary.effective_total} of {orgAssetSummary.selectable_total} granted assets
                </p>
              )}
            </div>

            {/* Access summary sidebar */}
            <div className="space-y-4">
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3">Access Summary</h4>
                <div className="space-y-2.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Accessible</span>
                    <span className="font-medium text-gray-800">{orgAssetSummary?.effective_total ?? '—'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Direct targets</span>
                    <span className="font-medium text-gray-800">{orgAssetSummary?.selected_total ?? '—'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Access groups</span>
                    <span className="font-medium text-gray-800">{orgAssetSummary?.selected_access_group_total ?? '—'}</span>
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
              options={memberCandidateList}
              loading={memberCandidatesLoading}
              selectedAccountId={memberForm.account_id}
              onSelect={(a) => setMemberForm({ ...memberForm, account_id: a.account_id })}
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
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary"
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
              className="px-4 py-2 text-sm bg-brand-primary text-brand-on-primary rounded-lg hover:bg-brand-primary-hover transition-colors disabled:opacity-50"
            >
              {saving ? 'Adding…' : 'Add Member'}
            </button>
          </div>
        </div>
      </Modal>
    </EntityDetailShell>
  );
}
