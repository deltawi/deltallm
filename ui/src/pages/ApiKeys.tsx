import { useScopedMutation } from '../lib/useScopedMutation';
import { modelOutputRows } from '../lib/modelOutputTpm';
import KeyRevocationNotice from '../components/KeyRevocationNotice';
import type { KeyRemovalResult } from '../lib/api/keyRevocations';
import { useState, useEffect, useMemo } from 'react';
import { useApi } from '../lib/hooks';
import { keys, serviceAccounts, teams } from '../lib/api';
import {
  assetAccessLoadErrorMessage,
  buildParentScopedAssetTargets,
  buildScopedSelectableAccessGroups,
  buildScopedSelectableTargets,
  isAssetVisibilityFor,
  isScopedAssetAccessFor,
} from '../lib/assetAccess';
import type { ApiKey, ServiceAccount } from '../lib/api';
import { useAuth } from '../lib/auth';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { Plus, RefreshCw, Trash2, Pencil, Key, List } from 'lucide-react';
import { ContentCard, IndexShell } from '../components/admin/shells';
import ApiKeysMobileList from '../components/api-keys/ApiKeysMobileList';
import ApiKeyEditorDialog from '../components/api-keys/ApiKeyEditorDialog';
import ApiKeySecretDialog from '../components/api-keys/ApiKeySecretDialog';
import type { KeyTeamOption as TeamOption } from '../components/api-keys/ApiKeyDetailsFields';
import { emptyKeyForm, keyMutationPayload, validateKeyForm, type KeyFormState } from '../lib/apiKeyForm';

type ViewTab = 'all' | 'my';
const EMPTY_PAGINATION = { total: 0, limit: 200, offset: 0, has_more: false };

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

