import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { models } from '../lib/api';
import { modelDetailPath, modelEditPath } from '../lib/modelRoutes';
import DataTable from '../components/DataTable';
import ProviderBadge from '../components/ProviderBadge';
import StatusBadge from '../components/StatusBadge';
import { ContentCard, IndexShell } from '../components/admin/shells';
import { MODE_OPTIONS, MODE_BADGE_COLORS } from '../components/ModelForm';
import { Box, Plus, Pencil, Search, Trash2, Radio } from 'lucide-react';

export default function Models() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 10;
  const { data: result, loading, refetch } = useApi(() => models.list({ search, limit: pageSize, offset: pageOffset }), [search, pageOffset]);
  const items = result?.data || [];
  const pagination = result?.pagination;

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPageOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this model?')) return;
    try {
      await models.delete(id);
      refetch();
    } catch (err: any) {
      alert(err?.message || 'Failed to delete model');
    }
  };

  const modeLabel = (mode: string) => {
    const opt = MODE_OPTIONS.find(o => o.value === mode);
    return opt ? opt.label : mode;
  };

  const columns = [
    { key: 'model_name', header: 'Model Name', render: (r: any) => <span className="font-medium">{r.model_name}</span> },
    { key: 'mode', header: 'Type', render: (r: any) => {
      const mode = r.mode || r.model_info?.mode || 'chat';
      return <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${MODE_BADGE_COLORS[mode] || 'bg-gray-100 text-gray-700'}`}>{modeLabel(mode)}</span>;
    }},
    { key: 'provider', header: 'Provider', render: (r: any) => <ProviderBadge provider={r.provider} model={r.deltallm_params?.model} /> },
    { key: 'transport', header: 'Transport', render: (r: any) => {
      const transport = r.deltallm_params?.transport || 'http';
      if (transport === 'grpc') {
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-violet-100 text-violet-700">
            <Radio className="w-3 h-3" /> gRPC
          </span>
        );
      }
      return <span className="text-xs text-gray-400">HTTP</span>;
    }},
    { key: 'deployment_id', header: 'Deployment ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.deployment_id}</code> },
    { key: 'healthy', header: 'Health', render: (r: any) => <StatusBadge status={r.healthy ? 'healthy' : 'unhealthy'} /> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => navigate(modelEditPath(r.deployment_id))} className="p-1.5 hover:bg-gray-100 rounded-lg"><Pencil className="w-4 h-4 text-gray-500" /></button>
          <button onClick={() => handleDelete(r.deployment_id)} className="p-1.5 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4 text-red-500" /></button>
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
      action={(
        <button
          onClick={() => navigate('/models/new')}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" /> Add Model
        </button>
      )}
      toolbar={(
        <div className="relative w-full sm:w-72">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search models..."
            className="h-9 w-full rounded-lg border border-gray-300 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}
    >
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
    </IndexShell>
  );
}
