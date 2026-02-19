import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { keys, teams } from '../lib/api';
import { useAuth } from '../lib/auth';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import { Plus, RefreshCw, Trash2, Copy, Check, Pencil } from 'lucide-react';

function KeyStatus({ row }: { row: any }) {
  if (row.expires) {
    const exp = new Date(row.expires);
    if (exp < new Date()) return <StatusBadge status="expired" />;
  }
  return <StatusBadge status="active" />;
}

function maskKey(token: string) {
  if (!token) return '';
  return token.substring(0, 8) + '...' + token.substring(token.length - 4);
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

export default function ApiKeys() {
  const { session } = useAuth();
  const currentUserId = session?.account_id || '';
  const { data, loading, refetch } = useApi(() => keys.list(), []);
  const { data: teamsList } = useApi(() => teams.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [form, setForm] = useState({ key_name: '', user_id: '', team_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const handleCreate = async () => {
    setError(null);
    setSaving(true);
    try {
      const result = await keys.create({
        key_name: form.key_name || undefined,
        user_id: currentUserId || undefined,
        team_id: form.team_id || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        models: form.models ? form.models.split(',').map(m => m.trim()).filter(Boolean) : [],
      });
      setCreatedKey(result.raw_key);
      setForm({ key_name: '', user_id: '', team_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to create key');
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!editItem) return;
    setError(null);
    setSaving(true);
    try {
      await keys.update(editItem.token, {
        key_name: form.key_name || undefined,
        user_id: form.user_id || undefined,
        team_id: form.team_id || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        models: form.models ? form.models.split(',').map(m => m.trim()).filter(Boolean) : [],
      });
      setEditItem(null);
      setForm({ key_name: '', user_id: '', team_id: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to update key');
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (row: any) => {
    setForm({
      key_name: row.key_name || '',
      user_id: row.user_id || '',
      team_id: row.team_id || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      models: (row.models || []).join(', '),
    });
    setEditItem(row);
  };

  const handleRevoke = async (hash: string) => {
    if (!confirm('Are you sure you want to revoke this key?')) return;
    await keys.revoke(hash);
    refetch();
  };

  const handleRegenerate = async (hash: string) => {
    if (!confirm('Regenerate this key? The old key will stop working.')) return;
    const result = await keys.regenerate(hash);
    setCreatedKey(result.raw_key);
    refetch();
  };

  const copyKey = () => {
    if (createdKey) {
      navigator.clipboard.writeText(createdKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const columns = [
    { key: 'key_name', header: 'Name', render: (r: any) => <span className="font-medium">{r.key_name || '(unnamed)'}</span> },
    { key: 'token', header: 'Token', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{maskKey(r.token)}</code> },
    { key: 'status', header: 'Status', render: (r: any) => <KeyStatus row={r} /> },
    { key: 'budget', header: 'Budget', render: (r: any) => <BudgetBar spend={r.spend || 0} max_budget={r.max_budget} /> },
    { key: 'rpm_limit', header: 'RPM', render: (r: any) => r.rpm_limit != null ? <span className="text-xs font-medium">{Number(r.rpm_limit).toLocaleString()}</span> : <span className="text-gray-400 text-xs">No limit</span> },
    { key: 'tpm_limit', header: 'TPM', render: (r: any) => r.tpm_limit != null ? <span className="text-xs font-medium">{Number(r.tpm_limit).toLocaleString()}</span> : <span className="text-gray-400 text-xs">No limit</span> },
    { key: 'models', header: 'Models', render: (r: any) => r.models?.length ? <span className="text-xs">{r.models.join(', ')}</span> : <span className="text-gray-400 text-xs">All</span> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <div className="flex gap-1">
          <button onClick={() => openEdit(r)} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Edit"><Pencil className="w-4 h-4 text-gray-500" /></button>
          <button onClick={() => handleRegenerate(r.token)} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Regenerate"><RefreshCw className="w-4 h-4 text-gray-500" /></button>
          <button onClick={() => handleRevoke(r.token)} className="p-1.5 hover:bg-red-50 rounded-lg" title="Revoke"><Trash2 className="w-4 h-4 text-red-500" /></button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">API Keys</h1>
          <p className="text-sm text-gray-500 mt-1">Manage API keys for accessing the proxy</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Create Key
        </button>
      </div>
      <Card>
        <DataTable columns={columns} data={data || []} loading={loading} emptyMessage="No API keys created yet" />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={() => { setShowCreate(false); setEditItem(null); }} title={editItem ? 'Edit API Key' : 'Create API Key'}>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Key Name</label>
            <input value={form.key_name} onChange={(e) => setForm({ ...form, key_name: e.target.value })} placeholder="my-key" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">User ID</label>
              <input value={editItem ? form.user_id : (currentUserId || '')} readOnly={!editItem} className={`w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${!editItem ? 'bg-gray-50 text-gray-500 cursor-not-allowed' : ''}`} onChange={editItem ? (e) => setForm({ ...form, user_id: e.target.value }) : undefined} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Team</label>
              <select value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                <option value="">No team</option>
                {form.team_id && !(teamsList || []).some((t: any) => t.team_id === form.team_id) && (
                  <option value={form.team_id} disabled>{form.team_id} (inaccessible)</option>
                )}
                {(teamsList || []).map((t: any) => (
                  <option key={t.team_id} value={t.team_id}>{t.team_alias || t.team_id}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
              <input type="number" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
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
            <label className="block text-sm font-medium text-gray-700 mb-1">Allowed Models (comma-separated)</label>
            <input value={form.models} onChange={(e) => setForm({ ...form, models: e.target.value })} placeholder="gpt-4o, claude-3-sonnet" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => { setShowCreate(false); setEditItem(null); setError(null); }} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={editItem ? handleUpdate : handleCreate} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : editItem ? 'Save Changes' : 'Create Key'}</button>
          </div>
        </div>
      </Modal>

      <Modal open={!!createdKey} onClose={() => setCreatedKey(null)} title="API Key Created">
        <div>
          <p className="text-sm text-gray-600 mb-3">Copy your API key now. You won't be able to see it again.</p>
          <div className="flex items-center gap-2 bg-gray-50 border rounded-lg p-3">
            <code className="flex-1 text-sm break-all">{createdKey}</code>
            <button onClick={copyKey} className="p-2 hover:bg-gray-200 rounded-lg transition-colors">
              {copied ? <Check className="w-4 h-4 text-green-600" /> : <Copy className="w-4 h-4 text-gray-500" />}
            </button>
          </div>
          <div className="flex justify-end mt-4">
            <button onClick={() => setCreatedKey(null)} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">Done</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
