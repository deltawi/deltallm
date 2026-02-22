import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { guardrails, organizations, teams, keys } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import ScopedGuardrailEditor from '../components/ScopedGuardrailEditor';
import { Plus, Pencil, Trash2, Shield, Building2, Users, Key } from 'lucide-react';

type ScopeTarget = { scope: 'organization' | 'team' | 'key'; id: string; label: string } | null;

export default function Guardrails() {
  const { data, loading, refetch } = useApi(() => guardrails.list(), []);
  const { data: orgList } = useApi(() => organizations.list(), []);
  const { data: teamList } = useApi(() => teams.list(), []);
  const { data: keyList } = useApi(() => keys.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [scopeTarget, setScopeTarget] = useState<ScopeTarget>(null);
  const [form, setForm] = useState({
    guardrail_name: '',
    guardrail: '',
    mode: 'pre_call',
    default_action: 'block',
    threshold: '0.5',
    default_on: true,
  });

  const saveAll = async (updated: any[]) => {
    const payload = {
      guardrails: updated.map((g: any) => ({
        guardrail_name: g.guardrail_name,
        litellm_params: g.litellm_params || {
          guardrail: g.guardrail,
          mode: g.mode,
          default_action: g.default_action,
          threshold: g.threshold,
          default_on: g.default_on,
        },
      })),
    };
    await guardrails.update(payload);
    refetch();
  };

  const handleSave = async () => {
    const current = data || [];
    const newItem = {
      guardrail_name: form.guardrail_name,
      litellm_params: {
        guardrail: form.guardrail,
        mode: form.mode,
        default_action: form.default_action,
        threshold: Number(form.threshold),
        default_on: form.default_on,
      },
    };

    let updated;
    if (editItem) {
      updated = current.map((g: any) =>
        g.guardrail_name === editItem.guardrail_name ? newItem : g
      );
    } else {
      updated = [...current, newItem];
    }
    await saveAll(updated);
    setShowCreate(false);
    setEditItem(null);
    setForm({ guardrail_name: '', guardrail: '', mode: 'pre_call', default_action: 'block', threshold: '0.5', default_on: true });
  };

  const handleDelete = async (name: string) => {
    if (!confirm('Delete this guardrail?')) return;
    const updated = (data || []).filter((g: any) => g.guardrail_name !== name);
    await saveAll(updated);
  };

  const openEdit = (row: any) => {
    const params = row.litellm_params || {};
    setForm({
      guardrail_name: row.guardrail_name || '',
      guardrail: params.guardrail || '',
      mode: params.mode || 'pre_call',
      default_action: params.default_action || 'block',
      threshold: String(params.threshold ?? 0.5),
      default_on: params.default_on !== false,
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
    { key: 'type', header: 'Class', render: (r: any) => <span className="text-xs bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full">{r.litellm_params?.guardrail || '—'}</span> },
    { key: 'mode', header: 'Mode', render: (r: any) => <span className="text-xs bg-gray-100 px-2 py-0.5 rounded-full">{r.litellm_params?.mode || '—'}</span> },
    { key: 'default_action', header: 'Action', render: (r: any) => <span className="text-xs">{r.litellm_params?.default_action || '—'}</span> },
    { key: 'threshold', header: 'Threshold', render: (r: any) => r.litellm_params?.threshold ?? '—' },
    { key: 'enabled', header: 'Status', render: (r: any) => <StatusBadge status={r.litellm_params?.default_on !== false ? 'enabled' : 'disabled'} /> },
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
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Guardrails</h1>
          <p className="text-sm text-gray-500 mt-1">Configure content safety and security policies</p>
        </div>
        <button onClick={() => { setForm({ guardrail_name: '', guardrail: '', mode: 'pre_call', default_action: 'block', threshold: '0.5', default_on: true }); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
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
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
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
            <input type="checkbox" checked={form.default_on} onChange={(e) => setForm({ ...form, default_on: e.target.checked })} id="default_on" className="rounded" />
            <label htmlFor="default_on" className="text-sm text-gray-700">Enabled by default</label>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => { setShowCreate(false); setEditItem(null); }} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSave} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">{editItem ? 'Save Changes' : 'Create'}</button>
          </div>
        </div>
      </Modal>

      <div className="mt-8">
        <h2 className="text-lg font-semibold text-gray-900 mb-1">Scoped Assignments</h2>
        <p className="text-sm text-gray-500 mb-4">Assign guardrails at the organization, team, or API key level. Scoped assignments use hierarchical resolution: Global &rarr; Organization &rarr; Team &rarr; API Key.</p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <Card>
            <div className="p-4">
              <div className="flex items-center gap-2 mb-3">
                <Building2 className="w-4 h-4 text-indigo-600" />
                <h3 className="font-medium text-sm text-gray-900">Organizations</h3>
              </div>
              {(orgList || []).length === 0 ? (
                <p className="text-xs text-gray-400">No organizations</p>
              ) : (
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {(orgList || []).map((org: any) => (
                    <button key={org.organization_id} onClick={() => setScopeTarget({ scope: 'organization', id: org.organization_id, label: org.organization_alias || org.organization_id })} className={`w-full text-left px-3 py-2 rounded-lg text-sm hover:bg-indigo-50 transition-colors ${scopeTarget?.scope === 'organization' && scopeTarget?.id === org.organization_id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-700'}`}>
                      {org.organization_alias || org.organization_id}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <Card>
            <div className="p-4">
              <div className="flex items-center gap-2 mb-3">
                <Users className="w-4 h-4 text-emerald-600" />
                <h3 className="font-medium text-sm text-gray-900">Teams</h3>
              </div>
              {(teamList || []).length === 0 ? (
                <p className="text-xs text-gray-400">No teams</p>
              ) : (
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {(teamList || []).map((team: any) => (
                    <button key={team.team_id} onClick={() => setScopeTarget({ scope: 'team', id: team.team_id, label: team.team_alias || team.team_id })} className={`w-full text-left px-3 py-2 rounded-lg text-sm hover:bg-emerald-50 transition-colors ${scopeTarget?.scope === 'team' && scopeTarget?.id === team.team_id ? 'bg-emerald-50 text-emerald-700 font-medium' : 'text-gray-700'}`}>
                      {team.team_alias || team.team_id}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <Card>
            <div className="p-4">
              <div className="flex items-center gap-2 mb-3">
                <Key className="w-4 h-4 text-amber-600" />
                <h3 className="font-medium text-sm text-gray-900">API Keys</h3>
              </div>
              {(keyList || []).length === 0 ? (
                <p className="text-xs text-gray-400">No API keys</p>
              ) : (
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {(keyList || []).map((k: any) => (
                    <button key={k.token} onClick={() => setScopeTarget({ scope: 'key', id: k.token, label: k.key_name || k.key_alias || k.token?.slice(0, 12) })} className={`w-full text-left px-3 py-2 rounded-lg text-sm hover:bg-amber-50 transition-colors ${scopeTarget?.scope === 'key' && scopeTarget?.id === k.token ? 'bg-amber-50 text-amber-700 font-medium' : 'text-gray-700'}`}>
                      {k.key_name || k.key_alias || k.token?.slice(0, 12)}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>
        </div>

        {scopeTarget && (
          <ScopedGuardrailEditor
            key={`${scopeTarget.scope}-${scopeTarget.id}`}
            scope={scopeTarget.scope}
            entityId={scopeTarget.id}
            entityLabel={scopeTarget.label}
            onClose={() => setScopeTarget(null)}
          />
        )}
      </div>
    </div>
  );
}
