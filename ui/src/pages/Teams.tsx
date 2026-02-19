import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { teams, organizations } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
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
  const { data, loading, refetch } = useApi(() => teams.list(), []);
  const { data: orgList } = useApi(() => organizations.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [selectedTeam, setSelectedTeam] = useState<any>(null);
  const [form, setForm] = useState({ team_alias: '', organization_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });
  const [memberForm, setMemberForm] = useState({ user_id: '', user_email: '', user_role: 'internal_user' });

  const { data: members, refetch: refetchMembers } = useApi(
    () => selectedTeam ? teams.members(selectedTeam.team_id) : Promise.resolve([]),
    [selectedTeam?.team_id]
  );

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resetForm = () => setForm({ team_alias: '', organization_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });

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
        models: form.models ? form.models.split(',').map(m => m.trim()).filter(Boolean) : [],
      };
      if (editItem) {
        await teams.update(editItem.team_id, payload);
      } else {
        await teams.create(payload);
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
    setForm({
      team_alias: row.team_alias || '',
      organization_id: row.organization_id || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      models: (row.models || []).join(', '),
    });
    setEditItem(row);
  };

  const handleAddMember = async () => {
    if (!selectedTeam) return;
    await teams.addMember(selectedTeam.team_id, {
      user_id: memberForm.user_id,
      user_email: memberForm.user_email || undefined,
      user_role: memberForm.user_role,
    });
    setMemberForm({ user_id: '', user_email: '', user_role: 'internal_user' });
    refetchMembers();
  };

  const handleRemoveMember = async (userId: string) => {
    if (!selectedTeam || !confirm('Remove this member from the team?')) return;
    await teams.removeMember(selectedTeam.team_id, userId);
    refetchMembers();
  };

  const columns = [
    { key: 'team_alias', header: 'Name', render: (r: any) => <span className="font-medium">{r.team_alias || r.team_id}</span> },
    { key: 'team_id', header: 'Team ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.team_id}</code> },
    { key: 'member_count', header: 'Members', render: (r: any) => <span className="flex items-center gap-1"><Users className="w-3.5 h-3.5 text-gray-400" /> {r.member_count || 0}</span> },
    { key: 'budget', header: 'Budget', render: (r: any) => <BudgetBar spend={r.spend || 0} max_budget={r.max_budget} /> },
    { key: 'rpm_limit', header: 'RPM', render: (r: any) => <RateLimit value={r.rpm_limit} unit="req/min" /> },
    { key: 'tpm_limit', header: 'TPM', render: (r: any) => <RateLimit value={r.tpm_limit} unit="tok/min" /> },
    { key: 'models', header: 'Models', render: (r: any) => r.models?.length ? <span className="text-xs">{r.models.join(', ')}</span> : <span className="text-gray-400 text-xs">All</span> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <div className="flex gap-1">
          <button onClick={(e) => { e.stopPropagation(); openEdit(r); }} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Edit"><Pencil className="w-4 h-4 text-gray-500" /></button>
          <button onClick={(e) => { e.stopPropagation(); setSelectedTeam(r); }} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Members"><Users className="w-4 h-4 text-gray-500" /></button>
        </div>
      ),
    },
  ];

  const memberColumns = [
    { key: 'user_id', header: 'User ID', render: (r: any) => <span className="font-medium">{r.user_id}</span> },
    { key: 'user_email', header: 'Email', render: (r: any) => r.user_email || <span className="text-gray-400">—</span> },
    { key: 'user_role', header: 'Role' },
    { key: 'actions', header: '', render: (r: any) => (
      <button onClick={() => handleRemoveMember(r.user_id)} className="p-1.5 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4 text-red-500" /></button>
    ) },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Teams</h1>
          <p className="text-sm text-gray-500 mt-1">Manage teams, members, and rate limits</p>
        </div>
        <button onClick={() => { resetForm(); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Create Team
        </button>
      </div>
      <Card>
        <DataTable columns={columns} data={data || []} loading={loading} emptyMessage="No teams created yet" onRowClick={(r) => navigate(`/teams/${r.team_id}`)} />
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
              <select value={form.organization_id} onChange={(e) => setForm({ ...form, organization_id: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
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
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Allowed Models</label>
            <input value={form.models} onChange={(e) => setForm({ ...form, models: e.target.value })} placeholder="gpt-4o, claude-3" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => { setShowCreate(false); setEditItem(null); resetForm(); setError(null); }} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSave} disabled={!form.organization_id || saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">{saving ? 'Saving...' : editItem ? 'Save Changes' : 'Create Team'}</button>
          </div>
        </div>
      </Modal>

      <Modal open={!!selectedTeam} onClose={() => setSelectedTeam(null)} title={`Team: ${selectedTeam?.team_alias || selectedTeam?.team_id || ''}`} wide>
        <div>
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-semibold text-gray-700">Members</h4>
          </div>
          <div className="flex gap-2 mb-4">
            <input value={memberForm.user_id} onChange={(e) => setMemberForm({ ...memberForm, user_id: e.target.value })} placeholder="User ID" className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            <input value={memberForm.user_email} onChange={(e) => setMemberForm({ ...memberForm, user_email: e.target.value })} placeholder="Email (optional)" className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            <button onClick={handleAddMember} className="flex items-center gap-1 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 transition-colors">
              <UserPlus className="w-4 h-4" /> Add
            </button>
          </div>
          <DataTable columns={memberColumns} data={members || []} emptyMessage="No members in this team" />
        </div>
      </Modal>
    </div>
  );
}
