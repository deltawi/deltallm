import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { models, type ModelDeploymentDetail } from '../lib/api';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession, resolveUiAccess } from '../lib/authorization';
import { modelDetailPath, modelEditPath } from '../lib/modelRoutes';
import type { Column } from '../components/DataTable';
import AssetList from '../components/admin/lists/AssetList';
import { assetMetadataColumns } from '../components/admin/lists/assetMetadataColumns';
import { CopyIdentifier, ListIdentity } from '../components/admin/lists/AssetListCells';
import ModelTypePill from '../components/models/ModelTypePill';
import { useAssetListSort } from '../lib/useAssetListSort';
import ProviderBadge from '../components/ProviderBadge';
import StatusBadge from '../components/StatusBadge';
import { IndexShell } from '../components/admin/shells';
import { MODE_OPTIONS } from '../components/modelFormShared';
import { Box, Plus, Pencil, Trash2 } from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { mutationOutcome } from '../lib/mutationOutcome';

export default function Models() {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { session, authMode } = useAuth();
  const canCreateModels = resolveUiAccess(authMode, session).model_admin;
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [modeFilter, setModeFilter] = useState('all');
  const [pageOffset, setPageOffset] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const pageSize = 10;
  const listSort = useAssetListSort(['name', 'mode', 'provider', 'health', 'created_by', 'updated_at', 'visibility'] as const, () => setPageOffset(0));
  const { data: result, loading, error, refetch } = useApi(
    (signal) => models.list(
      {
        search,
        mode: modeFilter === 'all' ? undefined : modeFilter,
        limit: pageSize,
        offset: pageOffset,
        sort_by: listSort.sortBy, sort_direction: listSort.sortDirection,
      },
      signal,
    ),
    [search, modeFilter, pageOffset, listSort.sortBy, listSort.sortDirection],
  );
  const items = result?.data || [];
  const pagination = result?.pagination;

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPageOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const result = await models.delete(deleteTarget);
      const outcome = mutationOutcome(`"${deleteTarget}" was deleted.`, result.warnings);
      pushToast({
        tone: outcome.tone,
        title: outcome.tone === 'info' ? 'Model deleted with warning' : 'Model deleted',
        message: outcome.message,
      });
      setDeleteTarget(null);
      refetch();
    } catch (err: unknown) {
      pushToast({
        tone: 'error',
        title: 'Delete failed',
        message: err instanceof Error ? err.message : 'Failed to delete model',
      });
    } finally {
      setDeleting(false);
    }
  };

  const handleModeFilterChange = (value: string) => {
    setModeFilter(value);
    setPageOffset(0);
  };

  const rowMode = (row: ModelDeploymentDetail) => {
    const metadataMode = row.model_info?.mode;
    return row.mode || (typeof metadataMode === 'string' ? metadataMode : 'chat');
  };

  const displayName = (row: ModelDeploymentDetail) => row.display_name || row.model_name;
  const apiModelId = (row: ModelDeploymentDetail) => row.api_model_id || row.model_name;

  const columns: Column<ModelDeploymentDetail>[] = [
    { key: 'name', header: 'Model', sortKey: 'name', render: (row) => <ListIdentity name={displayName(row)} identifier={apiModelId(row)} onOpen={() => navigate(modelDetailPath(row.deployment_id))} secondary={<><CopyIdentifier value={row.deployment_id} label="Deployment ID" />{row.deltallm_params.tpm || row.deltallm_params.rpm ? <span className="block text-gray-400 md:hidden">{row.deltallm_params.tpm ? `TPM ${row.deltallm_params.tpm}` : `RPM ${row.deltallm_params.rpm}`}</span> : null}</>} actions={<>
      {isPlatformAdmin || row.access?.capabilities.write ? <button type="button" aria-label={`Edit ${displayName(row)}`} onClick={() => navigate(modelEditPath(row.deployment_id))} className="rounded-lg p-1.5 hover:bg-gray-100"><Pencil className="h-4 w-4 text-gray-500" /></button> : null}
      {isPlatformAdmin || row.access?.capabilities.delete ? <button type="button" aria-label={`Delete ${displayName(row)}`} onClick={() => setDeleteTarget(row.deployment_id)} className="rounded-lg p-2 md:p-1.5 hover:bg-red-50"><Trash2 className="h-4 w-4 text-red-500" /></button> : null}
    </>} /> },
    { key: 'mode', header: 'Type', sortKey: 'mode', render: (row) => <ModelTypePill mode={rowMode(row)} /> },
    { key: 'provider', header: 'Provider', sortKey: 'provider', render: (row) => <ProviderBadge provider={row.provider} model={row.deltallm_params.model} /> },
    { key: 'health', header: 'Health', sortKey: 'health', render: (row) => <StatusBadge status={row.health_status || (row.healthy == null ? 'unknown' : row.healthy ? 'healthy' : 'unhealthy')} /> },
    ...assetMetadataColumns<ModelDeploymentDetail>(),
  ];

  return (
    <IndexShell
      title="Models"
      titleIcon={Box}
      count={pagination?.total ?? null}
      description="Manage model deployments and providers"
      action={canCreateModels ? (
        <button
          onClick={() => navigate('/models/new')}
          className="flex items-center gap-2 rounded-lg bg-brand-primary px-4 py-2 text-sm font-medium text-brand-on-primary transition-colors hover:bg-brand-primary-hover"
        >
          <Plus className="h-4 w-4" /> Add Model
        </button>
      ) : undefined}

    >
      <AssetList columns={columns} data={items} rowKey={(row) => row.deployment_id} loading={loading} error={error} onRetry={refetch} emptyMessage="No models configured" search={searchInput} searchLabel="Search models" onSearchChange={setSearchInput} sort={listSort.sort} onSortChange={listSort.onSortChange} pagination={pagination} onPageChange={setPageOffset} onRowClick={(row) => navigate(modelDetailPath(row.deployment_id))} filters={
        <select value={modeFilter} onChange={(event) => handleModeFilterChange(event.target.value)} aria-label="Filter model type" className="h-10 rounded-lg border border-gray-200 bg-white px-3 text-base text-gray-700 focus:outline-none focus:ring-2 focus:ring-brand-primary md:text-sm"><option value="all">All types</option>{MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
      } />
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete model deployment"
        description={deleteTarget ? `Delete deployment "${deleteTarget}"? This cannot be undone.` : ''}
        confirmLabel="Delete Model"
        destructive
        confirming={deleting}
        onConfirm={handleDelete}
        onClose={() => { if (!deleting) setDeleteTarget(null); }}
      />
    </IndexShell>
  );
}
