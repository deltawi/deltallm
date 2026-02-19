import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { models } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import { Plus, Pencil, Trash2 } from 'lucide-react';

const emptyForm = {
  model_name: '',
  model: '',
  api_key: '',
  api_base: '',
  rpm: '',
  tpm: '',
  timeout: '',
  stream_timeout: '',
  max_tokens: '',
  input_cost_per_token: '',
  output_cost_per_token: '',
  max_context_window: '',
};

type FormState = typeof emptyForm;

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 pt-2">
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">{children}</span>
      <div className="flex-1 border-t border-gray-200" />
    </div>
  );
}

export default function Models() {
  const { data, loading, refetch } = useApi(() => models.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState<FormState>({ ...emptyForm });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const buildPayload = () => ({
    model_name: form.model_name,
    litellm_params: {
      model: form.model,
      api_key: form.api_key || undefined,
      api_base: form.api_base || undefined,
      rpm: form.rpm ? Number(form.rpm) : undefined,
      tpm: form.tpm ? Number(form.tpm) : undefined,
      timeout: form.timeout ? Number(form.timeout) : undefined,
      stream_timeout: form.stream_timeout ? Number(form.stream_timeout) : undefined,
      max_tokens: form.max_tokens ? Number(form.max_tokens) : undefined,
    },
    model_info: {
      input_cost_per_token: form.input_cost_per_token ? Number(form.input_cost_per_token) : undefined,
      output_cost_per_token: form.output_cost_per_token ? Number(form.output_cost_per_token) : undefined,
      max_tokens: form.max_context_window ? Number(form.max_context_window) : undefined,
    },
  });

  const handleCreate = async () => {
    setError(null);
    setSaving(true);
    try {
      await models.create(buildPayload());
      setShowCreate(false);
      setForm({ ...emptyForm });
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to create model');
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!editItem) return;
    setError(null);
    setSaving(true);
    try {
      await models.update(editItem.deployment_id, buildPayload());
      setEditItem(null);
      setForm({ ...emptyForm });
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to update model');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this model?')) return;
    try {
      await models.delete(id);
      refetch();
    } catch (err: any) {
      alert(err?.message || 'Failed to delete model');
    }
  };

  const openEdit = (row: any) => {
    const lp = row.litellm_params || {};
    const mi = row.model_info || {};
    setForm({
      model_name: row.model_name || '',
      model: lp.model || '',
      api_key: lp.api_key || '',
      api_base: lp.api_base || '',
      rpm: lp.rpm != null ? String(lp.rpm) : '',
      tpm: lp.tpm != null ? String(lp.tpm) : '',
      timeout: lp.timeout != null ? String(lp.timeout) : '',
      stream_timeout: lp.stream_timeout != null ? String(lp.stream_timeout) : '',
      max_tokens: lp.max_tokens != null ? String(lp.max_tokens) : '',
      input_cost_per_token: mi.input_cost_per_token != null ? String(mi.input_cost_per_token) : '',
      output_cost_per_token: mi.output_cost_per_token != null ? String(mi.output_cost_per_token) : '',
      max_context_window: mi.max_tokens != null ? String(mi.max_tokens) : '',
    });
    setError(null);
    setEditItem(row);
  };

  const closeModal = () => {
    setShowCreate(false);
    setEditItem(null);
    setError(null);
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

  const inputClass = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Models</h1>
          <p className="text-sm text-gray-500 mt-1">Manage model deployments and providers</p>
        </div>
        <button onClick={() => { setForm({ ...emptyForm }); setError(null); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Add Model
        </button>
      </div>
      <Card>
        <DataTable columns={columns} data={data || []} loading={loading} emptyMessage="No models configured" />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={closeModal} title={editItem ? 'Edit Model' : 'Add Model'}>
        <div className="space-y-4">
          <SectionLabel>Provider Connection</SectionLabel>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Model Name</label>
            <input value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })} placeholder="gpt-4o" className={inputClass} />
            <p className="text-xs text-gray-400 mt-1">Public name users will reference in API calls</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Provider / Model</label>
            <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="openai/gpt-4o" className={inputClass} />
            <p className="text-xs text-gray-400 mt-1">Format: provider/model-id (e.g. openai/gpt-4o, anthropic/claude-3-sonnet)</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
              <input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="sk-..." className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Base URL</label>
              <input value={form.api_base} onChange={(e) => setForm({ ...form, api_base: e.target.value })} placeholder="https://api.openai.com/v1" className={inputClass} />
            </div>
          </div>

          <SectionLabel>Rate Limits</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM</label>
              <input type="number" value={form.rpm} onChange={(e) => setForm({ ...form, rpm: e.target.value })} placeholder="e.g. 500" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Requests per minute to the provider</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM</label>
              <input type="number" value={form.tpm} onChange={(e) => setForm({ ...form, tpm: e.target.value })} placeholder="e.g. 100000" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Tokens per minute to the provider</p>
            </div>
          </div>

          <SectionLabel>Timeouts & Limits</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Timeout (s)</label>
              <input type="number" value={form.timeout} onChange={(e) => setForm({ ...form, timeout: e.target.value })} placeholder="e.g. 60" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Request timeout</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Stream Timeout (s)</label>
              <input type="number" value={form.stream_timeout} onChange={(e) => setForm({ ...form, stream_timeout: e.target.value })} placeholder="e.g. 120" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Streaming timeout</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
              <input type="number" value={form.max_tokens} onChange={(e) => setForm({ ...form, max_tokens: e.target.value })} placeholder="e.g. 4096" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Per-request limit</p>
            </div>
          </div>

          <SectionLabel>Cost Tracking</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token</label>
              <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="e.g. 0.000003" className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Output Cost / Token</label>
              <input type="number" step="any" value={form.output_cost_per_token} onChange={(e) => setForm({ ...form, output_cost_per_token: e.target.value })} placeholder="e.g. 0.000015" className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Context Window</label>
              <input type="number" value={form.max_context_window} onChange={(e) => setForm({ ...form, max_context_window: e.target.value })} placeholder="e.g. 128000" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Max context tokens</p>
            </div>
          </div>

          {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={closeModal} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={editItem ? handleUpdate : handleCreate} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : editItem ? 'Save Changes' : 'Create'}</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
