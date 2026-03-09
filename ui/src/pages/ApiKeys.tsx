import { useState, useEffect } from 'react';
import { useApi } from '../lib/hooks';
import { keys, serviceAccounts, teams } from '../lib/api';
import type { ApiKey, ServiceAccount } from '../lib/api';
import { useAuth } from '../lib/auth';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import { Plus, RefreshCw, Trash2, Copy, Check, Pencil } from 'lucide-react';

type OwnerMode = 'self' | 'service_account';

type KeyFormState = {
  key_name: string;
  team_id: string;
  owner_mode: OwnerMode;
  owner_service_account_id: string;
  max_budget: string;
  rpm_limit: string;
  tpm_limit: string;
  models: string;
};

const EMPTY_PAGINATION = { total: 0, limit: 200, offset: 0, has_more: false };

function emptyForm(): KeyFormState {
  return {
    key_name: '',
    team_id: '',
    owner_mode: 'self',
    owner_service_account_id: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    models: '',
  };
}

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
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 10;
  const { data: result, loading, refetch } = useApi(() => keys.list({ search, limit: pageSize, offset: pageOffset }), [search, pageOffset]);
  const items = result?.data || [];
  const pagination = result?.pagination;
  const { data: teamsResult } = useApi(() => teams.list({ limit: 500 }), []);
  const teamsList = teamsResult?.data || [];
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<ApiKey | null>(null);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [form, setForm] = useState<KeyFormState>(() => emptyForm());
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [newServiceAccountName, setNewServiceAccountName] = useState('');
  const [creatingServiceAccount, setCreatingServiceAccount] = useState(false);
  const selectedTeamId = form.team_id;
  const { data: serviceAccountsResult, loading: serviceAccountsLoading, refetch: refetchServiceAccounts } = useApi(
    () => (
      selectedTeamId
        ? serviceAccounts.list({ team_id: selectedTeamId, limit: 200 })
        : Promise.resolve({ data: [] as ServiceAccount[], pagination: EMPTY_PAGINATION })
    ),
    [selectedTeamId]
  );
  const availableServiceAccounts = serviceAccountsResult?.data || [];
  const hasServiceAccounts = availableServiceAccounts.length > 0;

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPageOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    if (form.owner_mode !== 'service_account' && form.owner_service_account_id) {
      setForm((current) => ({ ...current, owner_service_account_id: '' }));
    }
  }, [form.owner_mode, form.owner_service_account_id]);

  useEffect(() => {
    if (!form.owner_service_account_id) return;
    const stillAvailable = availableServiceAccounts.some((item) => item.service_account_id === form.owner_service_account_id);
    if (!stillAvailable) {
      setForm((current) => ({ ...current, owner_service_account_id: '' }));
    }
  }, [availableServiceAccounts, form.owner_service_account_id]);

  const closeEditor = () => {
    setShowCreate(false);
    setEditItem(null);
    setError(null);
    setSaving(false);
    setCreatingServiceAccount(false);
    setNewServiceAccountName('');
    setForm(emptyForm());
  };

  const openCreate = () => {
    setError(null);
    setEditItem(null);
    setNewServiceAccountName('');
    setForm(emptyForm());
    setShowCreate(true);
  };

  const handleCreateServiceAccount = async () => {
    if (!form.team_id) {
      setError('Select a team before creating a service account.');
      return;
    }
    if (!newServiceAccountName.trim()) {
      setError('Enter a name for the service account.');
      return;
    }

    setError(null);
    setCreatingServiceAccount(true);
    try {
      const created = await serviceAccounts.create({
        team_id: form.team_id,
        name: newServiceAccountName.trim(),
      });
      await refetchServiceAccounts();
      setForm((current) => ({
        ...current,
        owner_mode: 'service_account',
        owner_service_account_id: created.service_account_id,
      }));
      setNewServiceAccountName('');
    } catch (err: any) {
      setError(err?.message || 'Failed to create service account');
    } finally {
      setCreatingServiceAccount(false);
    }
  };

  const handleCreate = async () => {
    setError(null);
    setSaving(true);
    try {
      if (!form.key_name.trim()) {
        setError('Enter a key name before creating a key.');
        return;
      }
      if (!form.team_id) {
        setError('Select a team before creating a key.');
        return;
      }
      if (form.owner_mode === 'service_account' && !form.owner_service_account_id) {
        setError('Select a service account or switch ownership to You.');
        return;
      }
      const result = await keys.create({
        key_name: form.key_name.trim(),
        team_id: form.team_id || undefined,
        owner_account_id: form.owner_mode === 'self' ? currentUserId || undefined : undefined,
        owner_service_account_id: form.owner_mode === 'service_account' ? form.owner_service_account_id || undefined : undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        models: form.models ? form.models.split(',').map(m => m.trim()).filter(Boolean) : [],
      });
      setCreatedKey(result.raw_key);
      closeEditor();
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
      if (!form.key_name.trim()) {
        setError('Enter a key name before saving changes.');
        return;
      }
      if (!form.team_id) {
        setError('Select a team before saving changes.');
        return;
      }
      if (form.owner_mode === 'service_account' && !form.owner_service_account_id) {
        setError('Select a service account or switch ownership to You.');
        return;
      }
      await keys.update(editItem.token, {
        key_name: form.key_name.trim(),
        team_id: form.team_id || undefined,
        owner_account_id: form.owner_mode === 'self' ? currentUserId || undefined : undefined,
        owner_service_account_id: form.owner_mode === 'service_account' ? form.owner_service_account_id || undefined : undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        models: form.models ? form.models.split(',').map(m => m.trim()).filter(Boolean) : [],
      });
      closeEditor();
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to update key');
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (row: ApiKey) => {
    setForm({
      key_name: row.key_name || '',
      team_id: row.team_id || '',
      owner_mode: row.owner_service_account_id ? 'service_account' : 'self',
      owner_service_account_id: row.owner_service_account_id || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      models: (row.models || []).join(', '),
    });
    setEditItem(row);
    setError(null);
  };

  const handleRevoke = async (hash: string) => {
    if (!confirm('Are you sure you want to revoke this key?')) return;
    try {
      await keys.revoke(hash);
      refetch();
    } catch (err: any) {
      alert(err?.message || 'Failed to revoke key');
    }
  };

  const handleRegenerate = async (hash: string) => {
    if (!confirm('Regenerate this key? The old key will stop working.')) return;
    try {
      const result = await keys.regenerate(hash);
      setCreatedKey(result.raw_key);
      refetch();
    } catch (err: any) {
      alert(err?.message || 'Failed to regenerate key');
    }
  };

  const copyKey = () => {
    if (createdKey) {
      navigator.clipboard.writeText(createdKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const ownerLabel = (row: ApiKey) => {
    if (row.owner_service_account_name) return row.owner_service_account_name;
    if (row.owner_account_id && row.owner_account_id === currentUserId) return 'You';
    if (row.owner_account_email) return row.owner_account_email;
    if (row.owner_account_id) return row.owner_account_id;
    return 'Unassigned';
  };

  const columns = [
    { key: 'key_name', header: 'Name', render: (r: any) => <span className="font-medium">{r.key_name || '(unnamed)'}</span> },
    { key: 'token', header: 'Token', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{maskKey(r.token)}</code> },
    { key: 'team', header: 'Team', render: (r: ApiKey) => <span className="text-sm">{r.team_alias || r.team_id}</span> },
    { key: 'owner', header: 'Owner', render: (r: ApiKey) => <span className="text-sm">{ownerLabel(r)}</span> },
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
        <button onClick={openCreate} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Create Key
        </button>
      </div>
      <Card>
        <div className="px-4 pt-3 pb-2">
          <input value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder="Search keys..." className="w-full sm:w-72 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
        <DataTable columns={columns} data={items} loading={loading} emptyMessage="No API keys created yet" pagination={pagination} onPageChange={setPageOffset} />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={closeEditor} title={editItem ? 'Edit API Key' : 'Create API Key'}>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Key Name *</label>
            <input data-autofocus="true" value={form.key_name} onChange={(e) => setForm({ ...form, key_name: e.target.value })} placeholder="my-key" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team *</label>
            <select value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
              <option value="">Select a team</option>
              {form.team_id && !(teamsList || []).some((t: any) => t.team_id === form.team_id) && (
                <option value={form.team_id} disabled>{form.team_id} (inaccessible)</option>
              )}
              {(teamsList || []).map((t: any) => (
                <option key={t.team_id} value={t.team_id}>{t.team_alias || t.team_id}</option>
              ))}
            </select>
            <p className="mt-1 text-xs text-gray-500">Every key belongs to a team. Team budgets, access, and reporting apply to that scope.</p>
          </div>
          <div className="rounded-lg border border-gray-200 p-4 space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Owned By *</label>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <label className={`flex items-start gap-3 rounded-lg border px-3 py-2 cursor-pointer ${form.owner_mode === 'self' ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}>
                  <input
                    type="radio"
                    name="owner_mode"
                    value="self"
                    checked={form.owner_mode === 'self'}
                    onChange={() => setForm({ ...form, owner_mode: 'self', owner_service_account_id: '' })}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="block text-sm font-medium text-gray-900">You</span>
                    <span className="block text-xs text-gray-500">Use your current admin account as the owner.</span>
                  </span>
                </label>
                <label className={`flex items-start gap-3 rounded-lg border px-3 py-2 cursor-pointer ${form.owner_mode === 'service_account' ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}>
                  <input
                    type="radio"
                    name="owner_mode"
                    value="service_account"
                    checked={form.owner_mode === 'service_account'}
                    onChange={() => setForm({ ...form, owner_mode: 'service_account' })}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="block text-sm font-medium text-gray-900">Service account</span>
                    <span className="block text-xs text-gray-500">Use a non-login owner for automation or shared workloads.</span>
                  </span>
                </label>
              </div>
              <p className="mt-2 text-xs text-gray-500">Ownership is for accountability in the admin UI. It is separate from any optional runtime user attribution.</p>
            </div>

            {form.owner_mode === 'service_account' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Service Account *</label>
                {form.team_id && !serviceAccountsLoading && !hasServiceAccounts ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900">
                    No service accounts exist for this team yet. Create one below and it will be selected automatically.
                  </div>
                ) : (
                  <>
                    <select
                      value={form.owner_service_account_id}
                      onChange={(e) => setForm({ ...form, owner_service_account_id: e.target.value })}
                      disabled={!form.team_id || serviceAccountsLoading}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white disabled:bg-gray-50 disabled:text-gray-500"
                    >
                      <option value="">{!form.team_id ? 'Select a team first' : 'Select a service account'}</option>
                      {form.owner_service_account_id && !availableServiceAccounts.some((item) => item.service_account_id === form.owner_service_account_id) && (
                        <option value={form.owner_service_account_id} disabled>{form.owner_service_account_id} (unavailable)</option>
                      )}
                      {availableServiceAccounts.map((item) => (
                        <option key={item.service_account_id} value={item.service_account_id}>
                          {item.name} ({item.service_account_id})
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-xs text-gray-500">
                      {form.team_id
                        ? 'Choose an existing service account for this team, or create a new one below.'
                        : 'Select a team to load or create service accounts.'}
                    </p>
                  </>
                )}
                <div className="mt-3 border-t border-gray-200 pt-3">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Create Service Account</label>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <input
                      value={newServiceAccountName}
                      onChange={(e) => setNewServiceAccountName(e.target.value)}
                      placeholder="ci-runner"
                      disabled={!form.team_id || creatingServiceAccount}
                      className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                    />
                    <button
                      type="button"
                      onClick={handleCreateServiceAccount}
                      disabled={!form.team_id || creatingServiceAccount}
                      className="px-3 py-2 text-sm bg-gray-900 text-white rounded-lg hover:bg-gray-800 transition-colors disabled:opacity-50"
                    >
                      {creatingServiceAccount ? 'Creating...' : 'Create'}
                    </button>
                  </div>
                  <p className="mt-1 text-xs text-gray-500">Service accounts are non-login owners for shared services and automation. After creation, the new service account is selected automatically.</p>
                </div>
              </div>
            )}
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
            <button onClick={closeEditor} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
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
