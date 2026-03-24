import { useState, useEffect, useMemo } from 'react';
import { useApi } from '../lib/hooks';
import { keys, serviceAccounts, teams } from '../lib/api';
import { buildParentScopedAssetTargets, buildScopedSelectableTargets } from '../lib/assetAccess';
import type { ApiKey, ServiceAccount } from '../lib/api';
import { useAuth } from '../lib/auth';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import { Plus, RefreshCw, Trash2, Copy, Check, Pencil, Key, List, Info } from 'lucide-react';
import { ContentCard, IndexShell } from '../components/admin/shells';

type OwnerMode = 'self' | 'service_account';
type ViewTab = 'all' | 'my';
type TeamOption = {
  team_id: string;
  team_alias?: string | null;
  self_service_keys_enabled?: boolean;
};
type KeyMutationPayload = Record<string, number | string | null | undefined>;

type KeyFormState = {
  key_name: string;
  team_id: string;
  owner_mode: OwnerMode;
  owner_service_account_id: string;
  max_budget: string;
  rpm_limit: string;
  tpm_limit: string;
  rph_limit: string;
  rpd_limit: string;
  tpd_limit: string;
  expires: string;
  asset_access_mode: 'inherit' | 'restrict';
  selected_callable_keys: string[];
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
    rph_limit: '',
    rpd_limit: '',
    tpd_limit: '',
    expires: '',
    asset_access_mode: 'inherit',
    selected_callable_keys: [],
  };
}

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}

