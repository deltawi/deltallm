import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { models } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import { Plus, Pencil, Trash2 } from 'lucide-react';

export default function Models() {
  const { data, loading, refetch } = useApi(() => models.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState({ model_name: '', model: '', api_key: '', api_base: '' });

  const handleCreate = async () => {
    await models.create({
      model_name: form.model_name,
      litellm_params: {
        model: form.model,
        api_key: form.api_key || undefined,
        api_base: form.api_base || undefined,
      },
    });
    setShowCreate(false);
    setForm({ model_name: '', model: '', api_key: '', api_base: '' });
    refetch();
  };

  const handleUpdate = async () => {
    if (!editItem) return;
    await models.update(editItem.deployment_id, {
      model_name: form.model_name,
      litellm_params: {
        model: form.model,
        api_key: form.api_key || undefined,
        api_base: form.api_base || undefined,
      },
    });
    setEditItem(null);
    setForm({ model_name: '', model: '', api_key: '', api_base: '' });
    refetch();
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this model?')) return;
    await models.delete(id);
    refetch();
  };

  const openEdit = (row: any) => {
    setForm({
      model_name: row.model_name || '',
      model: row.litellm_params?.model || '',
      api_key: row.litellm_params?.api_key || '',
      api_base: row.litellm_params?.api_base || '',
    });
    setEditItem(row);
  };

  const columns = [
    { key: 'model_name', header: 'Model Name', render: (r: any) => <span className="font-medium">{r.model_name}</span> },
    { key: 'provider', header: 'Provider', render: (r: any) => <span className="text-gray-500">{r.provider}</span> },
    { key: 'deployment_id', header: 'Deployment ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.deployment_id}</code> },
    { key: 'healthy', header: 'Health', render: (r: any) => <StatusBadge status={r.healthy ? 'healthy' : 'unhealthy'} /> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <div className="flex gap-1">
          <button onClick={() => openEdit(r)} className="p-1.5 hover:bg-gray-100 rounded-lg"><Pencil className="w-4 h-4 text-gray-500" /></button>
          <button onClick={() => handleDelete(r.deployment_id)} className="p-1.5 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4 text-red-500" /></button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Models</h1>
          <p className="text-sm text-gray-500 mt-1">Manage model deployments and providers</p>
        </div>
        <button onClick={() => { setForm({ model_name: '', model: '', api_key: '', api_base: '' }); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Add Model
        </button>
      </div>
      <Card>
        <DataTable columns={columns} data={data || []} loading={loading} emptyMessage="No models configured" />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={() => { setShowCreate(false); setEditItem(null); }} title={editItem ? 'Edit Model' : 'Add Model'}>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Model Name</label>
            <input value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })} placeholder="gpt-4o" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Provider/Model</label>
            <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="openai/gpt-4o" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
            <input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="sk-..." className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">API Base URL (optional)</label>
            <input value={form.api_base} onChange={(e) => setForm({ ...form, api_base: e.target.value })} placeholder="https://api.openai.com/v1" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => { setShowCreate(false); setEditItem(null); }} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={editItem ? handleUpdate : handleCreate} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">{editItem ? 'Save Changes' : 'Create'}</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
