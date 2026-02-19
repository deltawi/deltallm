import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { users, teams } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import { Plus, Ban, CheckCircle, Pencil } from 'lucide-react';

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

export default function UsersPage() {
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin' || userRole === 'platform_co_admin';
  const { data, loading, refetch } = useApi(() => users.list(), []);
  const { data: teamsList } = useApi(() => teams.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [teamSearch, setTeamSearch] = useState('');
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState({ user_id: '', user_email: '', user_role: 'internal_user', team_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });

  const resetForm = () => { setForm({ user_id: '', user_email: '', user_role: 'internal_user', team_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' }); setTeamSearch(''); };

  const handleSave = async () => {
    const payload = {
      user_id: form.user_id,
      user_email: form.user_email || undefined,
      user_role: form.user_role,
      team_id: form.team_id || undefined,
      max_budget: form.max_budget ? Number(form.max_budget) : undefined,
      rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
      tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
      models: form.models ? form.models.split(',').map(m => m.trim()).filter(Boolean) : [],
    };
    if (editItem) {
      await users.update(editItem.user_id, payload);
    } else {
      await users.create(payload);
    }
    setShowCreate(false);
    setEditItem(null);
    resetForm();
    refetch();
  };

  const openEdit = (row: any) => {
    const matchedTeam = (teamsList || []).find((t: any) => t.team_id === row.team_id);
    setTeamSearch(matchedTeam ? (matchedTeam.team_alias || matchedTeam.team_id) : (row.team_id || ''));
    setForm({
      user_id: row.user_id || '',
      user_email: row.user_email || '',
      user_role: row.user_role || 'internal_user',
      team_id: row.team_id || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      models: (row.models || []).join(', '),
    });
    setEditItem(row);
  };

  const toggleBlock = async (userId: string, currentBlocked: boolean) => {
    await users.block(userId, !currentBlocked);
    refetch();
  };

  const columns = [
    { key: 'user_id', header: 'User ID', render: (r: any) => <span className="font-medium">{r.user_id}</span> },
    { key: 'user_email', header: 'Email', render: (r: any) => r.user_email || <span className="text-gray-400">—</span> },
    { key: 'user_role', header: 'Role', render: (r: any) => <span className="text-xs bg-gray-100 px-2 py-0.5 rounded-full">{r.user_role}</span> },
    { key: 'team_id', header: 'Team', render: (r: any) => r.team_id || <span className="text-gray-400">—</span> },
    { key: 'budget', header: 'Budget', render: (r: any) => <BudgetBar spend={r.spend || 0} max_budget={r.max_budget} /> },
    { key: 'rpm_limit', header: 'RPM', render: (r: any) => <RateLimit value={r.rpm_limit} unit="req/min" /> },
    { key: 'tpm_limit', header: 'TPM', render: (r: any) => <RateLimit value={r.tpm_limit} unit="tok/min" /> },
    { key: 'status', header: 'Status', render: (r: any) => <StatusBadge status={r.blocked ? 'blocked' : 'active'} /> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <div className="flex gap-1">
          {isPlatformAdmin && (
            <button onClick={() => openEdit(r)} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Edit"><Pencil className="w-4 h-4 text-gray-500" /></button>
          )}
          {isPlatformAdmin && (
            <button onClick={() => toggleBlock(r.user_id, r.blocked)} className="p-1.5 hover:bg-gray-100 rounded-lg" title={r.blocked ? 'Unblock' : 'Block'}>
              {r.blocked ? <CheckCircle className="w-4 h-4 text-green-500" /> : <Ban className="w-4 h-4 text-red-500" />}
            </button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Users</h1>
          <p className="text-sm text-gray-500 mt-1">Manage users, permissions, and rate limits</p>
        </div>
        <button onClick={() => { resetForm(); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Add User
        </button>
      </div>
      <Card>
        <DataTable columns={columns} data={data || []} loading={loading} emptyMessage="No users found" />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={() => { setShowCreate(false); setEditItem(null); resetForm(); }} title={editItem ? 'Edit User' : 'Add User'}>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">User ID</label>
            <input value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} placeholder="user-123" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" disabled={!!editItem} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input value={form.user_email} onChange={(e) => setForm({ ...form, user_email: e.target.value })} placeholder="user@example.com" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
              <select value={form.user_role} onChange={(e) => setForm({ ...form, user_role: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="internal_user">Internal User</option>
                <option value="admin">Admin</option>
                <option value="user">User</option>
              </select>
            </div>
            <div className="relative">
              <label className="block text-sm font-medium text-gray-700 mb-1">Team</label>
              <input
                value={teamSearch}
                onChange={(e) => { setTeamSearch(e.target.value); if (!e.target.value) setForm({ ...form, team_id: '' }); }}
                onFocus={() => setTeamSearch(teamSearch || (teamsList || []).find((t: any) => t.team_id === form.team_id)?.team_alias || '')}
                placeholder="Search teams..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {teamSearch && !form.team_id && (teamsList || []).filter((t: any) => (t.team_alias || t.team_id).toLowerCase().includes(teamSearch.toLowerCase())).length > 0 && (
                <div className="absolute z-10 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-40 overflow-y-auto">
                  {(teamsList || []).filter((t: any) => (t.team_alias || t.team_id).toLowerCase().includes(teamSearch.toLowerCase())).map((t: any) => (
                    <button key={t.team_id} type="button" onClick={() => { setForm({ ...form, team_id: t.team_id }); setTeamSearch(t.team_alias || t.team_id); }} className="w-full text-left px-3 py-2 text-sm hover:bg-blue-50 transition-colors">
                      <span className="font-medium">{t.team_alias || t.team_id}</span>
                      <span className="text-gray-400 ml-2 text-xs">{t.team_id}</span>
                    </button>
                  ))}
                </div>
              )}
              {form.team_id && (
                <button type="button" onClick={() => { setForm({ ...form, team_id: '' }); setTeamSearch(''); }} className="absolute right-2 top-8 text-gray-400 hover:text-gray-600 text-xs">clear</button>
              )}
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
              <input type="number" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
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
            <button onClick={() => { setShowCreate(false); setEditItem(null); resetForm(); }} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSave} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">{editItem ? 'Save Changes' : 'Add User'}</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