function KeyStatus({ row }: { row: ApiKey }) {
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

function PolicyHint({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full">
      <Info className="w-3 h-3" /> {label}: {value}
    </span>
  );
}

export default function ApiKeys() {
  const { session, authMode } = useAuth();
  const currentUserId = session?.account_id || '';
  const permissions = useMemo(() => new Set(session?.effective_permissions || []), [session?.effective_permissions]);
  const isPlatformAdmin = authMode === 'master_key' || session?.role === 'platform_admin';
  const isAdmin = isPlatformAdmin || permissions.has('key.update');
  const canCreateSelf = permissions.has('key.create_self') || isAdmin;
  const canCreate = isAdmin || canCreateSelf;
  const canRevoke = isAdmin || permissions.has('key.create_self');
  const canRegenerate = isAdmin || permissions.has('key.create_self');

  const [viewTab, setViewTab] = useState<ViewTab>(isAdmin ? 'all' : 'my');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 10;
  const myKeysMode = viewTab === 'my';
  const { data: result, loading, refetch } = useApi(
    () => keys.list({ search, my_keys: myKeysMode || undefined, limit: pageSize, offset: pageOffset }),
    [search, pageOffset, myKeysMode],
  );
  const items = result?.data || [];
  const pagination = result?.pagination;
  const { data: teamsResult } = useApi(() => teams.list({ limit: 500 }), []);
  const teamsList = useMemo<TeamOption[]>(
    () => (Array.isArray(teamsResult?.data) ? (teamsResult.data as TeamOption[]) : []),
    [teamsResult?.data],
  );

  const selfServiceTeams = useMemo(
    () => teamsList.filter((team) => team.self_service_keys_enabled),
    [teamsList],
  );

  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<ApiKey | null>(null);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [form, setForm] = useState<KeyFormState>(() => emptyForm());
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [newServiceAccountName, setNewServiceAccountName] = useState('');
  const [creatingServiceAccount, setCreatingServiceAccount] = useState(false);
  const selectedTeamId = form.team_id;
  const usesParentPreview = !editItem || selectedTeamId !== (editItem.team_id || '');
  const { data: editAssetAccess, loading: editAssetAccessLoading } = useApi(
    () => (editItem ? keys.assetAccess(editItem.token, { include_targets: false }) : Promise.resolve(null)),
    [editItem?.token],
  );
  const { data: editAssetAccessTargets, loading: editAssetAccessTargetsLoading } = useApi(
    () => (
      editItem && !usesParentPreview && form.asset_access_mode === 'restrict'
        ? keys.assetAccess(editItem.token, { include_targets: true })
        : Promise.resolve(null)
    ),
    [editItem?.token, usesParentPreview, form.asset_access_mode],
  );
  const { data: parentTeamAssetVisibility, loading: parentTeamAssetVisibilityLoading } = useApi(
    () => (
      (showCreate || !!editItem) && usesParentPreview && form.asset_access_mode === 'restrict' && selectedTeamId
        ? teams.assetVisibility(selectedTeamId)
        : Promise.resolve(null)
    ),
    [showCreate, editItem?.token, selectedTeamId, usesParentPreview, form.asset_access_mode],
  );
  const { data: serviceAccountsResult, loading: serviceAccountsLoading, refetch: refetchServiceAccounts } = useApi(
    () => (
      selectedTeamId
        ? serviceAccounts.list({ team_id: selectedTeamId, limit: 200 })
        : Promise.resolve({ data: [] as ServiceAccount[], pagination: EMPTY_PAGINATION })
    ),
    [selectedTeamId]
  );
  const availableServiceAccounts = useMemo(
    () => serviceAccountsResult?.data ?? [],
    [serviceAccountsResult?.data],
  );
  const hasServiceAccounts = availableServiceAccounts.length > 0;

  const isSelfServiceCreate = myKeysMode && !isAdmin;

  const { data: selectedTeamPolicy } = useApi(
    () => selectedTeamId && isSelfServiceCreate
      ? teams.getSelfServicePolicy(selectedTeamId)
      : Promise.resolve(null),
    [selectedTeamId, isSelfServiceCreate],
  );

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

  useEffect(() => {
    if (!editItem || !editAssetAccess) return;
    setForm((current) => ({
      ...current,
      asset_access_mode: editAssetAccess.mode === 'restrict' ? 'restrict' : 'inherit',
      selected_callable_keys: editAssetAccess.selected_callable_keys || [],
    }));
  }, [editItem, editAssetAccess]);

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
    setPageError(null);
    setError(null);
    setEditItem(null);
    setNewServiceAccountName('');
    const initial = emptyForm();
    if (isSelfServiceCreate && selfServiceTeams.length === 1) {
      initial.team_id = selfServiceTeams[0].team_id;
    }
    setForm(initial);
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
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Failed to create service account'));
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
      if (!isSelfServiceCreate && form.owner_mode === 'service_account' && !form.owner_service_account_id) {
        setError('Select a service account or switch ownership to You.');
        return;
      }
      if (isSelfServiceCreate && selectedTeamPolicy) {
        if (selectedTeamPolicy.self_service_require_expiry && !form.expires) {
          setError('This team requires an expiry date for self-service keys.');
          return;
        }
        if (selectedTeamPolicy.self_service_max_expiry_days != null && form.expires) {
          const expiresDate = new Date(form.expires);
          const maxDate = new Date();
          maxDate.setDate(maxDate.getDate() + selectedTeamPolicy.self_service_max_expiry_days);
          if (expiresDate > maxDate) {
            setError(`Expiry must be within ${selectedTeamPolicy.self_service_max_expiry_days} days from today.`);
            return;
          }
        }
        if (selectedTeamPolicy.self_service_budget_ceiling != null && form.max_budget) {
          if (Number(form.max_budget) > selectedTeamPolicy.self_service_budget_ceiling) {
            setError(`Budget cannot exceed the team ceiling of $${selectedTeamPolicy.self_service_budget_ceiling}.`);
            return;
          }
        }
      }
      const payload: KeyMutationPayload = {
        key_name: form.key_name.trim(),
        team_id: form.team_id || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        rph_limit: form.rph_limit ? Number(form.rph_limit) : undefined,
        rpd_limit: form.rpd_limit ? Number(form.rpd_limit) : undefined,
        tpd_limit: form.tpd_limit ? Number(form.tpd_limit) : undefined,
      };
      if (form.expires) {
        payload.expires = new Date(form.expires).toISOString();
      }
      if (!isSelfServiceCreate) {
        payload.owner_account_id = form.owner_mode === 'self' ? currentUserId || undefined : undefined;
        payload.owner_service_account_id = form.owner_mode === 'service_account' ? form.owner_service_account_id || undefined : undefined;
      }
      const result = await keys.create(payload);
      let assetAccessError: string | null = null;
      if (!isSelfServiceCreate && form.asset_access_mode === 'restrict') {
        try {
          await keys.updateAssetAccess(result.token, {
            mode: 'restrict',
            selected_callable_keys: form.selected_callable_keys,
          });
        } catch (err: unknown) {
          assetAccessError = getErrorMessage(
            err,
            'API key created, but asset access could not be updated. Open the key again to finish access setup.',
          );
        }
      }
      setCreatedKey(result.raw_key);
      closeEditor();
      refetch();
      setPageError(assetAccessError);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Failed to create key'));
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
      const payload: KeyMutationPayload = {
        key_name: form.key_name.trim(),
        team_id: form.team_id || undefined,
        owner_account_id: form.owner_mode === 'self' ? currentUserId || undefined : undefined,
        owner_service_account_id: form.owner_mode === 'service_account' ? form.owner_service_account_id || undefined : undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        rph_limit: form.rph_limit ? Number(form.rph_limit) : undefined,
        rpd_limit: form.rpd_limit ? Number(form.rpd_limit) : undefined,
        tpd_limit: form.tpd_limit ? Number(form.tpd_limit) : undefined,
      };
      if (isSelfServiceCreate) {
        payload.expires = form.expires ? new Date(form.expires).toISOString() : null;
      }
      await keys.update(editItem.token, payload);
      await keys.updateAssetAccess(editItem.token, {
        mode: form.asset_access_mode,
        selected_callable_keys: form.asset_access_mode === 'restrict' ? form.selected_callable_keys : [],
      });
      closeEditor();
      refetch();
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Failed to update key'));
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (row: ApiKey) => {
    setPageError(null);
    setForm({
      key_name: row.key_name || '',
      team_id: row.team_id || '',
      owner_mode: row.owner_service_account_id ? 'service_account' : 'self',
      owner_service_account_id: row.owner_service_account_id || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      rph_limit: row.rph_limit != null ? String(row.rph_limit) : '',
      rpd_limit: row.rpd_limit != null ? String(row.rpd_limit) : '',
      tpd_limit: row.tpd_limit != null ? String(row.tpd_limit) : '',
      expires: row.expires ? row.expires.slice(0, 16) : '',
      asset_access_mode: 'inherit',
      selected_callable_keys: [],
    });
    setEditItem(row);
    setError(null);
  };

  const handleTeamChange = (teamId: string) => {
    setForm((current) => {
      const changed = current.team_id !== teamId;
      return {
        ...current,
        team_id: teamId,
        asset_access_mode: changed ? 'inherit' : current.asset_access_mode,
        selected_callable_keys: changed ? [] : current.selected_callable_keys,
      };
    });
  };

  const handleRevoke = async (hash: string) => {
    if (!confirm('Are you sure you want to revoke this key?')) return;
    try {
      await keys.revoke(hash);
      refetch();
    } catch (err: unknown) {
      alert(getErrorMessage(err, 'Failed to revoke key'));
    }
  };

  const handleRegenerate = async (hash: string) => {
    if (!confirm('Regenerate this key? The old key will stop working.')) return;
    try {
      const result = await keys.regenerate(hash);
      setCreatedKey(result.raw_key);
      refetch();
    } catch (err: unknown) {
      alert(getErrorMessage(err, 'Failed to regenerate key'));
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
    { key: 'key_name', header: 'Name', render: (row: ApiKey) => <span className="font-medium">{row.key_name || '(unnamed)'}</span> },
    { key: 'token', header: 'Token', render: (row: ApiKey) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{maskKey(row.token)}</code> },
    { key: 'team', header: 'Team', render: (row: ApiKey) => <span className="text-sm">{row.team_alias || row.team_id}</span> },
    { key: 'owner', header: 'Owner', render: (row: ApiKey) => <span className="text-sm">{ownerLabel(row)}</span> },
    { key: 'status', header: 'Status', render: (row: ApiKey) => <KeyStatus row={row} /> },
    { key: 'budget', header: 'Budget', render: (row: ApiKey) => <BudgetBar spend={row.spend || 0} max_budget={row.max_budget} /> },
    { key: 'rpm_limit', header: 'RPM', render: (row: ApiKey) => row.rpm_limit != null ? <span className="text-xs font-medium">{Number(row.rpm_limit).toLocaleString()}</span> : <span className="text-gray-400 text-xs">No limit</span> },
    { key: 'tpm_limit', header: 'TPM', render: (row: ApiKey) => row.tpm_limit != null ? <span className="text-xs font-medium">{Number(row.tpm_limit).toLocaleString()}</span> : <span className="text-gray-400 text-xs">No limit</span> },
    { key: 'rph_limit', header: 'RPH', render: (row: ApiKey) => row.rph_limit != null ? <span className="text-xs font-medium">{Number(row.rph_limit).toLocaleString()}</span> : <span className="text-gray-400 text-xs">No limit</span> },
    { key: 'rpd_limit', header: 'RPD', render: (row: ApiKey) => row.rpd_limit != null ? <span className="text-xs font-medium">{Number(row.rpd_limit).toLocaleString()}</span> : <span className="text-gray-400 text-xs">No limit</span> },
    { key: 'tpd_limit', header: 'TPD', render: (row: ApiKey) => row.tpd_limit != null ? <span className="text-xs font-medium">{Number(row.tpd_limit).toLocaleString()}</span> : <span className="text-gray-400 text-xs">No limit</span> },
    {
      key: 'actions', header: '', render: (row: ApiKey) => (
        <div className="flex gap-1">
          {isAdmin && <button onClick={() => openEdit(row)} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Edit"><Pencil className="w-4 h-4 text-gray-500" /></button>}
          {canRegenerate && <button onClick={() => handleRegenerate(row.token)} className="p-1.5 hover:bg-gray-100 rounded-lg" title="Regenerate"><RefreshCw className="w-4 h-4 text-gray-500" /></button>}
          {canRevoke && <button onClick={() => handleRevoke(row.token)} className="p-1.5 hover:bg-red-50 rounded-lg" title="Revoke"><Trash2 className="w-4 h-4 text-red-500" /></button>}
        </div>
      ),
    },
  ];
  const assetTargets = usesParentPreview
    ? buildParentScopedAssetTargets(
        parentTeamAssetVisibility?.callable_targets?.items || [],
        form.selected_callable_keys,
        form.asset_access_mode,
      )
    : buildScopedSelectableTargets(
        editAssetAccessTargets?.selectable_targets || [],
        form.selected_callable_keys,
        form.asset_access_mode,
      );
  const assetAccessLoading = form.asset_access_mode !== 'restrict'
    ? false
    : usesParentPreview
      ? parentTeamAssetVisibilityLoading
      : editAssetAccessTargetsLoading || editAssetAccessLoading;

  const createTeamOptions = isSelfServiceCreate ? selfServiceTeams : teamsList;
  const createFormTitle = isSelfServiceCreate
    ? 'Create Personal Key'
    : editItem ? 'Edit API Key' : 'Create API Key';

  return (
    <IndexShell
      title="API Keys"
      count={pagination?.total ?? null}
      description={(
        <>
          {myKeysMode ? 'Your personal API keys' : 'Manage API keys, ownership, budgets, and rate limits'}
          <span className="mt-1 block text-xs text-gray-400">
            {myKeysMode
              ? 'Create and manage keys for teams that have self-service enabled.'
              : 'Create keys that inherit their team asset set or restrict them to a smaller callable-target subset.'}
          </span>
        </>
      )}
      action={canCreate ? (
        <button onClick={openCreate} className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700">
          <Plus className="h-4 w-4" /> {myKeysMode && !isAdmin ? 'Create Personal Key' : 'Create Key'}
        </button>
      ) : null}
      notice={pageError ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{pageError}</div>
      ) : null}
      toolbar={(
        <div className="flex items-center gap-3 flex-wrap">
          {isAdmin && (
            <div className="inline-flex rounded-lg border border-gray-300 bg-white p-0.5">
              <button
                onClick={() => { setViewTab('all'); setPageOffset(0); }}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewTab === 'all' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'}`}
              >
                <List className="w-3.5 h-3.5" /> All Keys
              </button>
              <button
                onClick={() => { setViewTab('my'); setPageOffset(0); }}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewTab === 'my' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'}`}
              >
                <Key className="w-3.5 h-3.5" /> My Keys
              </button>
            </div>
          )}
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search keys..."
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 sm:w-72"
          />
        </div>
      )}
    >
      <ContentCard>
        <DataTable columns={columns} data={items} loading={loading} emptyMessage={myKeysMode ? 'You have no personal keys yet' : 'No API keys created yet'} pagination={pagination} onPageChange={setPageOffset} />
      </ContentCard>

      <Modal open={showCreate || !!editItem} onClose={closeEditor} title={createFormTitle}>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Key Name *</label>
            <input data-autofocus="true" value={form.key_name} onChange={(e) => setForm({ ...form, key_name: e.target.value })} placeholder="my-key" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team *</label>
            <select value={form.team_id} onChange={(e) => handleTeamChange(e.target.value)} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
              <option value="">Select a team</option>
              {form.team_id && !createTeamOptions.some((team) => team.team_id === form.team_id) && (
                <option value={form.team_id} disabled>{form.team_id} (inaccessible)</option>
              )}
              {createTeamOptions.map((team) => (
                <option key={team.team_id} value={team.team_id}>{team.team_alias || team.team_id}</option>
              ))}
            </select>
            <p className="mt-1 text-xs text-gray-500">
              {isSelfServiceCreate
                ? 'Only teams with self-service keys enabled are shown.'
                : 'Every key belongs to a team. Team budgets, access, and reporting apply to that scope.'}
            </p>
          </div>

          {isSelfServiceCreate && selectedTeamPolicy && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 space-y-1.5">
              <p className="text-xs font-medium text-blue-800">Team Policy Constraints</p>
              <div className="flex flex-wrap gap-1.5">
                {selectedTeamPolicy.self_service_max_keys_per_user != null && (
                  <PolicyHint label="Max keys" value={String(selectedTeamPolicy.self_service_max_keys_per_user)} />
                )}
                {selectedTeamPolicy.self_service_budget_ceiling != null && (
                  <PolicyHint label="Budget ceiling" value={`$${selectedTeamPolicy.self_service_budget_ceiling}`} />
                )}
                {selectedTeamPolicy.self_service_require_expiry && (
                  <PolicyHint label="Expiry" value="Required" />
                )}
                {selectedTeamPolicy.self_service_max_expiry_days != null && (
                  <PolicyHint label="Max expiry" value={`${selectedTeamPolicy.self_service_max_expiry_days} days`} />
                )}
                {!selectedTeamPolicy.self_service_max_keys_per_user && !selectedTeamPolicy.self_service_budget_ceiling && !selectedTeamPolicy.self_service_require_expiry && !selectedTeamPolicy.self_service_max_expiry_days && (
                  <span className="text-xs text-blue-600">No additional constraints</span>
                )}
              </div>
            </div>
          )}

          {!isSelfServiceCreate && !editItem && (
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
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Budget ($)
                {isSelfServiceCreate && selectedTeamPolicy?.self_service_budget_ceiling != null && (
                  <span className="text-xs font-normal text-gray-400 ml-1">(max ${selectedTeamPolicy.self_service_budget_ceiling})</span>
                )}
              </label>
              <input
                type="number"
                value={form.max_budget}
                onChange={(e) => setForm({ ...form, max_budget: e.target.value })}
                max={isSelfServiceCreate && selectedTeamPolicy?.self_service_budget_ceiling != null ? selectedTeamPolicy.self_service_budget_ceiling : undefined}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input type="number" value={form.rpm_limit} onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input type="number" value={form.tpm_limit} onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPH Limit</label>
              <input type="number" value={form.rph_limit} onChange={(e) => setForm({ ...form, rph_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="Requests per hour" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPD Limit</label>
              <input type="number" value={form.rpd_limit} onChange={(e) => setForm({ ...form, rpd_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="Requests per day" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPD Limit</label>
              <input type="number" value={form.tpd_limit} onChange={(e) => setForm({ ...form, tpd_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="Tokens per day" />
            </div>
          </div>

          {(isSelfServiceCreate || !editItem) && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Expiry Date
                {isSelfServiceCreate && selectedTeamPolicy?.self_service_require_expiry && (
                  <span className="text-xs font-normal text-red-500 ml-1">*required</span>
                )}
                {isSelfServiceCreate && selectedTeamPolicy?.self_service_max_expiry_days != null && (
                  <span className="text-xs font-normal text-gray-400 ml-1">(max {selectedTeamPolicy.self_service_max_expiry_days} days)</span>
                )}
              </label>
              <input
                type="datetime-local"
                value={form.expires}
                onChange={(e) => setForm({ ...form, expires: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          )}

          {!isSelfServiceCreate && (
            <>
              <p className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
                Key runtime access is enforced through callable-target bindings and scope policies across organization, team, key, and user scopes. Use the section below to inherit the team set or narrow it for this key.
              </p>
              <AssetAccessEditor
                title="Key Asset Access"
                description="Choose whether this key inherits the team asset set or narrows itself to a selected subset."
                mode={form.asset_access_mode}
                allowModeSelection
                onModeChange={(asset_access_mode) => setForm((current) => ({
                  ...current,
                  asset_access_mode: asset_access_mode === 'restrict' ? 'restrict' : 'inherit',
                  selected_callable_keys: asset_access_mode === 'restrict' ? current.selected_callable_keys : [],
                }))}
                targets={assetTargets}
                selectedKeys={form.selected_callable_keys}
                onSelectedKeysChange={(selected_callable_keys) => setForm({ ...form, selected_callable_keys })}
                loading={assetAccessLoading}
                disabled={saving || !form.team_id}
              />
            </>
          )}

          {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={closeEditor} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={editItem ? handleUpdate : handleCreate} disabled={saving || !form.team_id || assetAccessLoading} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : editItem ? 'Save Changes' : 'Create Key'}</button>
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
    </IndexShell>
  );
}
