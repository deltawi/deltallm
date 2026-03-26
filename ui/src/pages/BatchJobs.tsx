import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { batches, type BatchJobListItem, type BatchJobSummary } from '../lib/api';
import DataTable from '../components/DataTable';
import { Layers, Search, Clock, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import { ContentCard, IndexShell } from '../components/admin/shells';

const STATUS_COLORS: Record<string, string> = {
  validating: 'bg-purple-100 text-purple-700',
  queued: 'bg-blue-100 text-blue-700',
  in_progress: 'bg-yellow-100 text-yellow-700',
  finalizing: 'bg-yellow-100 text-yellow-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-600',
  expired: 'bg-gray-100 text-gray-600',
};

const STATUS_LABELS: Record<string, string> = {
  validating: 'Validating',
  queued: 'Queued',
  in_progress: 'In Progress',
  finalizing: 'Finalizing',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  expired: 'Expired',
};

function BatchStatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium', STATUS_COLORS[status] || 'bg-gray-100 text-gray-600')}>
      {STATUS_LABELS[status] || status}
    </span>
  );
}

function ProgressBar({ total, completed, failed, inProgress }: { total: number; completed: number; failed: number; inProgress: number }) {
  if (total === 0) return <span className="text-xs text-gray-400">--</span>;
  const completedPct = (completed / total) * 100;
  const failedPct = (failed / total) * 100;
  const inProgressPct = (inProgress / total) * 100;
  return (
    <div className="w-28">
      <div className="flex justify-between text-[10px] mb-0.5 text-gray-500">
        <span>{completed + failed}/{total}</span>
        <span>{Math.round(((completed + failed) / total) * 100)}%</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden flex">
        {completedPct > 0 && <div className="h-full bg-green-500" style={{ width: `${completedPct}%` }} />}
        {failedPct > 0 && <div className="h-full bg-red-500" style={{ width: `${failedPct}%` }} />}
        {inProgressPct > 0 && <div className="h-full bg-yellow-400" style={{ width: `${inProgressPct}%` }} />}
      </div>
    </div>
  );
}

function formatDuration(start: string | null | undefined, end: string | null | undefined): string {
  if (!start) return '--';
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const diff = Math.max(0, e - s);
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

function formatDate(d: string | null | undefined): string {
  if (!d) return '--';
  return new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const STATUS_TABS = [
  { value: '', label: 'All' },
  { value: 'queued', label: 'Queued' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
];

export default function BatchJobs() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 10;

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPageOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const { data: summary } = useApi<BatchJobSummary | null>(() => batches.summary(), []);
  const { data: result, loading } = useApi(
    () => batches.list({ search, status: statusFilter || undefined, limit: pageSize, offset: pageOffset }),
    [search, statusFilter, pageOffset],
  );

  const items: BatchJobListItem[] = result?.data || [];
  const pagination = result?.pagination;

  const summaryCards = [
    { label: 'Total Jobs', value: summary?.total ?? 0, icon: Layers, color: 'text-blue-600', bg: 'bg-blue-50' },
    { label: 'In Progress', value: (summary?.queued ?? 0) + (summary?.in_progress ?? 0), icon: Loader2, color: 'text-yellow-600', bg: 'bg-yellow-50' },
    { label: 'Completed', value: summary?.completed ?? 0, icon: CheckCircle2, color: 'text-green-600', bg: 'bg-green-50' },
    { label: 'Failed', value: summary?.failed ?? 0, icon: XCircle, color: 'text-red-600', bg: 'bg-red-50' },
  ];

  const columns = [
    {
      key: 'batch_id',
      header: 'Batch ID',
      render: (row: BatchJobListItem) => (
        <span className="font-mono text-xs">{(row.batch_id || '').substring(0, 8)}...</span>
      ),
    },
    {
      key: 'model',
      header: 'Model',
      render: (row: BatchJobListItem) => <span className="text-sm">{row.model || '--'}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      render: (row: BatchJobListItem) => <BatchStatusBadge status={row.status} />,
    },
    {
      key: 'progress',
      header: 'Progress',
      render: (row: BatchJobListItem) => (
        <ProgressBar
          total={row.total_items}
          completed={row.completed_items}
          failed={row.failed_items}
          inProgress={row.in_progress_items}
        />
      ),
    },
    {
      key: 'total_cost',
      header: 'Cost',
      render: (row: BatchJobListItem) => <span className="text-sm">${(row.total_cost || 0).toFixed(4)}</span>,
    },
    {
      key: 'team_alias',
      header: 'Team',
      render: (row: BatchJobListItem) => <span className="text-sm text-gray-500">{row.team_alias || '--'}</span>,
    },
    {
      key: 'created_at',
      header: 'Created',
      render: (row: BatchJobListItem) => <span className="text-xs text-gray-500">{formatDate(row.created_at)}</span>,
    },
    {
      key: 'duration',
      header: 'Duration',
      render: (row: BatchJobListItem) => (
        <div className="flex items-center gap-1 text-xs text-gray-500">
          <Clock className="w-3 h-3" />
          {formatDuration(row.started_at, row.completed_at)}
        </div>
      ),
    },
  ];

  return (
    <IndexShell
      title="Batch Jobs"
      titleIcon={Layers}
      count={pagination?.total ?? null}
      description="Monitor and manage batch embedding jobs."
      summary={(
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {summaryCards.map((card) => (
            <div key={card.label} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
              <div className="flex items-center gap-3">
                <div className={clsx('rounded-lg p-2', card.bg)}>
                  <card.icon className={clsx('h-5 w-5', card.color)} />
                </div>
                <div>
                  <p className="text-2xl font-bold text-gray-900">{card.value}</p>
                  <p className="text-xs text-gray-500">{card.label}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      toolbar={(
        <div className="space-y-3">
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Search by batch ID or model..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="w-full rounded-lg border border-gray-200 py-2 pl-9 pr-3 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex flex-wrap gap-1">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.value}
                onClick={() => {
                  setStatusFilter(tab.value);
                  setPageOffset(0);
                }}
                className={clsx(
                  'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
                  statusFilter === tab.value
                    ? 'bg-blue-100 text-blue-700'
                    : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      )}
    >
      <ContentCard>
        <DataTable
          columns={columns}
          data={items}
          loading={loading}
          emptyMessage="No batch jobs found"
          onRowClick={(row) => navigate(`/batches/${row.batch_id}`)}
          pagination={pagination}
          onPageChange={setPageOffset}
        />
      </ContentCard>
    </IndexShell>
  );
}
