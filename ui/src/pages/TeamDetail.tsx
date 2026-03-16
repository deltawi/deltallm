import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { organizations, teams } from '../lib/api';
import { buildParentScopedAssetTargets, buildScopedSelectableTargets } from '../lib/assetAccess';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import UserSearchSelect from '../components/UserSearchSelect';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import { ArrowLeft, UsersRound, Users, DollarSign, Gauge, Shield, Pencil, UserPlus, Trash2 } from 'lucide-react';

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

export default function TeamDetail() {
  const { teamId } = useParams<{ teamId: string }>();
  const navigate = useNavigate();

  const { data: team, loading: teamLoading, refetch: refetchTeam } = useApi(() => teams.get(teamId!), [teamId]);
  const { data: members, loading: membersLoading, refetch: refetchMembers } = useApi(() => teams.members(teamId!), [teamId]);
  const { data: teamAssetAccess, loading: teamAssetAccessLoading, refetch: refetchTeamAssetAccess } = useApi(
    () => teams.assetAccess(teamId!, { include_targets: false }),
    [teamId],
  );

  const [showEdit, setShowEdit] = useState(false);
  const [form, setForm] = useState({
    team_alias: '',
    organization_id: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    asset_access_mode: 'inherit' as 'inherit' | 'restrict',
    selected_callable_keys: [] as string[],
  });
  const [showAddMember, setShowAddMember] = useState(false);
  const [memberSearch, setMemberSearch] = useState('');
  const [memberForm, setMemberForm] = useState({ user_id: '', user_email: '', user_role: 'team_viewer' });
  const [saving, setSaving] = useState(false);
  const [teamError, setTeamError] = useState<string | null>(null);
  const selectedOrganizationId = form.organization_id.trim();
  const usesParentPreview = selectedOrganizationId !== (team?.organization_id || '');
  const { data: teamAssetAccessTargets, loading: teamAssetAccessTargetsLoading } = useApi(
    () => (
      showEdit && !usesParentPreview && form.asset_access_mode === 'restrict'
        ? teams.assetAccess(teamId!, { include_targets: true })
        : Promise.resolve(null)
    ),
    [showEdit, teamId, usesParentPreview, form.asset_access_mode],
  );
  const { data: parentOrgAssetVisibility, loading: parentOrgAssetVisibilityLoading } = useApi(
    () => (
      showEdit && usesParentPreview && form.asset_access_mode === 'restrict' && selectedOrganizationId
        ? organizations.assetVisibility(selectedOrganizationId)
        : Promise.resolve(null)
    ),
    [showEdit, selectedOrganizationId, usesParentPreview, form.asset_access_mode],
  );
  const { data: memberCandidates, loading: memberCandidatesLoading } = useApi(
    () => showAddMember ? teams.memberCandidates(teamId!, { search: memberSearch, limit: 50 }) : Promise.resolve([]),
    [teamId, showAddMember, memberSearch],
  );

  useEffect(() => {
    if (!showEdit || !teamAssetAccess) return;
    setForm((current) => ({
      ...current,
      asset_access_mode: teamAssetAccess.mode === 'restrict' ? 'restrict' : 'inherit',
      selected_callable_keys: teamAssetAccess.selected_callable_keys || [],
    }));
  }, [showEdit, teamAssetAccess]);

  const openEdit = () => {
    if (!team) return;
    setTeamError(null);
    setForm({
      team_alias: team.team_alias || '',
      organization_id: team.organization_id || '',
      max_budget: team.max_budget != null ? String(team.max_budget) : '',
      rpm_limit: team.rpm_limit != null ? String(team.rpm_limit) : '',
      tpm_limit: team.tpm_limit != null ? String(team.tpm_limit) : '',
      asset_access_mode: teamAssetAccess?.mode === 'restrict' ? 'restrict' : 'inherit',
      selected_callable_keys: teamAssetAccess?.selected_callable_keys || [],
    });
    setShowEdit(true);
  };

  const handleOrganizationChange = (organizationId: string) => {
    setForm((current) => {
      const changed = current.organization_id !== organizationId;
      return {
        ...current,
        organization_id: organizationId,
        asset_access_mode: changed ? 'inherit' : current.asset_access_mode,
        selected_callable_keys: changed ? [] : current.selected_callable_keys,
      };
    });
  };

  const handleSaveTeam = async () => {
    setSaving(true);
    setTeamError(null);
    try {
      await teams.update(teamId!, {
        team_alias: form.team_alias || undefined,
        organization_id: form.organization_id || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
      });
      await teams.updateAssetAccess(teamId!, {
        mode: form.asset_access_mode,
        selected_callable_keys: form.asset_access_mode === 'restrict' ? form.selected_callable_keys : [],
      });
      setShowEdit(false);
      refetchTeam();
      refetchTeamAssetAccess();
    } catch (err: any) {
      setTeamError(err?.message || 'Failed to update team');
    } finally {
      setSaving(false);
    }
  };

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

  if (teamLoading) {
    return (
      <div className="p-6 flex items-center justify-center py-24">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!team) {
    return (
      <div className="p-6">
        <p className="text-gray-500">Team not found.</p>
        <Link to="/teams" className="text-blue-600 text-sm mt-2 inline-block">Back to Teams</Link>
      </div>
    );
  }

  const memberColumns = [
    { key: 'user_id', header: 'User ID', render: (r: any) => <span className="font-medium font-mono text-xs">{r.user_id}</span> },
    { key: 'user_email', header: 'Email', render: (r: any) => r.user_email || <span className="text-gray-400">--</span> },
    { key: 'user_role', header: 'Team Role', render: (r: any) => (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700">{r.user_role}</span>
    ) },
    { key: 'spend', header: 'Spend', render: (r: any) => <span className="text-sm">${(r.spend || 0).toFixed(2)}</span> },
    { key: 'actions', header: '', render: (r: any) => (
      <button onClick={() => handleRemoveMember(r.user_id)} className="p-1.5 hover:bg-red-50 rounded-lg transition-colors" title="Remove member">
        <Trash2 className="w-4 h-4 text-red-500" />
      </button>
    ) },
  ];
  const assetTargets = usesParentPreview
    ? buildParentScopedAssetTargets(
        parentOrgAssetVisibility?.callable_targets?.items || [],
        form.selected_callable_keys,
        form.asset_access_mode,
      )
    : buildScopedSelectableTargets(
        teamAssetAccessTargets?.selectable_targets || [],
        form.selected_callable_keys,
        form.asset_access_mode,
      );
  const assetAccessLoading = form.asset_access_mode !== 'restrict'
    ? false
    : usesParentPreview
      ? parentOrgAssetVisibilityLoading
      : teamAssetAccessTargetsLoading || teamAssetAccessLoading;

  return (
    <div className="p-4 sm:p-6 max-w-6xl">
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 mb-6">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <button onClick={() => navigate('/teams')} className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors">
            <ArrowLeft className="w-5 h-5 text-gray-500" />
          </button>
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-50 rounded-lg">
              <UsersRound className="w-5 h-5 text-indigo-600" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900">{team.team_alias || team.team_id}</h1>
              <div className="flex items-center gap-2 mt-0.5">
                <code className="text-xs text-gray-400 font-mono">{team.team_id}</code>
                {team.organization_id && (
                  <>
                    <span className="text-gray-300">|</span>
                    <Link to={`/organizations/${team.organization_id}`} className="text-xs text-blue-500 hover:text-blue-600">
                      Org: {team.organization_id}
                    </Link>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
        <button onClick={openEdit} className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
          <Pencil className="w-4 h-4" /> Edit
        </button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard icon={DollarSign} label="Spend" value={`$${(team.spend || 0).toFixed(2)}`} subValue={team.max_budget ? `of $${team.max_budget} budget` : 'No budget limit'} color="bg-green-50 text-green-600" />
        <StatCard icon={Users} label="Members" value={String(members?.length || 0)} color="bg-blue-50 text-blue-600" />
        <StatCard icon={Gauge} label="RPM Limit" value={team.rpm_limit != null ? team.rpm_limit.toLocaleString() : 'Unlimited'} subValue="Requests per minute" color="bg-purple-50 text-purple-600" />
        <StatCard icon={Shield} label="Access" value="Scoped" subValue="Inherit or restrict this team below" color="bg-orange-50 text-orange-600" />
      </div>

      <div className="mb-6 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
        Team runtime access comes from callable-target bindings and scope policies across organization, team, key, and user scopes. Use the edit dialog to inherit the org set or narrow it for this team.
      </div>

      <Card
        title="Members"
        action={
          <button onClick={() => { setMemberSearch(''); setMemberForm({ user_id: '', user_email: '', user_role: 'team_viewer' }); setShowAddMember(true); }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
            <UserPlus className="w-3.5 h-3.5" /> Add Member
          </button>
        }
      >
        <DataTable
          columns={memberColumns}
          data={members || []}
          loading={membersLoading}
          emptyMessage="No members in this team yet"
        />
      </Card>

      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Team">
        <div className="space-y-4">
          {teamError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{teamError}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team Name</label>
            <input value={form.team_alias} onChange={(e) => setForm({ ...form, team_alias: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Organization ID</label>
              <input value={form.organization_id} onChange={(e) => handleOrganizationChange(e.target.value)} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
              <input type="number" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
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
          <p className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
            Team runtime access is enforced through callable-target bindings and scope policies. Use the section below to inherit the organization set or narrow it for this team.
          </p>
          <AssetAccessEditor
            title="Team Asset Access"
            description="Choose whether this team inherits the organization asset ceiling or narrows itself to a selected subset."
            mode={form.asset_access_mode}
            allowModeSelection
            onModeChange={(asset_access_mode) => setForm((current) => ({
              ...current,
              asset_access_mode: asset_access_mode === 'restrict' ? 'restrict' : 'inherit',
              selected_callable_keys: asset_access_mode === 'restrict' ? current.selected_callable_keys : [],
            }))}
            targets={assetTargets}
            selectedKeys={form.selected_callable_keys}
            onSelectedKeysChange={(selected_callable_keys) => setForm({ ...form, selected_callable_keys })}
            loading={assetAccessLoading}
            disabled={saving || !form.organization_id}
          />
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowEdit(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSaveTeam} disabled={saving || !form.organization_id || assetAccessLoading} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">Save Changes</button>
          </div>
        </div>
      </Modal>

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
            <select value={memberForm.user_role} onChange={(e) => setMemberForm({ ...memberForm, user_role: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
              <option value="team_viewer">Viewer</option>
              <option value="team_developer">Developer</option>
              <option value="team_admin">Admin</option>
            </select>
            <p className="text-xs text-gray-400 mt-1">Authorization and scope are managed via RBAC memberships.</p>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowAddMember(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleAddMember} disabled={saving || !memberForm.user_id.trim()} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">Add Member</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
