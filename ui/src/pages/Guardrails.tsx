import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { guardrails } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import { Plus, Pencil, Trash2, Shield } from 'lucide-react';

export default function Guardrails() {
  const { data, loading, refetch } = useApi(() => guardrails.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState({
    guardrail_name: '',
    guardrail: '',
    mode: 'pre_call',
    default_action: 'block',
    threshold: '0.5',
    enabled: true,
  });

  const handleSave = async () => {
    const payload = {
      guardrail_name: form.guardrail_name,
      litellm_params: {
        guardrail: form.guardrail,
        mode: form.mode,
        default_action: form.default_action,
        threshold: Number(form.threshold),
        enabled: form.enabled,
      },
    };
    if (editItem) {
      await guardrails.update(editItem.guardrail_name, payload);
    } else {
      await guardrails.create(payload);
    }
    setShowCreate(false);
    setEditItem(null);
    setForm({ guardrail_name: '', guardrail: '', mode: 'pre_call', default_action: 'block', threshold: '0.5', enabled: true });
    refetch();
  };

  const handleDelete = async (name: string) => {
    if (!confirm('Delete this guardrail?')) return;
    await guardrails.delete(name);
    refetch();
  };

  const openEdit = (row: any) => {
    setForm({
      guardrail_name: row.guardrail_name || '',
      guardrail: row.litellm_params?.guardrail || '',
      mode: row.mode || 'pre_call',
      default_action: row.default_action || 'block',
      threshold: String(row.threshold ?? 0.5),
      enabled: row.enabled !== false,
    });
    setEditItem(row);
  };

  const columns = [
    { key: 'guardrail_name', header: 'Name', render: (r: any) => (
      <div className="flex items-center gap-2">
        <Shield className="w-4 h-4 text-blue-500" />
        <span className="font-medium">{r.guardrail_name}</span>
      </div>
    ) },
    { key: 'type', header: 'Type', render: (r: any) => <span className="text-xs bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full">{r.type}</span> },
    { key: 'mode', header: 'Mode', render: (r: any) => <span className="text-xs bg-gray-100 px-2 py-0.5 rounded-full">{r.mode}</span> },
    { key: 'default_action', header: 'Action', render: (r: any) => <span className="text-xs">{r.default_action}</span> },
    { key: 'threshold', header: 'Threshold', render: (r: any) => r.threshold },
    { key: 'enabled', header: 'Status', render: (r: any) => <StatusBadge status={r.enabled ? 'enabled' : 'disabled'} /> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <div className="flex gap-1">
          <button onClick={() => openEdit(r)} className="p-1.5 hover:bg-gray-100 rounded-lg"><Pencil className="w-4 h-4 text-gray-500" /></button>
          <button onClick={() => handleDelete(r.guardrail_name)} className="p-1.5 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4 text-red-500" /></button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Guardrails</h1>
          <p className="text-sm text-gray-500 mt-1">Configure content safety and security policies</p>
        </div>
        <button onClick={() => { setForm({ guardrail_name: '', guardrail: '', mode: 'pre_call', default_action: 'block', threshold: '0.5', enabled: true }); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Add Guardrail
        </button>
      </div>
      <Card>
        <DataTable columns={columns} data={data || []} loading={loading} emptyMessage="No guardrails configured" />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={() => { setShowCreate(false); setEditItem(null); }} title={editItem ? 'Edit Guardrail' : 'Add Guardrail'}>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Guardrail Name</label>
            <input value={form.guardrail_name} onChange={(e) => setForm({ ...form, guardrail_name: e.target.value })} placeholder="pii-detection" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" disabled={!!editItem} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Guardrail Class</label>
            <input value={form.guardrail} onChange={(e) => setForm({ ...form, guardrail: e.target.value })} placeholder="src.guardrails.presidio.PresidioGuardrail" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Mode</label>
              <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="pre_call">Pre-call</option>
                <option value="post_call">Post-call</option>
                <option value="during_call">During call</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Action</label>
              <select value={form.default_action} onChange={(e) => setForm({ ...form, default_action: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="block">Block</option>
                <option value="warn">Warn</option>
                <option value="log">Log</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Threshold</label>
              <input type="number" step="0.1" min="0" max="1" value={form.threshold} onChange={(e) => setForm({ ...form, threshold: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} id="enabled" className="rounded" />
            <label htmlFor="enabled" className="text-sm text-gray-700">Enabled</label>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => { setShowCreate(false); setEditItem(null); }} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSave} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">{editItem ? 'Save Changes' : 'Create'}</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
