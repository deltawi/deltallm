import { useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate, Link, useLocation } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { organizations, teams } from '../lib/api';
import { buildParentScopedAssetTargets, buildScopedSelectableTargets } from '../lib/assetAccess';
import Modal from '../components/Modal';
import UserSearchSelect from '../components/UserSearchSelect';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import {
  DetailMetricCard,
  EntityDetailShell,
  TextTabs,
} from '../components/admin/shells';
import {
  ArrowLeft, Users, DollarSign, Gauge, Shield, Pencil, UserPlus, Trash2,
  Building2, AlertOctagon, CheckCircle2, TrendingUp,
  Lock, Unlock, Info, Key, ToggleLeft, ToggleRight,
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

function getInitials(userId: string, email?: string | null): string {
  if (email) {
    const local = email.split('@')[0];
    const parts = local.split(/[._-]/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return local.slice(0, 2).toUpperCase();
  }
  return userId.slice(0, 2).toUpperCase();
}

const ROLE_BADGES: Record<string, { label: string; cls: string }> = {
  team_admin:     { label: 'Admin',     cls: 'bg-indigo-100 text-indigo-700' },
  team_developer: { label: 'Developer', cls: 'bg-blue-100 text-blue-700' },
  team_viewer:    { label: 'Viewer',    cls: 'bg-gray-100 text-gray-600' },
};

type TabId = 'overview' | 'members' | 'assets';

/* ─────────────── subcomponents ─────────────── */

/* ─────────────── page ─────────────── */

export default function TeamDetail() {
  const { teamId } = useParams<{ teamId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { session, authMode } = useAuth();
  const permissions = useMemo(() => new Set(session?.effective_permissions || []), [session?.effective_permissions]);
  const isPlatformAdmin = authMode === 'master_key' || session?.role === 'platform_admin';
  const canEditTeam = isPlatformAdmin || permissions.has('team.update');
  const [tab, setTab] = useState<TabId>('overview');

  /* ── data ── */
  const { data: team, loading: teamLoading, refetch: refetchTeam } = useApi(
    () => teams.get(teamId!),
    [teamId],
  );
  const { data: members, loading: membersLoading, refetch: refetchMembers } = useApi(
    () => teams.members(teamId!),
    [teamId],
  );
  const { data: teamAssetAccess, refetch: refetchTeamAssetAccess } = useApi(
    () => teams.assetAccess(teamId!, { include_targets: false }),
    [teamId],
  );
  /* targets loaded lazily on assets tab */
  const { data: teamAssetTargets, loading: teamAssetTargetsLoading } = useApi(
    () => tab === 'assets' ? teams.assetAccess(teamId!, { include_targets: true }) : Promise.resolve(null),
    [teamId, tab],
  );
  /* org info for breadcrumb + info card */
  const { data: orgData } = useApi(
    () => team?.organization_id ? organizations.get(team.organization_id) : Promise.resolve(null),
    [team?.organization_id],
  );

  /* ── inline editing ── */
  const [isEditingSettings, setIsEditingSettings] = useState(false);
  const [isEditingAssets, setIsEditingAssets] = useState(false);
  const [form, setForm] = useState({
    team_alias: '',
    organization_id: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    rph_limit: '',
    rpd_limit: '',
    tpd_limit: '',
    asset_access_mode: 'inherit' as 'inherit' | 'restrict',
    selected_callable_keys: [] as string[],
  });
  const [saving, setSaving] = useState(false);
  const [teamError, setTeamError] = useState<string | null>(
    typeof location.state === 'object' && location.state && 'pageWarning' in location.state
      ? String((location.state as { pageWarning?: string }).pageWarning || '') || null
      : null,
  );

  const selectedOrganizationId = form.organization_id.trim();
  const usesParentPreview = selectedOrganizationId !== (team?.organization_id || '');

  const { data: teamAssetAccessTargets, loading: teamAssetAccessTargetsLoading } = useApi(
    () => (isEditingAssets && !usesParentPreview && form.asset_access_mode === 'restrict'
      ? teams.assetAccess(teamId!, { include_targets: true })
      : Promise.resolve(null)),
    [isEditingAssets, teamId, usesParentPreview, form.asset_access_mode],
  );
  const { data: parentOrgAssetVisibility, loading: parentOrgAssetVisibilityLoading } = useApi(
    () => (isEditingAssets && usesParentPreview && form.asset_access_mode === 'restrict' && selectedOrganizationId
      ? organizations.assetVisibility(selectedOrganizationId)
      : Promise.resolve(null)),
    [isEditingAssets, selectedOrganizationId, usesParentPreview, form.asset_access_mode],
  );

  useEffect(() => {
    if (!isEditingAssets || !teamAssetAccess) return;
    setForm((c) => ({
      ...c,
      asset_access_mode: teamAssetAccess.mode === 'restrict' ? 'restrict' : 'inherit',
      selected_callable_keys: teamAssetAccess.selected_callable_keys || [],
    }));
  }, [isEditingAssets, teamAssetAccess]);

  const openEditSettings = () => {
    if (!team) return;
    setTeamError(null);
    setForm((c) => ({
      ...c,
      team_alias: team.team_alias || '',
      organization_id: team.organization_id || '',
      max_budget: team.max_budget != null ? String(team.max_budget) : '',
      rpm_limit: team.rpm_limit != null ? String(team.rpm_limit) : '',
      tpm_limit: team.tpm_limit != null ? String(team.tpm_limit) : '',
      rph_limit: team.rph_limit != null ? String(team.rph_limit) : '',
      rpd_limit: team.rpd_limit != null ? String(team.rpd_limit) : '',
      tpd_limit: team.tpd_limit != null ? String(team.tpd_limit) : '',
    }));
    setIsEditingSettings(true);
  };

  const openEditAssets = () => {
    if (!team) return;
    setTeamError(null);
    setForm((c) => ({
      ...c,
      organization_id: team.organization_id || '',
      asset_access_mode: teamAssetAccess?.mode === 'restrict' ? 'restrict' : 'inherit',
      selected_callable_keys: teamAssetAccess?.selected_callable_keys || [],
    }));
    setIsEditingAssets(true);
  };

  const assetTargets = usesParentPreview
    ? buildParentScopedAssetTargets(parentOrgAssetVisibility?.callable_targets?.items || [], form.selected_callable_keys, form.asset_access_mode)
    : buildScopedSelectableTargets(teamAssetAccessTargets?.selectable_targets || [], form.selected_callable_keys, form.asset_access_mode);
  const assetAccessLoading = form.asset_access_mode !== 'restrict' ? false
    : usesParentPreview ? parentOrgAssetVisibilityLoading
    : teamAssetAccessTargetsLoading;

  const handleSaveSettings = async () => {
    setSaving(true);
    setTeamError(null);
    try {
      await teams.update(teamId!, {
        team_alias: form.team_alias || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        rph_limit: form.rph_limit ? Number(form.rph_limit) : undefined,
        rpd_limit: form.rpd_limit ? Number(form.rpd_limit) : undefined,
        tpd_limit: form.tpd_limit ? Number(form.tpd_limit) : undefined,
      });
      setIsEditingSettings(false);
      refetchTeam();
    } catch (err: any) {
      setTeamError(err?.message || 'Failed to update team');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAssets = async () => {
    setSaving(true);
    setTeamError(null);
    try {
      await teams.updateAssetAccess(teamId!, {
        mode: form.asset_access_mode,
        selected_callable_keys: form.asset_access_mode === 'restrict' ? form.selected_callable_keys : [],
      });
      setIsEditingAssets(false);
      refetchTeam();
      refetchTeamAssetAccess();
    } catch (err: any) {
      setTeamError(err?.message || 'Failed to update asset access');
    } finally {
      setSaving(false);
    }
  };

  /* ── add member modal ── */
  const [showAddMember, setShowAddMember] = useState(false);
  const [memberSearch, setMemberSearch] = useState('');
  const [memberForm, setMemberForm] = useState({ user_id: '', user_email: '', user_role: 'team_viewer' });
  const { data: memberCandidates, loading: memberCandidatesLoading } = useApi(
    () => showAddMember ? teams.memberCandidates(teamId!, { search: memberSearch, limit: 50 }) : Promise.resolve([]),
    [teamId, showAddMember, memberSearch],
  );

  const handleAddMember = async () => {
    if (!memberForm.user_id.trim()) return;
    setSaving(true);
    try {
      await teams.addMember(teamId!, {
        user_id: memberForm.user_id.trim(),
        user_email: memberForm.user_email.trim() || undefined,
        user_role: memberForm.user_role,
      });
      setShowAddMember(false);
      setMemberForm({ user_id: '', user_email: '', user_role: 'team_viewer' });
      refetchMembers();
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveMember = async (userId: string) => {
    if (!confirm('Remove this member from the team?')) return;
    await teams.removeMember(teamId!, userId);
    refetchMembers();
  };

  /* ── self-service policy editing ── */
  const [isEditingPolicy, setIsEditingPolicy] = useState(false);
  const [policyForm, setPolicyForm] = useState({
    self_service_keys_enabled: false,
    self_service_max_keys_per_user: '',
    self_service_budget_ceiling: '',
    self_service_require_expiry: false,
    self_service_max_expiry_days: '',
  });
  const [policySaving, setPolicySaving] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);

  const openEditPolicy = () => {
    if (!team) return;
    setPolicyError(null);
    setPolicyForm({
      self_service_keys_enabled: !!team.self_service_keys_enabled,
      self_service_max_keys_per_user: team.self_service_max_keys_per_user != null ? String(team.self_service_max_keys_per_user) : '',
      self_service_budget_ceiling: team.self_service_budget_ceiling != null ? String(team.self_service_budget_ceiling) : '',
      self_service_require_expiry: !!team.self_service_require_expiry,
      self_service_max_expiry_days: team.self_service_max_expiry_days != null ? String(team.self_service_max_expiry_days) : '',
    });
    setIsEditingPolicy(true);
  };

  const handleSavePolicy = async () => {
    setPolicySaving(true);
    setPolicyError(null);
    try {
      await teams.update(teamId!, {
        self_service_keys_enabled: policyForm.self_service_keys_enabled,
        self_service_max_keys_per_user: policyForm.self_service_max_keys_per_user ? Number(policyForm.self_service_max_keys_per_user) : null,
        self_service_budget_ceiling: policyForm.self_service_budget_ceiling ? Number(policyForm.self_service_budget_ceiling) : null,
        self_service_require_expiry: policyForm.self_service_require_expiry,
        self_service_max_expiry_days: policyForm.self_service_max_expiry_days ? Number(policyForm.self_service_max_expiry_days) : null,
      });
      setIsEditingPolicy(false);
      refetchTeam();
    } catch (err: any) {
      setPolicyError(err?.message || 'Failed to update self-service policy');
    } finally {
      setPolicySaving(false);
    }
  };

  /* ── derived ── */
  const memberList: any[] = members || [];
  const spend = team?.spend || 0;
  const budget = team?.max_budget ?? null;
  const spendPct = budget ? Math.min(100, Math.round((spend / budget) * 100)) : null;
  const assetMode = teamAssetAccess?.mode ?? 'inherit';
  const assetSummary = teamAssetAccess?.summary;

  const grantedTargets = teamAssetTargets?.selectable_targets?.filter((t: any) => t.selected) ?? [];
  const blockedTargets = teamAssetTargets?.selectable_targets?.filter((t: any) => !t.selected) ?? [];

  const topSpenders = [...memberList]
    .filter((m) => (m.spend || 0) > 0)
    .sort((a, b) => (b.spend || 0) - (a.spend || 0))
    .slice(0, 3);

  /* ── loading / not found ── */
  if (teamLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600" />
      </div>
    );
  }

  if (!team) {
    return (
      <div className="p-6">
        <p className="text-gray-500">Team not found.</p>
        <Link to="/teams" className="text-indigo-600 text-sm mt-2 inline-block">← Back to Teams</Link>
      </div>
    );
  }

  const teamName = team.team_alias || team.team_id;
  const orgName = orgData?.organization_name || orgData?.organization_id || team.organization_id;

  return (
    <EntityDetailShell
      breadcrumbs={[
        { label: 'Teams', onClick: () => navigate('/teams'), icon: ArrowLeft },
        ...(team.organization_id
          ? [{ label: orgName, onClick: () => navigate(`/organizations/${team.organization_id}`), icon: Building2 }]
          : []),
        { label: teamName },
      ]}
      avatar={(
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-blue-700 shadow-sm">
          <Users className="h-6 w-6 text-white" />
        </div>
      )}
      title={teamName}
      badges={(
        <>
          {team.blocked ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-700">
              <AlertOctagon className="h-3.5 w-3.5" /> Blocked
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
              <CheckCircle2 className="h-3.5 w-3.5" /> Active
            </span>
          )}
          {orgName && (
            <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
              <Building2 className="h-3 w-3" /> {orgName}
            </span>
          )}
        </>
      )}
      meta={(
        <div className="flex items-center gap-3">
          <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-400">{team.team_id}</code>
          {team.created_at && (
            <span className="text-xs text-gray-400">
              Created {new Date(team.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
            </span>
          )}
        </div>
      )}
      action={(
        <button
          onClick={openEditSettings}
          className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
        >
          <Pencil className="h-3.5 w-3.5" /> Edit
        </button>
      )}
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
            icon={Users}
            label="Members"
            value={String(memberList.length || team.member_count || 0)}
            sub="in this team"
            tone="blue"
          />
          <DetailMetricCard
            icon={Gauge}
            label="RPM Limit"
            value={team.rpm_limit != null ? Number(team.rpm_limit).toLocaleString() : 'Unlimited'}
            sub="requests / min"
            tone="violet"
          />
          <DetailMetricCard
            icon={Shield}
            label="Asset access"
            value={
              assetMode === 'restrict' && assetSummary
                ? `${assetSummary.selected_total}/${assetSummary.selectable_total}`
                : assetMode === 'restrict' ? 'Restricted' : 'Inherited'
            }
            sub={assetMode === 'restrict' ? 'from org ceiling' : 'full org access'}
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
            { id: 'assets', label: 'Asset Access' },
          ]}
        />
      )}
      notice={teamError ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">{teamError}</div>
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
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${spendPct > 80 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'}`}>
                      {spendPct}% used
                    </span>
                  )}
                </div>
                {budget && (
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden mb-4">
                    <div
                      className={`h-full rounded-full transition-all ${spendPct! > 90 ? 'bg-red-500' : spendPct! > 80 ? 'bg-amber-500' : 'bg-indigo-500'}`}
                      style={{ width: `${spendPct}%` }}
                    />
                  </div>
                )}
                <div className="flex justify-between items-end mb-4">
                  <div>
                    <p className="text-2xl font-bold text-gray-900">${spend.toLocaleString(undefined, { maximumFractionDigits: 2 })}</p>
                    <p className="text-xs text-gray-400 mt-0.5">Current spend</p>
                  </div>
                  {budget && (
                    <div className="text-right">
                      <p className="text-lg font-semibold text-gray-500">${(budget - spend).toLocaleString(undefined, { maximumFractionDigits: 2 })}</p>
                      <p className="text-xs text-gray-400">Remaining</p>
                    </div>
                  )}
                  {!budget && <span className="text-sm text-gray-400">No budget limit</span>}
                </div>
                <div className="border-t border-gray-100 pt-4 grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                      <Gauge className="w-3.5 h-3.5" /> RPM Limit
                    </p>
                    {team.rpm_limit != null
                      ? <p className="text-sm font-semibold">{Number(team.rpm_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">req/min</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                  <div>
                    <p className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                      <TrendingUp className="w-3.5 h-3.5" /> TPM Limit
                    </p>
                    {team.tpm_limit != null
                      ? <p className="text-sm font-semibold">{Number(team.tpm_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">tok/min</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                  <div>
                    <p className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                      <Gauge className="w-3.5 h-3.5" /> RPH Limit
                    </p>
                    {team.rph_limit != null
                      ? <p className="text-sm font-semibold">{Number(team.rph_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">req/hr</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                  <div>
                    <p className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                      <Gauge className="w-3.5 h-3.5" /> RPD Limit
                    </p>
                    {team.rpd_limit != null
                      ? <p className="text-sm font-semibold">{Number(team.rpd_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">req/day</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                  <div>
                    <p className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                      <TrendingUp className="w-3.5 h-3.5" /> TPD Limit
                    </p>
                    {team.tpd_limit != null
                      ? <p className="text-sm font-semibold">{Number(team.tpd_limit).toLocaleString()} <span className="text-xs font-normal text-gray-400">tok/day</span></p>
                      : <p className="text-sm text-gray-400">Unlimited</p>}
                  </div>
                </div>
              </div>

              {/* Top Spenders */}
              {topSpenders.length > 0 && (
                <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                  <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-900">Top Spenders</h3>
                    <button onClick={() => setTab('members')} className="text-xs text-indigo-600 hover:underline">
                      View all →
                    </button>
                  </div>
                  <div className="divide-y divide-gray-100">
                    {topSpenders.map((m: any, idx: number) => (
                      <div key={m.user_id} className="flex items-center gap-3 px-5 py-3 hover:bg-gray-50 transition-colors">
                        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${AVATAR_COLORS[idx % AVATAR_COLORS.length]}`}>
                          {getInitials(m.user_id, m.user_email)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-gray-800 truncate">{m.user_email || m.user_id}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <div className="w-24 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-indigo-400 rounded-full"
                              style={{ width: `${Math.min(100, (m.spend / spend) * 100)}%` }}
                            />
                          </div>
                          <span className="text-xs font-medium text-gray-700 w-14 text-right">
                            ${(m.spend || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Sidebar */}
            <div className="space-y-4">
              {/* Team Info / Settings */}
              <div className={`bg-white rounded-xl border p-4 transition-colors ${isEditingSettings ? 'border-indigo-300 ring-1 ring-indigo-200' : 'border-gray-200'}`}>
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Team Info</h4>
                  {!isEditingSettings && (
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
                    {teamError && (
                      <div className="p-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700">{teamError}</div>
                    )}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Team Name</label>
                      <input
                        value={form.team_alias}
                        onChange={(e) => setForm({ ...form, team_alias: e.target.value })}
                        className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Max Budget ($)</label>
                      <input
                        type="number"
                        value={form.max_budget}
                        onChange={(e) => setForm({ ...form, max_budget: e.target.value })}
                        className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        placeholder="No limit"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">RPM Limit</label>
                        <input
                          type="number"
                          value={form.rpm_limit}
                          onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">TPM Limit</label>
                        <input
                          type="number"
                          value={form.tpm_limit}
                          onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">RPH Limit</label>
                        <input
                          type="number"
                          value={form.rph_limit}
                          onChange={(e) => setForm({ ...form, rph_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">RPD Limit</label>
                        <input
                          type="number"
                          value={form.rpd_limit}
                          onChange={(e) => setForm({ ...form, rpd_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          placeholder="Unlimited"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">TPD Limit</label>
                        <input
                          type="number"
                          value={form.tpd_limit}
                          onChange={(e) => setForm({ ...form, tpd_limit: e.target.value })}
                          className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          placeholder="Unlimited"
                        />
                      </div>
                    </div>
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => { setIsEditingSettings(false); setTeamError(null); }}
                        className="flex-1 px-3 py-1.5 text-xs text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleSaveSettings}
                        disabled={saving}
                        className="flex-1 px-3 py-1.5 text-xs text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50"
                      >
                        {saving ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2.5 text-sm">
                    {team.organization_id && (
                      <div className="flex justify-between items-center">
                        <span className="text-gray-500">Organization</span>
                        <button
                          onClick={() => navigate(`/organizations/${team.organization_id}`)}
                          className="text-xs font-medium text-indigo-600 hover:underline"
                        >
                          {orgName}
                        </button>
                      </div>
                    )}
                    {team.created_at && (
                      <div className="flex justify-between items-center">
                        <span className="text-gray-500">Created</span>
                        <span className="text-xs font-medium text-gray-800">
                          {new Date(team.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                        </span>
                      </div>
                    )}
                    {team.max_budget != null && (
                      <div className="flex justify-between items-center">
                        <span className="text-gray-500">Max budget</span>
                        <span className="text-xs font-semibold text-gray-800">${Number(team.max_budget).toLocaleString()}</span>
                      </div>
                    )}
                    {team.rpm_limit != null && (
                      <div className="flex justify-between items-center">
                        <span className="text-gray-500">RPM limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(team.rpm_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {team.tpm_limit != null && (
                      <div className="flex justify-between items-center">
                        <span className="text-gray-500">TPM limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(team.tpm_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {team.rph_limit != null && (
                      <div className="flex justify-between items-center">
                        <span className="text-gray-500">RPH limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(team.rph_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {team.rpd_limit != null && (
                      <div className="flex justify-between items-center">
                        <span className="text-gray-500">RPD limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(team.rpd_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {team.tpd_limit != null && (
                      <div className="flex justify-between items-center">
                        <span className="text-gray-500">TPD limit</span>
                        <span className="text-xs font-semibold text-gray-800">{Number(team.tpd_limit).toLocaleString()}</span>
                      </div>
                    )}
                    <div className="flex justify-between items-center">
                      <span className="text-gray-500">Status</span>
                      {team.blocked
                        ? <span className="text-xs font-medium text-red-600 flex items-center gap-1"><AlertOctagon className="w-3 h-3" /> Blocked</span>
                        : <span className="text-xs font-medium text-emerald-600 flex items-center gap-1"><CheckCircle2 className="w-3 h-3" /> Active</span>}
                    </div>
                  </div>
                )}
              </div>

              {/* Asset Access summary */}
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <div className="flex items-center gap-1.5 mb-3">
                  <Shield className="w-3.5 h-3.5 text-indigo-600" />
                  <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Asset Access</h4>
                  <Info className="w-3.5 h-3.5 text-gray-400" />
                </div>
                <div className="mb-3">
                  <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${
                    assetMode === 'restrict' ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'
                  }`}>
                    {assetMode === 'restrict'
                      ? <><Lock className="w-3 h-3" /> Restricted</>
                      : <><Unlock className="w-3 h-3" /> Inherited</>}
                  </span>
                </div>
                {assetMode === 'restrict' && assetSummary && (
                  <>
                    <div className="flex justify-between text-xs mb-1.5 text-gray-600">
                      <span>{assetSummary.selected_total} assets selected</span>
                      <span className="text-gray-400">of {assetSummary.selectable_total}</span>
                    </div>
                    <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden mb-2">
                      <div
                        className="h-full bg-indigo-500 rounded-full"
                        style={{ width: `${Math.min(100, (assetSummary.selected_total / Math.max(1, assetSummary.selectable_total)) * 100)}%` }}
                      />
                    </div>
                  </>
                )}
                <button
                  onClick={() => setTab('assets')}
                  className="text-xs text-indigo-600 hover:underline font-medium"
                >
                  Manage asset access →
                </button>
              </div>

              {/* Self-Service Key Policy */}
              {canEditTeam && <div className={`bg-white rounded-xl border p-4 transition-colors ${isEditingPolicy ? 'border-indigo-300 ring-1 ring-indigo-200' : 'border-gray-200'}`}>
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-1.5">
                    <Key className="w-3.5 h-3.5 text-indigo-600" />
                    <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Self-Service Keys</h4>
                  </div>
                  {!isEditingPolicy && canEditTeam && (
                    <button
                      onClick={openEditPolicy}
                      className="p-1 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600 transition-colors"
                      title="Edit policy"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
                {isEditingPolicy ? (
                  <div className="space-y-3">
                    {policyError && (
                      <div className="p-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700">{policyError}</div>
                    )}
                    <label className="flex items-center justify-between cursor-pointer">
                      <span className="text-xs font-medium text-gray-700">Enable self-service keys</span>
                      <button
                        type="button"
                        onClick={() => setPolicyForm((c) => ({ ...c, self_service_keys_enabled: !c.self_service_keys_enabled }))}
                        className="focus:outline-none"
                      >
                        {policyForm.self_service_keys_enabled
                          ? <ToggleRight className="w-6 h-6 text-indigo-600" />
                          : <ToggleLeft className="w-6 h-6 text-gray-400" />}
                      </button>
                    </label>
                    {policyForm.self_service_keys_enabled && (
                      <>
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-1">Max keys per user</label>
                          <input
                            type="number"
                            value={policyForm.self_service_max_keys_per_user}
                            onChange={(e) => setPolicyForm({ ...policyForm, self_service_max_keys_per_user: e.target.value })}
                            className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                            placeholder="Unlimited"
                            min="1"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-1">Budget ceiling ($)</label>
                          <input
                            type="number"
                            value={policyForm.self_service_budget_ceiling}
                            onChange={(e) => setPolicyForm({ ...policyForm, self_service_budget_ceiling: e.target.value })}
                            className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                            placeholder="No ceiling"
                            min="0"
                            step="0.01"
                          />
                        </div>
                        <label className="flex items-center justify-between cursor-pointer">
                          <span className="text-xs font-medium text-gray-700">Require expiry date</span>
                          <button
                            type="button"
                            onClick={() => setPolicyForm((c) => ({ ...c, self_service_require_expiry: !c.self_service_require_expiry }))}
                            className="focus:outline-none"
                          >
                            {policyForm.self_service_require_expiry
                              ? <ToggleRight className="w-6 h-6 text-indigo-600" />
                              : <ToggleLeft className="w-6 h-6 text-gray-400" />}
                          </button>
                        </label>
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-1">Max expiry (days)</label>
                          <input
                            type="number"
                            value={policyForm.self_service_max_expiry_days}
                            onChange={(e) => setPolicyForm({ ...policyForm, self_service_max_expiry_days: e.target.value })}
                            className="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                            placeholder="No limit"
                            min="1"
                          />
                        </div>
                      </>
                    )}
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => { setIsEditingPolicy(false); setPolicyError(null); }}
                        className="flex-1 px-3 py-1.5 text-xs text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleSavePolicy}
                        disabled={policySaving}
                        className="flex-1 px-3 py-1.5 text-xs text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50"
                      >
                        {policySaving ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between items-center">
                      <span className="text-gray-500">Status</span>
                      {team?.self_service_keys_enabled
                        ? <span className="text-xs font-medium text-emerald-600 flex items-center gap-1"><CheckCircle2 className="w-3 h-3" /> Enabled</span>
                        : <span className="text-xs font-medium text-gray-400">Disabled</span>}
                    </div>
                    {team?.self_service_keys_enabled && (
                      <>
                        <div className="flex justify-between items-center">
                          <span className="text-gray-500">Max keys/user</span>
                          <span className="text-xs font-medium text-gray-800">
                            {team.self_service_max_keys_per_user != null ? team.self_service_max_keys_per_user : 'Unlimited'}
                          </span>
                        </div>
                        <div className="flex justify-between items-center">
                          <span className="text-gray-500">Budget ceiling</span>
                          <span className="text-xs font-medium text-gray-800">
                            {team.self_service_budget_ceiling != null ? `$${Number(team.self_service_budget_ceiling).toLocaleString()}` : 'No ceiling'}
                          </span>
                        </div>
                        <div className="flex justify-between items-center">
                          <span className="text-gray-500">Require expiry</span>
                          <span className="text-xs font-medium text-gray-800">
                            {team.self_service_require_expiry ? 'Yes' : 'No'}
                          </span>
                        </div>
                        <div className="flex justify-between items-center">
                          <span className="text-gray-500">Max expiry</span>
                          <span className="text-xs font-medium text-gray-800">
                            {team.self_service_max_expiry_days != null ? `${team.self_service_max_expiry_days} days` : 'No limit'}
                          </span>
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>}
            </div>
          </div>
        )}

        {/* ── MEMBERS ── */}
        {tab === 'members' && (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-gray-50">
              <h3 className="text-sm font-semibold text-gray-900">
                Members {memberList.length > 0 && `(${memberList.length})`}
              </h3>
              <button
                onClick={() => { setMemberSearch(''); setMemberForm({ user_id: '', user_email: '', user_role: 'team_viewer' }); setShowAddMember(true); }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
              >
                <UserPlus className="w-3.5 h-3.5" /> Add Member
              </button>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Member</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Team Role</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Spend</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {membersLoading ? (
                  Array.from({ length: 3 }).map((_, i) => (
                    <tr key={i} className="border-b border-gray-100">
                      {[1, 2, 3, 4].map((j) => (
                        <td key={j} className="px-5 py-3.5"><div className="h-4 bg-gray-100 rounded animate-pulse w-24" /></td>
                      ))}
                    </tr>
                  ))
                ) : memberList.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-5 py-12 text-center text-sm text-gray-400">
                      No members yet.{' '}
                      <button onClick={() => setShowAddMember(true)} className="text-indigo-600 hover:underline">Add the first one</button>
                    </td>
                  </tr>
                ) : (
                  memberList.map((m: any, idx: number) => {
                    const role = ROLE_BADGES[m.user_role] ?? { label: m.user_role, cls: 'bg-gray-100 text-gray-600' };
                    const totalSpend = spend || 1;
                    return (
                      <tr key={m.user_id} className={`hover:bg-gray-50 transition-colors ${idx < memberList.length - 1 ? 'border-b border-gray-100' : ''}`}>
                        <td className="px-5 py-3.5">
                          <div className="flex items-center gap-3">
                            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${AVATAR_COLORS[idx % AVATAR_COLORS.length]}`}>
                              {getInitials(m.user_id, m.user_email)}
                            </div>
                            <div>
                              <p className="font-medium text-gray-900 text-sm">{m.user_email || m.user_id}</p>
                              {m.user_email && <p className="text-[10px] text-gray-400 font-mono">{m.user_id}</p>}
                            </div>
                          </div>
                        </td>
                        <td className="px-5 py-3.5">
                          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${role.cls}`}>{role.label}</span>
                        </td>
                        <td className="px-5 py-3.5">
                          {(m.spend || 0) > 0 ? (
                            <div className="flex items-center gap-2">
                              <div className="w-20 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-indigo-400 rounded-full"
                                  style={{ width: `${Math.min(100, ((m.spend || 0) / totalSpend) * 100)}%` }}
                                />
                              </div>
                              <span className="text-xs text-gray-700">${(m.spend || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
                            </div>
                          ) : (
                            <span className="text-xs text-gray-400">—</span>
                          )}
                        </td>
                        <td className="px-5 py-3.5 text-right">
                          <button
                            onClick={() => handleRemoveMember(m.user_id)}
                            className="p-1.5 hover:bg-red-50 rounded-lg transition-colors"
                            title="Remove member"
                          >
                            <Trash2 className="w-4 h-4 text-red-400" />
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* ── ASSET ACCESS ── */}
        {tab === 'assets' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div className="lg:col-span-2 space-y-4">
              {isEditingAssets ? (
                <div className="bg-white rounded-xl border border-indigo-300 ring-1 ring-indigo-200 p-5 space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-900">Edit Asset Access</h3>
                    <button
                      onClick={() => { setIsEditingAssets(false); setTeamError(null); }}
                      className="text-xs text-gray-500 hover:text-gray-700 transition-colors"
                    >
                      ✕ Cancel
                    </button>
                  </div>
                  {teamError && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{teamError}</div>
                  )}
                  <AssetAccessEditor
                    title="Team Asset Access"
                    description="Choose whether this team inherits the organization asset ceiling or narrows itself to a selected subset."
                    mode={form.asset_access_mode}
                    allowModeSelection
                    onModeChange={(mode) => setForm((c) => ({
                      ...c,
                      asset_access_mode: mode === 'restrict' ? 'restrict' : 'inherit',
                      selected_callable_keys: mode === 'restrict' ? c.selected_callable_keys : [],
                    }))}
                    targets={assetTargets}
                    selectedKeys={form.selected_callable_keys}
                    onSelectedKeysChange={(selected_callable_keys) => setForm({ ...form, selected_callable_keys })}
                    loading={assetAccessLoading}
                    disabled={saving || !form.organization_id}
                  />
                  <div className="flex justify-end gap-3 pt-2 border-t border-gray-100">
                    <button
                      onClick={() => { setIsEditingAssets(false); setTeamError(null); }}
                      className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleSaveAssets}
                      disabled={saving || !form.organization_id || assetAccessLoading}
                      className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50"
                    >
                      {saving ? 'Saving…' : 'Save Changes'}
                    </button>
                  </div>
                </div>
              ) : teamAssetTargetsLoading ? (
                <div className="bg-white rounded-xl border border-gray-200 p-8 flex items-center justify-center">
                  <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-indigo-600" />
                </div>
              ) : assetMode === 'inherit' ? (
                <div className="bg-white rounded-xl border border-gray-200 p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <Unlock className="w-4 h-4 text-gray-500" />
                    <h3 className="text-sm font-semibold text-gray-900">Inherited Access</h3>
                  </div>
                  <p className="text-sm text-gray-500 mb-4">
                    This team inherits the full asset set available to its organization. All models and routes accessible to the org are also accessible here.
                  </p>
                  {teamAssetTargets?.effective_targets && teamAssetTargets.effective_targets.length > 0 && (
                    <div className="space-y-1.5">
                      {teamAssetTargets.effective_targets.map((t: any) => (
                        <div key={t.callable_key} className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-gray-50 border border-gray-200">
                          <CheckCircle2 className="w-4 h-4 text-gray-400 shrink-0" />
                          <span className="text-sm font-medium text-gray-700">{t.callable_key}</span>
                          <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{t.target_type}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <>
                  {/* Granted */}
                  <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                    <div className="px-5 py-4 border-b border-gray-200 flex items-center gap-2">
                      <Lock className="w-4 h-4 text-indigo-600" />
                      <h3 className="text-sm font-semibold text-gray-900">Granted to this team</h3>
                      <span className="ml-auto inline-flex items-center justify-center min-w-[1.5rem] h-5 px-1.5 rounded-full bg-gray-100 text-xs font-semibold text-gray-600">
                        {grantedTargets.length}
                      </span>
                    </div>
                    <div className="p-3 space-y-1.5">
                      {grantedTargets.length === 0
                        ? <p className="text-sm text-gray-400 text-center py-4">No assets granted yet.</p>
                        : grantedTargets.map((t: any) => (
                          <div key={t.callable_key} className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-indigo-50 border border-indigo-100">
                            <CheckCircle2 className="w-4 h-4 text-indigo-600 shrink-0" />
                            <span className="text-sm font-medium text-gray-800">{t.callable_key}</span>
                            <span className="text-xs px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600">{t.target_type}</span>
                          </div>
                        ))}
                    </div>
                  </div>

                  {/* Blocked (available in org, not granted here) */}
                  {blockedTargets.length > 0 && (
                    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                      <div className="px-5 py-4 border-b border-gray-200 flex items-center gap-2">
                        <Lock className="w-4 h-4 text-gray-400" />
                        <h3 className="text-sm font-semibold text-gray-600">Not granted to this team</h3>
                        <span className="text-xs text-gray-400 ml-1">— available in org</span>
                        <span className="ml-auto inline-flex items-center justify-center min-w-[1.5rem] h-5 px-1.5 rounded-full bg-gray-100 text-xs font-semibold text-gray-600">
                          {blockedTargets.length}
                        </span>
                      </div>
                      <div className="p-3 space-y-1.5">
                        {blockedTargets.map((t: any) => (
                          <div key={t.callable_key} className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-gray-50 border border-gray-200 opacity-50">
                            <Lock className="w-4 h-4 text-gray-400 shrink-0" />
                            <span className="text-sm font-medium text-gray-500">{t.callable_key}</span>
                            <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{t.target_type}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Access policy sidebar */}
            <div className="space-y-4">
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Access Policy</h4>
                <div className="space-y-2">
                  <div className={`flex items-start gap-2 p-2.5 rounded-lg border-2 ${
                    assetMode === 'restrict' ? 'border-indigo-500 bg-indigo-50' : 'border-gray-300 bg-gray-50'
                  }`}>
                    {assetMode === 'restrict'
                      ? <Lock className="w-4 h-4 text-indigo-600 mt-0.5 shrink-0" />
                      : <Unlock className="w-4 h-4 text-gray-500 mt-0.5 shrink-0" />}
                    <div>
                      <p className={`text-xs font-semibold ${assetMode === 'restrict' ? 'text-indigo-800' : 'text-gray-700'}`}>
                        {assetMode === 'restrict' ? 'Restricted' : 'Inherited'}
                      </p>
                      <p className={`text-[10px] mt-0.5 ${assetMode === 'restrict' ? 'text-indigo-700' : 'text-gray-500'}`}>
                        {assetMode === 'restrict'
                          ? `Only ${assetSummary?.selected_total ?? '?'} selected assets are accessible. Org ceiling: ${assetSummary?.selectable_total ?? '?'} assets.`
                          : 'All org assets are available to this team.'}
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              {!isEditingAssets && (
                <button
                  onClick={openEditAssets}
                  className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
                >
                  <Pencil className="w-3.5 h-3.5" /> Edit Asset Selection
                </button>
              )}

              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
                <p className="text-xs text-blue-800 leading-relaxed">
                  <strong>{assetMode === 'restrict' ? 'Restricted mode:' : 'Inherited mode:'}</strong>{' '}
                  {assetMode === 'restrict'
                    ? 'API keys and users in this team can only call models in the granted set. This narrows the org\'s ceiling — it never expands it.'
                    : 'This team has access to everything the parent organization allows. Switch to Restricted to limit access to a specific subset.'}
                </p>
              </div>
            </div>
          </div>
        )}

      {/* ── Add Member Modal ── */}
      <Modal open={showAddMember} onClose={() => setShowAddMember(false)} title="Add Member">
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Search Organization Members</label>
            <UserSearchSelect
              search={memberSearch}
              onSearchChange={setMemberSearch}
              options={(memberCandidates || []) as any[]}
              loading={memberCandidatesLoading}
              selectedAccountId={memberForm.user_id}
              onSelect={(a: any) => setMemberForm({ ...memberForm, user_id: a.account_id, user_email: a.email || '' })}
              searchPlaceholder="Search by email or account ID"
              helperText="Results are restricted to users who already belong to this organization."
              emptyText="No organization members match your search."
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team Role</label>
            <select
              value={memberForm.user_role}
              onChange={(e) => setMemberForm({ ...memberForm, user_role: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
            >
              <option value="team_viewer">Viewer</option>
              <option value="team_developer">Developer</option>
              <option value="team_admin">Admin</option>
            </select>
            <p className="text-xs text-gray-400 mt-1">Authorization and scope are managed via RBAC memberships.</p>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowAddMember(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button
              onClick={handleAddMember}
              disabled={saving || !memberForm.user_id.trim()}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50"
            >
              Add Member
            </button>
          </div>
        </div>
      </Modal>
    </EntityDetailShell>
  );
}
