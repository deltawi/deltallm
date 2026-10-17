import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { models, type ModelDeploymentDetail } from '../lib/api';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession, resolveUiAccess } from '../lib/authorization';
import { modelDetailPath, modelEditPath } from '../lib/modelRoutes';
import Button from '../components/Button';
import type { Column } from '../components/DataTable';
import AssetList from '../components/admin/lists/AssetList';
import { assetMetadataColumns } from '../components/admin/lists/assetMetadataColumns';
import { CopyIdentifier, ListIdentity, RowAction } from '../components/admin/lists/AssetListCells';
import ModelTypePill from '../components/models/ModelTypePill';
import ModelCostCell from '../components/models/ModelCostCell';
import { useAssetListSort } from '../lib/useAssetListSort';
import ProviderBadge from '../components/ProviderBadge';
import StatusBadge from '../components/StatusBadge';
import { IndexShell } from '../components/admin/shells';
import { MODE_OPTIONS } from '../components/modelFormShared';
import { Box, Plus, Pencil, Trash2 } from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { mutationOutcome } from '../lib/mutationOutcome';
import { navSectionLabel } from '../lib/navigation';

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
  const listSort = useAssetListSort(['name', 'mode', 'provider', 'cost', 'health', 'created_by', 'updated_at', 'visibility'] as const, () => setPageOffset(0));
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
    { key: 'name', header: 'Model', sortKey: 'name', render: (row) => <ListIdentity name={displayName(row)} identifier={apiModelId(row)} onOpen={() => navigate(modelDetailPath(row.deployment_id))} /> },
    { key: 'deployment_id', header: 'Deployment ID', render: (row) => <CopyIdentifier value={row.deployment_id} label="Deployment ID" /> },
    { key: 'mode', header: 'Type', sortKey: 'mode', render: (row) => <ModelTypePill mode={rowMode(row)} /> },
    { key: 'provider', header: 'Provider', sortKey: 'provider', render: (row) => <ProviderBadge provider={row.provider} model={row.deltallm_params.model} /> },
    { key: 'cost', header: 'Cost', sortKey: 'cost', render: (row) => <ModelCostCell mode={rowMode(row)} modelInfo={row.model_info} /> },
    { key: 'health', header: 'Health', sortKey: 'health', render: (row) => <StatusBadge status={row.health_status || (row.healthy == null ? 'unknown' : row.healthy ? 'healthy' : 'unhealthy')} /> },
    ...assetMetadataColumns<ModelDeploymentDetail>(),
  ];

  const rowActions = (row: ModelDeploymentDetail) => <>
    {isPlatformAdmin || row.access?.capabilities.write ? <RowAction label={`Edit ${displayName(row)}`} icon={Pencil} onClick={() => navigate(modelEditPath(row.deployment_id))} /> : null}
    {isPlatformAdmin || row.access?.capabilities.delete ? <RowAction label={`Delete ${displayName(row)}`} icon={Trash2} destructive onClick={() => setDeleteTarget(row.deployment_id)} /> : null}
  </>;

  return (
    <IndexShell
      eyebrowPrefix={navSectionLabel('/models')}
      title="Models"
      titleIcon={Box}
      count={pagination?.total ?? null}
      description="Manage model deployments and providers"
      action={canCreateModels ? (
        <Button
          onClick={() => navigate('/models/new')}
        >
          <Plus className="h-4 w-4" /> Add Model
        </Button>
      ) : undefined}

    >
      <AssetList columns={columns} data={items} rowKey={(row) => row.deployment_id} loading={loading} error={error} onRetry={refetch} emptyMessage="No models configured" search={searchInput} searchLabel="Search models" onSearchChange={setSearchInput} sort={listSort.sort} onSortChange={listSort.onSortChange} pagination={pagination} onPageChange={setPageOffset} onRowClick={(row) => navigate(modelDetailPath(row.deployment_id))} rowActions={rowActions} filters={
        <select value={modeFilter} onChange={(event) => handleModeFilterChange(event.target.value)} aria-label="Filter model type" className="control-field-md rounded-lg border border-gray-200 bg-white px-3 text-base text-gray-700 focus:outline-none focus:ring-2 focus:ring-brand-primary md:text-sm"><option value="all">All types</option>{MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
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