export default function ApiKeys() {
  const { session, authMode } = useAuth();
  const currentUserId = session?.account_id || '';
  const permissions = useMemo(() => new Set(session?.effective_permissions || []), [session?.effective_permissions]);
  const isPlatformAdmin = authMode === 'master_key' || session?.role === 'platform_admin';
  const isAdmin = isPlatformAdmin || permissions.has('key.update');
  const canCreateSelf = permissions.has('key.create_self') || isAdmin;
  const canCreate = isAdmin || canCreateSelf;
  const canRevoke = isAdmin || permissions.has('key.create_self');
  const canRegenerate = session?.session_source !== 'external_customer' && (isAdmin || permissions.has('key.create_self'));

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
  const [form, setForm] = useState<KeyFormState>(() => emptyKeyForm());
  const [error, setError] = useState<string | null>(null);
  const [revocation, setRevocation] = useState<KeyRemovalResult | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [newServiceAccountName, setNewServiceAccountName] = useState('');
  const [creatingServiceAccount, setCreatingServiceAccount] = useState(false);
  const [assetSearchInput, setAssetSearchInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const [accessGroupPageOffset, setAccessGroupPageOffset] = useState(0);
  const accessGroupPageSize = 50;
  const selectedTeamId = form.team_id;
  const usesParentPreview = !editItem || selectedTeamId !== (editItem.team_id || '');
  const { data: editAssetAccess, error: editAssetAccessError, loading: editAssetAccessLoading } = useApi(
    () => (editItem ? keys.assetAccess(editItem.token, { include_targets: false }) : Promise.resolve(null)),
    [editItem?.token],
  );
  const currentEditAssetAccess = editItem && isScopedAssetAccessFor(editAssetAccess, {
    scopeType: 'api_key',
    scopeId: editItem.token,
  })
    ? editAssetAccess
    : null;
  const editAssetAccessPending = !!editItem && (editAssetAccessLoading || !currentEditAssetAccess);
  const { data: editAssetAccessTargets, error: editAssetAccessTargetsError, loading: editAssetAccessTargetsLoading } = useApi(
    () => (
      editItem && !usesParentPreview && form.asset_access_mode === 'restrict'
        ? keys.assetAccess(editItem.token, {
            include_targets: true,
            access_group_search: assetSearch || undefined,
            access_group_limit: accessGroupPageSize,
            access_group_offset: accessGroupPageOffset,
          })
        : Promise.resolve(null)
    ),
    [editItem?.token, usesParentPreview, form.asset_access_mode, assetSearch, accessGroupPageOffset],
  );
  const { data: parentTeamAssetVisibility, error: parentTeamAssetVisibilityError, loading: parentTeamAssetVisibilityLoading } = useApi(
    () => (
      (showCreate || !!editItem) && usesParentPreview && form.asset_access_mode === 'restrict' && selectedTeamId
        ? teams.assetVisibility(selectedTeamId, {
            include_access_groups: true,
            access_group_search: assetSearch || undefined,
            access_group_limit: accessGroupPageSize,
            access_group_offset: accessGroupPageOffset,
          })
        : Promise.resolve(null)
    ),
    [showCreate, editItem?.token, selectedTeamId, usesParentPreview, form.asset_access_mode, assetSearch, accessGroupPageOffset],
  );
  const { data: serviceAccountsResult, error: serviceAccountsError, loading: serviceAccountsLoading, refetch: refetchServiceAccounts } = useApi(
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
    const t = setTimeout(() => {
      setAssetSearch(assetSearchInput);
      setAccessGroupPageOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [assetSearchInput]);

  useEffect(() => {
    if (form.owner_mode !== 'service_account' && form.owner_service_account_id) {
      setForm((current) => ({ ...current, owner_service_account_id: '' }));
    }
  }, [form.owner_mode, form.owner_service_account_id]);


  useEffect(() => {
    if (!editItem || !currentEditAssetAccess) return;
    setForm((current) => ({
      ...current,
      asset_access_mode: currentEditAssetAccess.mode === 'restrict' ? 'restrict' : 'inherit',
      selected_callable_keys: currentEditAssetAccess.selected_callable_keys || [],
      selected_access_group_keys: currentEditAssetAccess.selected_access_group_keys || [],
    }));
  }, [editItem, currentEditAssetAccess]);

  const closeEditor = () => {
    outputMutation.cancel();
    setShowCreate(false);
    setEditItem(null);
    setError(null);
    setSaving(false);
    setCreatingServiceAccount(false);
    setNewServiceAccountName('');
    setForm(emptyKeyForm());
    setAssetSearchInput('');
    setAssetSearch('');
    setAccessGroupPageOffset(0);
  };

  const openCreate = () => {
    setPageError(null);
    setError(null);
    setEditItem(null);
    setNewServiceAccountName('');
    const initial = emptyKeyForm();
    if (isSelfServiceCreate && selfServiceTeams.length === 1) {
      initial.team_id = selfServiceTeams[0].team_id;
    }
    setForm(initial);
    setAssetSearchInput('');
    setAssetSearch('');
    setAccessGroupPageOffset(0);
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
      refetchServiceAccounts();
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

  const outputMutation = useScopedMutation(`${showCreate}:${editItem?.token || ''}`);

  const handleCreate = async () => {
    if (saving || creatingServiceAccount) return;
    const pending = outputMutation.begin();
    if (!pending) return;
    setError(null);
    setSaving(true);
    try {
      const issue = validateKeyForm(form, isSelfServiceCreate, selectedTeamPolicy ?? null);
      if (issue) { setError(issue.message); return; }
      if (assetAccessLoadError) { setError(assetAccessLoadError); return; }
      if (assetAccessLoading) { setError('Wait for asset access options to finish loading.'); return; }
      const payload = keyMutationPayload(form, { selfService: isSelfServiceCreate, editing: false, accountId: currentUserId });
      const result = await keys.create(payload, pending.signal);
      if (!pending.current()) return;
      let assetAccessError: string | null = null;
      if (!isSelfServiceCreate && form.asset_access_mode === 'restrict') {
        try {
          await keys.updateAssetAccess(result.token, {
            mode: 'restrict',
            selected_callable_keys: form.selected_callable_keys,
            selected_access_group_keys: form.selected_access_group_keys,
          }, pending.signal);
          if (!pending.current()) return;
        } catch (err: unknown) {
          if (!pending.current()) return;
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
      if (!pending.current()) return;
      setError(getErrorMessage(err, 'Failed to create key'));
    } finally {
      if (pending.current()) setSaving(false);
      pending.finish();
    }
  };

  const handleUpdate = async () => {
    if (!editItem) return;
    if (saving || creatingServiceAccount) return;
    const pending = outputMutation.begin();
    if (!pending) return;
    setError(null);
    setSaving(true);
    try {
      const issue = validateKeyForm(form, isSelfServiceCreate, selectedTeamPolicy ?? null);
      if (issue) { setError(issue.message); return; }
      if (assetAccessLoadError) { setError(assetAccessLoadError); return; }
      if (editAssetAccessPending || assetAccessLoading) { setError('Wait for asset access options to finish loading.'); return; }
      const payload = keyMutationPayload(form, { selfService: isSelfServiceCreate, editing: true, accountId: currentUserId });
      await keys.update(editItem.token, payload, pending.signal);
      if (!pending.current()) return;
      let assetAccessError: string | null = null;
      try {
        await keys.updateAssetAccess(editItem.token, {
          mode: form.asset_access_mode,
          selected_callable_keys: form.asset_access_mode === 'restrict' ? form.selected_callable_keys : [],
          selected_access_group_keys: form.asset_access_mode === 'restrict' ? form.selected_access_group_keys : [],
        }, pending.signal);
      } catch (err: unknown) {
        if (!pending.current()) return;
        assetAccessError = `Key limits saved. Asset access could not be saved: ${getErrorMessage(err, 'Try again.')}`;
      }
      if (!pending.current()) return;
      closeEditor();
      refetch();
      setPageError(assetAccessError);
    } catch (err: unknown) {
      if (!pending.current()) return;
      setError(getErrorMessage(err, 'Failed to update key'));
    } finally {
      if (pending.current()) setSaving(false);
      pending.finish();
    }
  };

  const openEdit = (row: ApiKey) => {
    outputMutation.cancel();
    setSaving(false);
    setPageError(null);
    setForm({
      key_name: row.key_name || '',
      team_id: row.team_id || '',
      owner_mode: row.owner_service_account_id ? 'service_account' : 'self',
      owner_service_account_id: row.owner_service_account_id || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      output_tpm_limit: row.output_tpm_limit != null ? String(row.output_tpm_limit) : '',
      model_output_tpm_limit: modelOutputRows(row.model_output_tpm_limit),
      rph_limit: row.rph_limit != null ? String(row.rph_limit) : '',
      rpd_limit: row.rpd_limit != null ? String(row.rpd_limit) : '',
      tpd_limit: row.tpd_limit != null ? String(row.tpd_limit) : '',
      expires: row.expires ? row.expires.slice(0, 16) : '',
      asset_access_mode: 'inherit',
      selected_callable_keys: [],
      selected_access_group_keys: [],
    });
    setEditItem(row);
    setError(null);
    setAssetSearchInput('');
    setAssetSearch('');
    setAccessGroupPageOffset(0);
  };

  const handleTeamChange = (teamId: string) => {
    setForm((current) => {
      const changed = current.team_id !== teamId;
      return {
        ...current,
        team_id: teamId,
        owner_service_account_id: changed && !editItem ? '' : current.owner_service_account_id,
        asset_access_mode: changed ? 'inherit' : current.asset_access_mode,
        selected_callable_keys: changed ? [] : current.selected_callable_keys,
        selected_access_group_keys: changed ? [] : current.selected_access_group_keys,
      };
    });
    setAssetSearchInput('');
    setAssetSearch('');
    setAccessGroupPageOffset(0);
  };

  const handleRevoke = async (hash: string) => {
    if (!confirm('Are you sure you want to revoke this key?')) return;
    try {
      const result = await keys.revoke(hash);
      setRevocation(result);
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
    { key: 'output_tpm_limit', header: 'Output TPM', render: (row: ApiKey) => row.output_tpm_limit == null ? 'No limit' : row.output_tpm_limit.toLocaleString() },
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
  const currentEditAssetAccessTargets = editItem && isScopedAssetAccessFor(editAssetAccessTargets, {
    scopeType: 'api_key',
    scopeId: editItem.token,
  })
    ? editAssetAccessTargets
    : null;
  const currentParentTeamAssetVisibility = selectedTeamId && isAssetVisibilityFor(parentTeamAssetVisibility, {
    teamId: selectedTeamId,
  })
    ? parentTeamAssetVisibility
    : null;
  const assetTargets = usesParentPreview
    ? buildParentScopedAssetTargets(
        currentParentTeamAssetVisibility?.callable_targets?.items || [],
        form.selected_callable_keys,
        form.asset_access_mode,
      )
    : buildScopedSelectableTargets(
        currentEditAssetAccessTargets?.selectable_targets || [],
        form.selected_callable_keys,
        form.asset_access_mode,
      );
  const assetAccessGroups = usesParentPreview
    ? buildScopedSelectableAccessGroups(
        currentParentTeamAssetVisibility?.access_groups?.items || [],
        form.selected_access_group_keys,
      )
    : buildScopedSelectableAccessGroups(
        currentEditAssetAccessTargets?.selectable_access_groups || [],
        form.selected_access_group_keys,
      );
  const accessGroupPagination = usesParentPreview
    ? currentParentTeamAssetVisibility?.access_groups?.pagination
    : currentEditAssetAccessTargets?.access_group_pagination;
  const assetTargetsLoading = form.asset_access_mode !== 'restrict'
    ? false
    : usesParentPreview
      ? !currentParentTeamAssetVisibility && parentTeamAssetVisibilityLoading
      : !currentEditAssetAccessTargets && (editAssetAccessTargetsLoading || editAssetAccessLoading);
  const accessGroupsLoading = form.asset_access_mode !== 'restrict'
    ? false
    : usesParentPreview
      ? parentTeamAssetVisibilityLoading
      : editAssetAccessTargetsLoading || editAssetAccessLoading;
  const assetAccessLoading = assetTargetsLoading || accessGroupsLoading;
  const activeAssetAccessError = !isSelfServiceCreate && form.asset_access_mode === 'restrict'
    ? usesParentPreview ? parentTeamAssetVisibilityError : editAssetAccessTargetsError
    : null;
  const assetAccessLoadError = !isSelfServiceCreate && (showCreate || !!editItem)
    ? assetAccessLoadErrorMessage((editItem ? editAssetAccessError : null) || activeAssetAccessError)
    : null;

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
        <button onClick={openCreate} className="inline-flex items-center gap-2 rounded-lg bg-brand-primary px-4 py-2 text-sm font-medium text-brand-on-primary transition-colors hover:bg-brand-primary-hover">
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
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewTab === 'all' ? 'bg-brand-primary text-brand-on-primary shadow-sm' : 'text-gray-600 hover:bg-gray-50'}`}
              >
                <List className="w-3.5 h-3.5" /> All Keys
              </button>
              <button
                onClick={() => { setViewTab('my'); setPageOffset(0); }}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewTab === 'my' ? 'bg-brand-primary text-brand-on-primary shadow-sm' : 'text-gray-600 hover:bg-gray-50'}`}
              >
                <Key className="w-3.5 h-3.5" /> My Keys
              </button>
            </div>
          )}
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search keys..."
            className="hidden md:block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary sm:w-72"
          />
        </div>
      )}
    >
      {revocation && <KeyRevocationNotice key={revocation.invalidation_id} result={revocation} />}
      <div className="hidden md:block">
        <ContentCard>
          <DataTable columns={columns} data={items} loading={loading} emptyMessage={myKeysMode ? 'You have no personal keys yet' : 'No API keys created yet'} pagination={pagination} onPageChange={setPageOffset} />
        </ContentCard>
      </div>
      <div className="md:hidden">
        <ApiKeysMobileList
          items={items}
          loading={loading}
          pagination={pagination}
          pageSize={pageSize}
          onPageChange={setPageOffset}
          searchValue={searchInput}
          onSearchChange={setSearchInput}
          currentUserId={currentUserId}
          emptyMessage={myKeysMode ? 'You have no personal keys yet' : 'No API keys created yet'}
          canEdit={isAdmin}
          canRegenerate={canRegenerate}
          canRevoke={canRevoke}
          onEdit={openEdit}
          onRegenerate={handleRegenerate}
          onRevoke={handleRevoke}
        />
      </div>

      {(showCreate || editItem) && <ApiKeyEditorDialog
        title={createFormTitle}
        form={form}
        onChange={setForm}
        onTeamChange={handleTeamChange}
        teams={createTeamOptions}
        selfService={isSelfServiceCreate}
        editing={!!editItem}
        policy={selectedTeamPolicy ?? null}
        serviceAccounts={availableServiceAccounts}
        serviceAccountsLoading={serviceAccountsLoading}
        serviceAccountsError={serviceAccountsError ? getErrorMessage(serviceAccountsError, 'Failed to load service accounts.') : null}
        newServiceAccountName={newServiceAccountName}
        onNewServiceAccountNameChange={setNewServiceAccountName}
        creatingServiceAccount={creatingServiceAccount}
        onCreateServiceAccount={handleCreateServiceAccount}
        error={error || assetAccessLoadError}
        saving={saving}
        saveDisabled={!form.team_id || editAssetAccessPending || assetAccessLoading || Boolean(assetAccessLoadError)}
        onClose={closeEditor}
        onSave={editItem ? handleUpdate : handleCreate}
        assetAccess={{
          title: 'Key access',
          description: 'Choose the targets this key can use within team access.',
          mode: form.asset_access_mode,
          allowModeSelection: true,
          onModeChange: (mode) => setForm((current) => ({ ...current,
            asset_access_mode: mode === 'restrict' ? 'restrict' : 'inherit',
            selected_callable_keys: mode === 'restrict' ? current.selected_callable_keys : [],
            selected_access_group_keys: mode === 'restrict' ? current.selected_access_group_keys : [],
          })),
          targets: assetTargets,
          selectedKeys: form.selected_callable_keys,
          onSelectedKeysChange: (selected_callable_keys) => setForm({ ...form, selected_callable_keys }),
          accessGroups: assetAccessGroups,
          selectedAccessGroupKeys: form.selected_access_group_keys,
          onSelectedAccessGroupKeysChange: (selected_access_group_keys) => setForm({ ...form, selected_access_group_keys }),
          targetsLoading: assetTargetsLoading,
          accessGroupsLoading,
          disabled: saving || !form.team_id || editAssetAccessPending || Boolean(assetAccessLoadError),
          modeControlsDisabled: saving || !form.team_id || editAssetAccessPending,
          searchValue: assetSearchInput,
          onSearchValueChange: setAssetSearchInput,
          accessGroupPagination,
          onAccessGroupPageChange: setAccessGroupPageOffset,
        }}
      />}
      {createdKey && <ApiKeySecretDialog secret={createdKey} onClose={() => setCreatedKey(null)} />}

    </IndexShell>
  );
}
