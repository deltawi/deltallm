import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { users } from '../lib/api';
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
  const { data, loading, refetch } = useApi(() => users.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState({ user_id: '', user_email: '', user_role: 'internal_user', team_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });

  const resetForm = () => setForm({ user_id: '', user_email: '', user_role: 'internal_user', team_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });

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
          <button onClick={() => openEdit(r)} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Edit"><Pencil className="w-4 h-4 text-gray-500" /></button>
          <button onClick={() => toggleBlock(r.user_id, r.blocked)} className="p-1.5 hover:bg-gray-100 rounded-lg" title={r.blocked ? 'Unblock' : 'Block'}>
            {r.blocked ? <CheckCircle className="w-4 h-4 text-green-500" /> : <Ban className="w-4 h-4 text-red-500" />}
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
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
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
              <select value={form.user_role} onChange={(e) => setForm({ ...form, user_role: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="internal_user">Internal User</option>
                <option value="admin">Admin</option>
                <option value="user">User</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Team ID</label>
              <input value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
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
