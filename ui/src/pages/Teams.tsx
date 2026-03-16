import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { teams, organizations } from '../lib/api';
import { buildParentScopedAssetTargets, buildScopedSelectableTargets } from '../lib/assetAccess';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import UserSearchSelect from '../components/UserSearchSelect';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import { Plus, Users, Trash2, UserPlus, Pencil } from 'lucide-react';

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

function RateLimit({ value, unit }: { value: number | null | undefined; unit: string }) {
  if (value == null) return <span className="text-gray-400 text-xs">No limit</span>;
  return <span className="text-xs font-medium">{Number(value).toLocaleString()} {unit}</span>;
}

export default function Teams() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 10;
  const { data: result, loading, refetch } = useApi(() => teams.list({ search, limit: pageSize, offset: pageOffset }), [search, pageOffset]);
  const items = result?.data || [];
  const pagination = result?.pagination;
  const { data: orgResult } = useApi(() => organizations.list({ limit: 500 }), []);
  const orgList = orgResult?.data || [];

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPageOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [selectedTeam, setSelectedTeam] = useState<any>(null);
  const [form, setForm] = useState({
    team_alias: '',
    organization_id: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    asset_access_mode: 'inherit' as 'inherit' | 'restrict',
    selected_callable_keys: [] as string[],
  });
  const [memberSearch, setMemberSearch] = useState('');
  const [memberForm, setMemberForm] = useState({ user_id: '', user_email: '', user_role: 'team_viewer' });
  const { data: editAssetAccess, loading: editAssetAccessLoading } = useApi(
    () => (editItem ? teams.assetAccess(editItem.team_id, { include_targets: false }) : Promise.resolve(null)),
    [editItem?.team_id],
  );
  const selectedOrganizationId = form.organization_id.trim();
  const usesParentPreview = !editItem || selectedOrganizationId !== (editItem.organization_id || '');
  const { data: editAssetAccessTargets, loading: editAssetAccessTargetsLoading } = useApi(
    () => (
      editItem && !usesParentPreview && form.asset_access_mode === 'restrict'
        ? teams.assetAccess(editItem.team_id, { include_targets: true })
        : Promise.resolve(null)
    ),
    [editItem?.team_id, usesParentPreview, form.asset_access_mode],
  );
  const { data: parentOrgAssetVisibility, loading: parentOrgAssetVisibilityLoading } = useApi(
    () => (
      (showCreate || !!editItem) && usesParentPreview && form.asset_access_mode === 'restrict' && selectedOrganizationId
        ? organizations.assetVisibility(selectedOrganizationId)
        : Promise.resolve(null)
    ),
    [showCreate, editItem?.team_id, selectedOrganizationId, usesParentPreview, form.asset_access_mode],
  );

  const { data: members, refetch: refetchMembers } = useApi(
    () => selectedTeam ? teams.members(selectedTeam.team_id) : Promise.resolve([]),
    [selectedTeam?.team_id]
  );
  const { data: memberCandidates, loading: memberCandidatesLoading } = useApi(
    () => selectedTeam ? teams.memberCandidates(selectedTeam.team_id, { search: memberSearch, limit: 50 }) : Promise.resolve([]),
    [selectedTeam?.team_id, memberSearch]
  );

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const resetForm = () => setForm({
    team_alias: '',
    organization_id: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    asset_access_mode: 'inherit',
    selected_callable_keys: [],
  });

  useEffect(() => {
    if (!editItem || !editAssetAccess) return;
    setForm((current) => ({
      ...current,
      asset_access_mode: editAssetAccess.mode === 'restrict' ? 'restrict' : 'inherit',
      selected_callable_keys: editAssetAccess.selected_callable_keys || [],
    }));
  }, [editItem, editAssetAccess]);

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

  const handleSave = async () => {
    setError(null);
    setSaving(true);
    try {
      const payload = {
        team_alias: form.team_alias || undefined,
        organization_id: form.organization_id || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
      };
      if (editItem) {
        await teams.update(editItem.team_id, payload);
        await teams.updateAssetAccess(editItem.team_id, {
          mode: form.asset_access_mode,
          selected_callable_keys: form.asset_access_mode === 'restrict' ? form.selected_callable_keys : [],
        });
        setPageError(null);
      } else {
        const created = await teams.create(payload);
        let assetAccessError: string | null = null;
        if (form.asset_access_mode === 'restrict') {
          try {
            await teams.updateAssetAccess(created.team_id, {
              mode: 'restrict',
              selected_callable_keys: form.selected_callable_keys,
            });
          } catch (err: any) {
            assetAccessError = err?.message || 'Team created, but asset access could not be updated. Open the team again to finish access setup.';
          }
        }
        setPageError(assetAccessError);
      }
      setShowCreate(false);
      setEditItem(null);
      resetForm();
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to save team');
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (row: any) => {
    setPageError(null);
    setForm({
      team_alias: row.team_alias || '',
      organization_id: row.organization_id || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      asset_access_mode: 'inherit',
      selected_callable_keys: [],
    });
    setEditItem(row);
  };

  const assetTargets = usesParentPreview
    ? buildParentScopedAssetTargets(
        parentOrgAssetVisibility?.callable_targets?.items || [],
        form.selected_callable_keys,
        form.asset_access_mode,
      )
    : buildScopedSelectableTargets(
        editAssetAccessTargets?.selectable_targets || [],
        form.selected_callable_keys,
        form.asset_access_mode,
      );
  const assetAccessLoading = form.asset_access_mode !== 'restrict'
    ? false
    : usesParentPreview
      ? parentOrgAssetVisibilityLoading
      : editAssetAccessTargetsLoading || editAssetAccessLoading;

  const handleAddMember = async () => {
    if (!selectedTeam) return;
    await teams.addMember(selectedTeam.team_id, {
      user_id: memberForm.user_id,
      user_email: memberForm.user_email || undefined,
      user_role: memberForm.user_role,
    });
    setMemberForm({ user_id: '', user_email: '', user_role: 'team_viewer' });
    refetchMembers();
  };

  const handleRemoveMember = async (userId: string) => {
    if (!selectedTeam || !confirm('Remove this member from the team?')) return;
    await teams.removeMember(selectedTeam.team_id, userId);
    refetchMembers();
  };

  const handleDelete = async (row: any) => {
    if (!confirm(`Delete team "${row.team_alias || row.team_id}"? All members will be unassigned.`)) return;
    try {
      await teams.delete(row.team_id);
      if (selectedTeam?.team_id === row.team_id) setSelectedTeam(null);
      refetch();
    } catch (err: any) {
      alert(err?.message || 'Failed to delete team');
    }
  };

  const columns = [
    { key: 'team_alias', header: 'Name', render: (r: any) => <span className="font-medium">{r.team_alias || r.team_id}</span> },
    { key: 'team_id', header: 'Team ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.team_id}</code> },
    { key: 'member_count', header: 'Members', render: (r: any) => <span className="flex items-center gap-1"><Users className="w-3.5 h-3.5 text-gray-400" /> {r.member_count || 0}</span> },
    { key: 'budget', header: 'Budget', render: (r: any) => <BudgetBar spend={r.spend || 0} max_budget={r.max_budget} /> },
    { key: 'rpm_limit', header: 'RPM', render: (r: any) => <RateLimit value={r.rpm_limit} unit="req/min" /> },
    { key: 'tpm_limit', header: 'TPM', render: (r: any) => <RateLimit value={r.tpm_limit} unit="tok/min" /> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <div className="flex gap-1">
          <button onClick={(e) => { e.stopPropagation(); openEdit(r); }} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Edit"><Pencil className="w-4 h-4 text-gray-500" /></button>
          <button onClick={(e) => { e.stopPropagation(); setSelectedTeam(r); setMemberSearch(''); setMemberForm({ user_id: '', user_email: '', user_role: 'team_viewer' }); }} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Members"><Users className="w-4 h-4 text-gray-500" /></button>
          <button onClick={(e) => { e.stopPropagation(); handleDelete(r); }} className="p-1.5 hover:bg-red-50 rounded-lg" title="Delete"><Trash2 className="w-4 h-4 text-red-500" /></button>
        </div>
      ),
    },
  ];

  const memberColumns = [
    { key: 'user_id', header: 'User ID', render: (r: any) => <span className="font-medium">{r.user_id}</span> },
    { key: 'user_email', header: 'Email', render: (r: any) => r.user_email || <span className="text-gray-400">—</span> },
    { key: 'user_role', header: 'Team Role' },
    { key: 'actions', header: '', render: (r: any) => (
      <button onClick={() => handleRemoveMember(r.user_id)} className="p-1.5 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4 text-red-500" /></button>
    ) },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Teams</h1>
          <p className="text-sm text-gray-500 mt-1">Manage teams, members, budgets, and rate limits</p>
          <p className="text-xs text-gray-400 mt-1">Create or edit teams with inherited or restricted callable-target access under their organization.</p>
        </div>
        <button onClick={() => { resetForm(); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Create Team
        </button>
      </div>
      {pageError && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{pageError}</div>
      )}
      <Card>
        <div className="px-4 pt-3 pb-2">
          <input value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder="Search teams..." className="w-full sm:w-72 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
        <DataTable columns={columns} data={items} loading={loading} emptyMessage="No teams created yet" onRowClick={(r) => navigate(`/teams/${r.team_id}`)} pagination={pagination} onPageChange={setPageOffset} />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={() => { setShowCreate(false); setEditItem(null); resetForm(); setError(null); }} title={editItem ? 'Edit Team' : 'Create Team'}>
        <div className="space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team Name</label>
            <input value={form.team_alias} onChange={(e) => setForm({ ...form, team_alias: e.target.value })} placeholder="Engineering" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Organization <span className="text-red-500">*</span></label>
              <select value={form.organization_id} onChange={(e) => handleOrganizationChange(e.target.value)} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                <option value="">Select an organization</option>
                {(orgList || []).map((org: any) => (
                  <option key={org.organization_id} value={org.organization_id}>{org.organization_name || org.organization_id}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
              <input type="number" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input type="number" value={form.rpm_limit} onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })} placeholder="100" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              <p className="text-xs text-gray-400 mt-1">Requests per minute</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input type="number" value={form.tpm_limit} onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })} placeholder="100000" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              <p className="text-xs text-gray-400 mt-1">Tokens per minute</p>
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
            <button onClick={() => { setShowCreate(false); setEditItem(null); resetForm(); setError(null); }} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSave} disabled={!form.organization_id || saving || assetAccessLoading} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">{saving ? 'Saving...' : editItem ? 'Save Changes' : 'Create Team'}</button>
          </div>
        </div>
      </Modal>

      <Modal open={!!selectedTeam} onClose={() => setSelectedTeam(null)} title={`Team: ${selectedTeam?.team_alias || selectedTeam?.team_id || ''}`} wide>
        <div>
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-semibold text-gray-700">Members</h4>
          </div>
          <div className="mb-4 space-y-3">
            <UserSearchSelect
              search={memberSearch}
              onSearchChange={setMemberSearch}
              options={(memberCandidates || []) as any[]}
              loading={memberCandidatesLoading}
              selectedAccountId={memberForm.user_id}
              onSelect={(a: any) => setMemberForm({ ...memberForm, user_id: a.account_id, user_email: a.email || '' })}
              searchPlaceholder="Search by email or account ID"
              helperText="Results include users that already belong to this team's organization."
              emptyText="No organization members match your search."
            />
            <div className="flex flex-col sm:flex-row gap-2">
              <select value={memberForm.user_role} onChange={(e) => setMemberForm({ ...memberForm, user_role: e.target.value })} className="sm:w-56 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="team_viewer">Viewer</option>
                <option value="team_developer">Developer</option>
                <option value="team_admin">Admin</option>
              </select>
              <button onClick={handleAddMember} disabled={!memberForm.user_id.trim()} className="flex items-center justify-center gap-1 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                <UserPlus className="w-4 h-4" /> Add
              </button>
            </div>
          </div>
          <p className="text-xs text-gray-400 mb-4">Authorization and scope are managed via RBAC memberships.</p>
          <DataTable columns={memberColumns} data={members || []} emptyMessage="No members in this team" />
        </div>
      </Modal>
    </div>
  );
}
