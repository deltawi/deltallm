import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { teams } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import { ArrowLeft, UsersRound, Users, DollarSign, Gauge, Box, Pencil, UserPlus, Trash2 } from 'lucide-react';

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

  const [showEdit, setShowEdit] = useState(false);
  const [form, setForm] = useState({ team_alias: '', organization_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });
  const [showAddMember, setShowAddMember] = useState(false);
  const [memberForm, setMemberForm] = useState({ user_id: '', user_email: '', user_role: 'internal_user' });
  const [saving, setSaving] = useState(false);

  const openEdit = () => {
    if (!team) return;
    setForm({
      team_alias: team.team_alias || '',
      organization_id: team.organization_id || '',
      max_budget: team.max_budget != null ? String(team.max_budget) : '',
      rpm_limit: team.rpm_limit != null ? String(team.rpm_limit) : '',
      tpm_limit: team.tpm_limit != null ? String(team.tpm_limit) : '',
      models: (team.models || []).join(', '),
    });
    setShowEdit(true);
  };

  const handleSaveTeam = async () => {
    setSaving(true);
    try {
      await teams.update(teamId!, {
        team_alias: form.team_alias || undefined,
        organization_id: form.organization_id || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        models: form.models ? form.models.split(',').map(m => m.trim()).filter(Boolean) : [],
      });
      setShowEdit(false);
      refetchTeam();
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
      setMemberForm({ user_id: '', user_email: '', user_role: 'internal_user' });
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

  const modelList = team.models?.length ? team.models : [];

  const memberColumns = [
    { key: 'user_id', header: 'User ID', render: (r: any) => <span className="font-medium font-mono text-xs">{r.user_id}</span> },
    { key: 'user_email', header: 'Email', render: (r: any) => r.user_email || <span className="text-gray-400">--</span> },
    { key: 'user_role', header: 'Role', render: (r: any) => (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700">{r.user_role}</span>
    ) },
    { key: 'spend', header: 'Spend', render: (r: any) => <span className="text-sm">${(r.spend || 0).toFixed(2)}</span> },
    { key: 'actions', header: '', render: (r: any) => (
      <button onClick={() => handleRemoveMember(r.user_id)} className="p-1.5 hover:bg-red-50 rounded-lg transition-colors" title="Remove member">
        <Trash2 className="w-4 h-4 text-red-500" />
      </button>
    ) },
  ];

  return (
    <div className="p-6 max-w-6xl">
      <div className="flex items-center gap-3 mb-6">
        <button onClick={() => navigate('/teams')} className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors">
          <ArrowLeft className="w-5 h-5 text-gray-500" />
        </button>
        <div className="flex-1">
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

      <div className="grid grid-cols-4 gap-4 mb-6">
        <StatCard icon={DollarSign} label="Spend" value={`$${(team.spend || 0).toFixed(2)}`} subValue={team.max_budget ? `of $${team.max_budget} budget` : 'No budget limit'} color="bg-green-50 text-green-600" />
        <StatCard icon={Users} label="Members" value={String(members?.length || 0)} color="bg-blue-50 text-blue-600" />
        <StatCard icon={Gauge} label="RPM Limit" value={team.rpm_limit != null ? team.rpm_limit.toLocaleString() : 'Unlimited'} subValue="Requests per minute" color="bg-purple-50 text-purple-600" />
        <StatCard icon={Box} label="Models" value={modelList.length ? String(modelList.length) : 'All'} subValue={modelList.length ? modelList.slice(0, 3).join(', ') : 'All models allowed'} color="bg-orange-50 text-orange-600" />
      </div>

      <Card
        title="Members"
        action={
          <button onClick={() => setShowAddMember(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
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
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team Name</label>
            <input value={form.team_alias} onChange={(e) => setForm({ ...form, team_alias: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Organization ID</label>
              <input value={form.organization_id} onChange={(e) => setForm({ ...form, organization_id: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
              <input type="number" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input type="number" value={form.rpm_limit} onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input type="number" value={form.tpm_limit} onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Allowed Models</label>
            <input value={form.models} onChange={(e) => setForm({ ...form, models: e.target.value })} placeholder="gpt-4o, claude-3" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            <p className="text-xs text-gray-400 mt-1">Comma-separated. Leave empty for all models.</p>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowEdit(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSaveTeam} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">Save Changes</button>
          </div>
        </div>
      </Modal>

      <Modal open={showAddMember} onClose={() => setShowAddMember(false)} title="Add Member">
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">User ID</label>
            <input value={memberForm.user_id} onChange={(e) => setMemberForm({ ...memberForm, user_id: e.target.value })} placeholder="user-abc123" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email (optional)</label>
            <input value={memberForm.user_email} onChange={(e) => setMemberForm({ ...memberForm, user_email: e.target.value })} placeholder="user@example.com" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
            <select value={memberForm.user_role} onChange={(e) => setMemberForm({ ...memberForm, user_role: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
              <option value="internal_user">Internal User</option>
              <option value="internal_user_viewer">Viewer</option>
              <option value="admin">Admin</option>
            </select>
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
