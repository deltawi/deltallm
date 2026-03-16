import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { callableTargets, organizations, teams as teamsApi } from '../lib/api';
import { buildCatalogAssetTargets, buildParentScopedAssetTargets } from '../lib/assetAccess';
import { useAuth } from '../lib/auth';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import UserSearchSelect from '../components/UserSearchSelect';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import { ArrowLeft, Building2, Users, DollarSign, Gauge, Pencil, Plus, User, UserPlus, Trash2 } from 'lucide-react';

function StatCard({ icon: Icon, label, value, subValue, color }: { icon: any; label: string; value: string; subValue?: string; color: string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <div className="flex items-center gap-3 mb-3">
        <div className={`p-2 rounded-lg ${color}`}>
          <Icon className="w-4 h-4" />
        </div>
        <span className="text-sm text-gray-500">{label}</span>
      </div>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      {subValue && <p className="text-xs text-gray-400 mt-1">{subValue}</p>}
    </div>
  );
}

function BudgetBar({ spend, max_budget }: { spend: number; max_budget: number | null }) {
  if (!max_budget) return <span className="text-gray-400 text-xs">No limit</span>;
  const pct = Math.min(100, (spend / max_budget) * 100);
  return (
    <div className="w-24">
      <div className="flex justify-between text-xs mb-0.5">
        <span>${spend.toFixed(2)}</span>
        <span className="text-gray-400">${max_budget}</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-blue-500'}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function OrganizationDetail() {
  const { orgId } = useParams<{ orgId: string }>();
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';

  const { data: org, loading: orgLoading, refetch: refetchOrg } = useApi(() => organizations.get(orgId!), [orgId]);
  const { data: orgTeams, loading: teamsLoading, refetch: refetchTeams } = useApi(() => organizations.teams(orgId!), [orgId]);
  const { data: orgMembers, loading: membersLoading, refetch: refetchMembers } = useApi(() => organizations.members(orgId!), [orgId]);
  const { data: orgAssetAccess, loading: orgAssetAccessLoading, refetch: refetchOrgAssetAccess } = useApi(
    () => (isPlatformAdmin ? organizations.assetAccess(orgId!, { include_targets: false }) : Promise.resolve(null)),
    [orgId, isPlatformAdmin],
  );

  const [showEdit, setShowEdit] = useState(false);
  const [assetSearchInput, setAssetSearchInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const [assetPageOffset, setAssetPageOffset] = useState(0);
  const [assetTargetType, setAssetTargetType] = useState<'all' | 'model' | 'route_group'>('all');
  const assetPageSize = 50;
  const [form, setForm] = useState({
    organization_name: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    audit_content_storage_enabled: false,
    select_all_current_assets: false,
    selected_callable_keys: [] as string[],
  });
  const [showCreateTeam, setShowCreateTeam] = useState(false);
  const [teamForm, setTeamForm] = useState({
    team_alias: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    asset_access_mode: 'inherit' as 'inherit' | 'restrict',
    selected_callable_keys: [] as string[],
  });
  const [showAddMember, setShowAddMember] = useState(false);
  const [memberSearch, setMemberSearch] = useState('');
  const [memberForm, setMemberForm] = useState({ account_id: '', role: 'org_member' });
  const [saving, setSaving] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [orgError, setOrgError] = useState<string | null>(null);
  const [teamError, setTeamError] = useState<string | null>(null);
  const [memberError, setMemberError] = useState<string | null>(null);
  const { data: memberCandidates, loading: memberCandidatesLoading } = useApi(
    () => showAddMember ? organizations.memberCandidates(orgId!, { search: memberSearch, limit: 50 }) : Promise.resolve([]),
    [orgId, showAddMember, memberSearch],
  );
  const { data: childTeamAssetVisibility, loading: childTeamAssetVisibilityLoading } = useApi(
    () => (
      showCreateTeam && teamForm.asset_access_mode === 'restrict'
        ? organizations.assetVisibility(orgId!)
        : Promise.resolve(null)
    ),
    [orgId, showCreateTeam, teamForm.asset_access_mode],
  );
  const { data: callableTargetPage, loading: callableTargetPageLoading } = useApi(
    () => (
      isPlatformAdmin && showEdit && !form.select_all_current_assets
        ? callableTargets.list({
            search: assetSearch || undefined,
            target_type: assetTargetType === 'all' ? undefined : assetTargetType,
            limit: assetPageSize,
            offset: assetPageOffset,
          })
        : Promise.resolve({
            data: [],
            pagination: { total: 0, limit: assetPageSize, offset: 0, has_more: false },
          })
    ),
    [isPlatformAdmin, showEdit, form.select_all_current_assets, assetSearch, assetTargetType, assetPageOffset],
  );

  useEffect(() => {
    const t = setTimeout(() => {
      setAssetSearch(assetSearchInput);
      setAssetPageOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [assetSearchInput]);

  useEffect(() => {
    if (!showEdit || !orgAssetAccess) return;
    setForm((current) => ({
      ...current,
      select_all_current_assets:
        orgAssetAccess.summary.selectable_total > 0 &&
        orgAssetAccess.summary.selected_total === orgAssetAccess.summary.selectable_total,
      selected_callable_keys: orgAssetAccess.selected_callable_keys || [],
    }));
  }, [showEdit, orgAssetAccess]);

  const openEdit = () => {
    if (!org) return;
    setOrgError(null);
    setForm({
      organization_name: org.organization_name || '',
      max_budget: org.max_budget != null ? String(org.max_budget) : '',
      rpm_limit: org.rpm_limit != null ? String(org.rpm_limit) : '',
      tpm_limit: org.tpm_limit != null ? String(org.tpm_limit) : '',
      audit_content_storage_enabled: !!org.audit_content_storage_enabled,
      select_all_current_assets: false,
      selected_callable_keys: orgAssetAccess?.selected_callable_keys || [],
    });
    setAssetSearchInput('');
    setAssetSearch('');
    setAssetPageOffset(0);
    setAssetTargetType('all');
    setShowEdit(true);
  };

  const handleSaveOrg = async () => {
    setSaving(true);
    setOrgError(null);
    try {
      await organizations.update(orgId!, {
        organization_name: form.organization_name || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        audit_content_storage_enabled: !!form.audit_content_storage_enabled,
      });
      if (isPlatformAdmin) {
        await organizations.updateAssetAccess(orgId!, {
          selected_callable_keys: form.select_all_current_assets ? [] : form.selected_callable_keys,
          select_all_selectable: form.select_all_current_assets,
        });
        refetchOrgAssetAccess();
      }
      setShowEdit(false);
      refetchOrg();
    } catch (err: any) {
      setOrgError(err?.message || 'Failed to update organization');
    } finally {
      setSaving(false);
    }
  };

  const handleCreateTeam = async () => {
    setSaving(true);
    setTeamError(null);
    try {
      const created = await teamsApi.create({
        team_alias: teamForm.team_alias || undefined,
        organization_id: orgId,
        max_budget: teamForm.max_budget ? Number(teamForm.max_budget) : undefined,
        rpm_limit: teamForm.rpm_limit ? Number(teamForm.rpm_limit) : undefined,
        tpm_limit: teamForm.tpm_limit ? Number(teamForm.tpm_limit) : undefined,
      });
      let assetAccessError: string | null = null;
      if (teamForm.asset_access_mode === 'restrict') {
        try {
          await teamsApi.updateAssetAccess(created.team_id, {
            mode: 'restrict',
            selected_callable_keys: teamForm.selected_callable_keys,
          });
        } catch (err: any) {
          assetAccessError = err?.message || 'Team created, but asset access could not be updated. Open the team again to finish access setup.';
        }
      }
      setShowCreateTeam(false);
      setTeamForm({
        team_alias: '',
        max_budget: '',
        rpm_limit: '',
        tpm_limit: '',
        asset_access_mode: 'inherit',
        selected_callable_keys: [],
      });
      refetchTeams();
      setPageError(assetAccessError);
    } catch (err: any) {
      setTeamError(err?.message || 'Failed to create team');
    } finally {
      setSaving(false);
    }
  };

  const openAddMember = () => {
    setMemberError(null);
    setMemberSearch('');
    setMemberForm({ account_id: '', role: 'org_member' });
    setShowAddMember(true);
  };

  const handleAddMember = async () => {
    if (!memberForm.account_id) {
      setMemberError('Select an account to add.');
      return;
    }
    setSaving(true);
    setMemberError(null);
    try {
      await organizations.addMember(orgId!, {
        account_id: memberForm.account_id,
        role: memberForm.role,
      });
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

  if (orgLoading) {
    return (
      <div className="p-6 flex items-center justify-center py-24">
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

  const teamColumns = [
    { key: 'team_alias', header: 'Name', render: (r: any) => (
      <Link to={`/teams/${r.team_id}`} className="font-medium text-blue-600 hover:text-blue-700">{r.team_alias || r.team_id}</Link>
    ) },
    { key: 'team_id', header: 'Team ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.team_id}</code> },
    { key: 'member_count', header: 'Members', render: (r: any) => (
      <span className="flex items-center gap-1"><Users className="w-3.5 h-3.5 text-gray-400" /> {r.member_count || 0}</span>
    ) },
    { key: 'budget', header: 'Budget', render: (r: any) => <BudgetBar spend={r.spend || 0} max_budget={r.max_budget} /> },
  ];

  const memberColumns = [
    { key: 'email', header: 'Email', render: (r: any) => r.email || <span className="text-gray-400">--</span> },
    { key: 'account_id', header: 'Account ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded font-mono">{r.account_id}</code> },
    { key: 'org_role', header: 'Org Role', render: (r: any) => (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700">{r.org_role}</span>
    ) },
    { key: 'team_count', header: 'Teams', render: (r: any) => <span className="text-sm">{r.team_count || 0}</span> },
    { key: 'teams', header: 'Team Memberships', render: (r: any) => (
      r.teams?.length ? <span className="text-xs">{r.teams.join(', ')}</span> : <span className="text-gray-400 text-xs">None</span>
    ) },
    { key: 'actions', header: '', render: (r: any) => (
      <button onClick={() => handleRemoveMember(r.membership_id)} className="p-1.5 hover:bg-red-50 rounded-lg" title="Remove member">
        <Trash2 className="w-4 h-4 text-red-500" />
      </button>
    ) },
  ];
  const childTeamAssetTargets = buildParentScopedAssetTargets(
    childTeamAssetVisibility?.callable_targets?.items || [],
    teamForm.selected_callable_keys,
    teamForm.asset_access_mode,
  );
  const orgAssetTargets = buildCatalogAssetTargets((callableTargetPage?.data || []) as any[], form.selected_callable_keys);
  const orgAssetPagination = callableTargetPage?.pagination;

  return (
    <div className="p-4 sm:p-6 max-w-6xl">
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 mb-6">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <button onClick={() => navigate('/organizations')} className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors">
            <ArrowLeft className="w-5 h-5 text-gray-500" />
          </button>
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-50 rounded-lg">
              <Building2 className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900">{org.organization_name || org.organization_id}</h1>
              <p className="text-xs text-gray-400 font-mono mt-0.5">{org.organization_id}</p>
            </div>
          </div>
        </div>
        <button onClick={openEdit} className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
          <Pencil className="w-4 h-4" /> Edit
        </button>
      </div>
      {pageError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{pageError}</div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard icon={DollarSign} label="Spend" value={`$${(org.spend || 0).toFixed(2)}`} subValue={org.max_budget ? `of $${org.max_budget} budget` : 'No budget limit'} color="bg-green-50 text-green-600" />
        <StatCard icon={Users} label="Teams" value={String(orgTeams?.length || 0)} color="bg-blue-50 text-blue-600" />
        <StatCard icon={User} label="Members" value={String(orgMembers?.length || 0)} subValue="Across all teams" color="bg-teal-50 text-teal-600" />
        <StatCard icon={Gauge} label="RPM Limit" value={org.rpm_limit != null ? org.rpm_limit.toLocaleString() : 'Unlimited'} subValue="Requests per minute" color="bg-purple-50 text-purple-600" />
      </div>

      <div className="space-y-6">
        <Card
          title="Teams"
          action={
            <button onClick={() => {
              setTeamError(null);
              setTeamForm({
                team_alias: '',
                max_budget: '',
                rpm_limit: '',
                tpm_limit: '',
                asset_access_mode: 'inherit',
                selected_callable_keys: [],
              });
              setShowCreateTeam(true);
            }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
              <Plus className="w-3.5 h-3.5" /> Add Team
            </button>
          }
        >
          <p className="mb-4 text-sm text-gray-600">
            Teams define ownership, memberships, budgets, rate limits, and narrowed runtime access under this organization’s allowed asset set.
          </p>
          <DataTable
            columns={teamColumns}
            data={orgTeams || []}
            loading={teamsLoading}
            emptyMessage="No teams in this organization"
            onRowClick={(r) => navigate(`/teams/${r.team_id}`)}
          />
        </Card>

        <Card
          title="Members"
          action={
            <button onClick={openAddMember} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
              <UserPlus className="w-3.5 h-3.5" /> Add Member
            </button>
          }
        >
          <DataTable
            columns={memberColumns}
            data={orgMembers || []}
            loading={membersLoading}
            emptyMessage="No organization members yet"
          />
        </Card>
      </div>

      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Organization">
        <div className="space-y-4">
          {orgError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{orgError}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Organization Name</label>
            <input value={form.organization_name} onChange={(e) => setForm({ ...form, organization_name: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
            <input type="number" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input type="number" value={form.rpm_limit} onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input type="number" value={form.tpm_limit} onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <label className="flex items-start gap-3 p-3 border border-gray-200 rounded-lg bg-gray-50">
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
                <label className={`rounded-lg border px-3 py-2 text-sm ${form.select_all_current_assets ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                  <div className="flex items-start gap-2">
                    <input
                      type="radio"
                      name="organization-detail-asset-strategy"
                      checked={form.select_all_current_assets}
                      onChange={() => setForm((current) => ({ ...current, select_all_current_assets: true, selected_callable_keys: [] }))}
                      disabled={saving}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block font-medium text-gray-900">Allow all current assets</span>
                      <span className="block text-xs text-gray-500">Grant every current model and route group without loading the full catalog in the browser.</span>
                    </span>
                  </div>
                </label>
                <label className={`rounded-lg border px-3 py-2 text-sm ${!form.select_all_current_assets ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                  <div className="flex items-start gap-2">
                    <input
                      type="radio"
                      name="organization-detail-asset-strategy"
                      checked={!form.select_all_current_assets}
                      onChange={() => setForm((current) => ({ ...current, select_all_current_assets: false }))}
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
                    ? `This organization currently has ${orgAssetAccess.summary.selected_total} of ${orgAssetAccess.summary.selectable_total} assets granted. Saving with this option will align it to all current assets.`
                    : 'Saving will grant every currently available model and route group to this organization.'}
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
                  onTargetTypeFilterChange={(next) => {
                    setAssetTargetType(next);
                    setAssetPageOffset(0);
                  }}
                  pagination={orgAssetPagination}
                  onPageChange={setAssetPageOffset}
                  primaryActionLabel="Allow all current assets"
                  onPrimaryAction={() => setForm((current) => ({ ...current, select_all_current_assets: true, selected_callable_keys: [] }))}
                  secondaryActionLabel={form.selected_callable_keys.length > 0 ? 'Clear selection' : undefined}
                  onSecondaryAction={form.selected_callable_keys.length > 0 ? () => setForm((current) => ({ ...current, selected_callable_keys: [] })) : undefined}
                />
              )}
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowEdit(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSaveOrg} disabled={saving || (isPlatformAdmin && orgAssetAccessLoading)} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : 'Save Changes'}</button>
          </div>
        </div>
      </Modal>

      <Modal open={showCreateTeam} onClose={() => setShowCreateTeam(false)} title="Add Team to Organization">
        <div className="space-y-4">
          {teamError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{teamError}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team Name</label>
            <input value={teamForm.team_alias} onChange={(e) => setTeamForm({ ...teamForm, team_alias: e.target.value })} placeholder="Engineering" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
              <input type="number" value={teamForm.max_budget} onChange={(e) => setTeamForm({ ...teamForm, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
              Team access starts from this organization’s allowed assets. Use the section below to keep the full set or narrow it for this team.
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input type="number" value={teamForm.rpm_limit} onChange={(e) => setTeamForm({ ...teamForm, rpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input type="number" value={teamForm.tpm_limit} onChange={(e) => setTeamForm({ ...teamForm, tpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <AssetAccessEditor
            title="Team Asset Access"
            description="New teams inherit the organization asset ceiling by default. Switch to restrict if this team should only use a smaller subset."
            mode={teamForm.asset_access_mode}
            allowModeSelection
            onModeChange={(asset_access_mode) => setTeamForm((current) => ({
              ...current,
              asset_access_mode: asset_access_mode === 'restrict' ? 'restrict' : 'inherit',
              selected_callable_keys: asset_access_mode === 'restrict' ? current.selected_callable_keys : [],
            }))}
            targets={childTeamAssetTargets}
            selectedKeys={teamForm.selected_callable_keys}
            onSelectedKeysChange={(selected_callable_keys) => setTeamForm({ ...teamForm, selected_callable_keys })}
            loading={childTeamAssetVisibilityLoading}
            disabled={saving}
          />
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowCreateTeam(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleCreateTeam} disabled={saving || childTeamAssetVisibilityLoading} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Creating...' : 'Create Team'}</button>
          </div>
        </div>
      </Modal>

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
            <button onClick={handleAddMember} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Adding...' : 'Add Member'}</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
