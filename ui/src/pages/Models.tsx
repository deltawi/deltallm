import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { models, type ModelDeploymentDetail } from '../lib/api';
import { useAuth } from '../lib/auth';
import { resolveUiAccess } from '../lib/authorization';
import { modelDetailPath, modelEditPath } from '../lib/modelRoutes';
import DataTable from '../components/DataTable';
import ProviderBadge from '../components/ProviderBadge';
import StatusBadge from '../components/StatusBadge';
import { ContentCard, IndexShell } from '../components/admin/shells';
import { MODE_OPTIONS, MODE_BADGE_COLORS } from '../components/modelFormShared';
import ModelsMobileList, { type ModelFilterValue } from '../components/models/ModelsMobileList';
import { Box, Plus, Pencil, Search, Trash2 } from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { mutationOutcome } from '../lib/mutationOutcome';

export default function Models() {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { session, authMode } = useAuth();
  const canManageModels = resolveUiAccess(authMode, session).model_admin;
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [modeFilter, setModeFilter] = useState<ModelFilterValue>('all');
  const [pageOffset, setPageOffset] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const pageSize = 10;
  const { data: result, loading, refetch } = useApi(
    (signal) => models.list(
      {
        search,
        mode: modeFilter === 'all' ? undefined : modeFilter,
        limit: pageSize,
        offset: pageOffset,
      },
      signal,
    ),
    [search, modeFilter, pageOffset],
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

  const handleModeFilterChange = (value: ModelFilterValue) => {
    setModeFilter(value);
    setPageOffset(0);
  };

  const modeLabel = (mode: string) => {
    const opt = MODE_OPTIONS.find(o => o.value === mode);
    return opt ? opt.label : mode;
  };

  const rowMode = (row: ModelDeploymentDetail) => {
    const metadataMode = row.model_info.mode;
    return row.mode || (typeof metadataMode === 'string' ? metadataMode : 'chat');
  };

  const columns = [
    { key: 'model_name', header: 'Model Name', render: (r: ModelDeploymentDetail) => <span className="font-medium">{r.model_name}</span> },
    { key: 'mode', header: 'Type', render: (r: ModelDeploymentDetail) => {
      const mode = rowMode(r);
      return <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${MODE_BADGE_COLORS[mode] || 'bg-gray-100 text-gray-700'}`}>{modeLabel(mode)}</span>;
    }},
    { key: 'provider', header: 'Provider', render: (r: ModelDeploymentDetail) => <ProviderBadge provider={r.provider} model={r.deltallm_params.model} /> },
    { key: 'credential_source', header: 'Credentials', render: (r: ModelDeploymentDetail) => (
      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${r.credential_source === 'named' ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-700'}`}>
        {r.credential_source === 'named' ? 'Named' : 'Inline'}
      </span>
    )},
    { key: 'deployment_id', header: 'Deployment ID', render: (r: ModelDeploymentDetail) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.deployment_id}</code> },
    { key: 'healthy', header: 'Health', render: (r: ModelDeploymentDetail) => <StatusBadge status={r.healthy ? 'healthy' : 'unhealthy'} /> },
    {
      key: 'actions', header: '', render: (r: ModelDeploymentDetail) => (
        <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
          {canManageModels ? (
            <>
              <button aria-label={`Edit ${r.model_name}`} onClick={() => navigate(modelEditPath(r.deployment_id))} className="p-1.5 hover:bg-gray-100 rounded-lg"><Pencil className="w-4 h-4 text-gray-500" /></button>
              <button aria-label={`Delete ${r.model_name}`} onClick={() => setDeleteTarget(r.deployment_id)} className="p-1.5 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4 text-red-500" /></button>
            </>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <IndexShell
      title="Models"
      titleIcon={Box}
      count={pagination?.total ?? null}
      description="Manage model deployments and providers"
      action={canManageModels ? (
        <button
          onClick={() => navigate('/models/new')}
          className="flex items-center gap-2 rounded-lg bg-brand-primary px-4 py-2 text-sm font-medium text-brand-on-primary transition-colors hover:bg-brand-primary-hover"
        >
          <Plus className="h-4 w-4" /> Add Model
        </button>
      ) : undefined}
      toolbar={(
        <div className="hidden w-full flex-wrap items-center gap-3 md:flex">
          <div className="relative w-full sm:w-72">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search models..."
              className="h-9 w-full rounded-lg border border-gray-300 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
            />
          </div>
          <select
            value={modeFilter}
            onChange={(e) => handleModeFilterChange(e.target.value as ModelFilterValue)}
            aria-label="Filter model type"
            className="h-9 rounded-lg border border-gray-300 bg-white px-3 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-brand-primary"
          >
            <option value="all">All types</option>
            {MODE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      )}
    >
      <div className="hidden md:block">
        <ContentCard>
          <DataTable
            columns={columns}
            data={items}
            loading={loading}
            emptyMessage="No models configured"
            onRowClick={(row) => navigate(modelDetailPath(row.deployment_id))}
            pagination={pagination}
            onPageChange={setPageOffset}
          />
        </ContentCard>
      </div>
      <div className="md:hidden">
        <ModelsMobileList
          items={items}
          loading={loading}
          pagination={pagination}
          pageSize={pageSize}
          onPageChange={setPageOffset}
          searchValue={searchInput}
          onSearchChange={setSearchInput}
          activeFilter={modeFilter}
          onFilterChange={handleModeFilterChange}
          emptyMessage="No models configured"
          canManage={canManageModels}
          onView={(id) => navigate(modelDetailPath(id))}
          onEdit={(id) => navigate(modelEditPath(id))}
          onDelete={setDeleteTarget}
        />
      </div>
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
