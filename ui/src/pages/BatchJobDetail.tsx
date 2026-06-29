import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { batches, type BatchJobCosts, type BatchJobDetail, type BatchJobItem, type BatchJobItemDetail } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import { RecordDetailShell } from '../components/admin/shells';
import { ArrowLeft, XCircle, Clock, DollarSign, Hash, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import clsx from 'clsx';

const STATUS_COLORS: Record<string, string> = {
  validating: 'bg-purple-100 text-purple-700',
  queued: 'bg-blue-100 text-blue-700',
  in_progress: 'bg-yellow-100 text-yellow-700',
  finalizing: 'bg-yellow-100 text-yellow-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-600',
  expired: 'bg-gray-100 text-gray-600',
  pending: 'bg-blue-100 text-blue-700',
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
  pending: 'Pending',
};

const ITEMS_PAGE_SIZE = 50;

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx('inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium', STATUS_COLORS[status] || 'bg-gray-100 text-gray-600')}>
      {STATUS_LABELS[status] || status}
    </span>
  );
}

function formatDateTime(d: string | null | undefined): string {
  if (!d) return '--';
  return new Date(d).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
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

function formatCost(value: number | null | undefined, loading: boolean): string {
  if (typeof value === 'number') return `$${value.toFixed(6)}`;
  return loading ? 'Loading...' : '--';
}

function itemDetailKey(batchId: string, itemId: string): string {
  return `${batchId}:${itemId}`;
}

function ProgressRing({ total, completed, failed, inProgress, cancelled }: {
  total: number; completed: number; failed: number; inProgress: number; cancelled: number;
}) {
  if (total === 0) return <div className="text-gray-400 text-sm">No items</div>;
  const pct = Math.round(((completed + failed + cancelled) / total) * 100);
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const completedArc = (completed / total) * circumference;
  const failedArc = (failed / total) * circumference;
  const cancelledArc = (cancelled / total) * circumference;

  return (
    <div className="flex items-center gap-6">
      <div className="relative w-32 h-32">
        <svg className="w-32 h-32 transform -rotate-90" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r={radius} fill="none" stroke="#f3f4f6" strokeWidth="10" />
          <circle cx="60" cy="60" r={radius} fill="none" stroke="#22c55e" strokeWidth="10"
            strokeDasharray={`${completedArc} ${circumference}`} strokeLinecap="round" />
          <circle cx="60" cy="60" r={radius} fill="none" stroke="#ef4444" strokeWidth="10"
            strokeDasharray={`${failedArc} ${circumference}`}
            strokeDashoffset={`${-completedArc}`} strokeLinecap="round" />
          {cancelledArc > 0 && (
            <circle cx="60" cy="60" r={radius} fill="none" stroke="#9ca3af" strokeWidth="10"
              strokeDasharray={`${cancelledArc} ${circumference}`}
              strokeDashoffset={`${-(completedArc + failedArc)}`} strokeLinecap="round" />
          )}
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-2xl font-bold text-gray-900">{pct}%</span>
        </div>
      </div>
      <div className="space-y-2 text-sm">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-green-500" />
          <span className="text-gray-600">Completed</span>
          <span className="font-semibold text-gray-900 ml-auto">{completed}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-red-500" />
          <span className="text-gray-600">Failed</span>
          <span className="font-semibold text-gray-900 ml-auto">{failed}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-yellow-400" />
          <span className="text-gray-600">In Progress</span>
          <span className="font-semibold text-gray-900 ml-auto">{inProgress}</span>
        </div>
        {cancelled > 0 && (
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-gray-400" />
            <span className="text-gray-600">Cancelled</span>
            <span className="font-semibold text-gray-900 ml-auto">{cancelled}</span>
          </div>
        )}
        <div className="flex items-center gap-2 pt-1 border-t border-gray-100">
          <div className="w-3 h-3 rounded-full bg-gray-200" />
          <span className="text-gray-600">Total</span>
          <span className="font-semibold text-gray-900 ml-auto">{total}</span>
        </div>
      </div>
    </div>
  );
}

export default function BatchJobDetail() {
  const { batchId } = useParams<{ batchId: string }>();
  const navigate = useNavigate();
  const activeBatchIdRef = useRef(batchId);
  const [itemsPage, setItemsPage] = useState<{ offset: number; afterLineNumber: number | null }>({ offset: 0, afterLineNumber: null });
  const [itemPageCursors, setItemPageCursors] = useState<Array<number | null>>([null]);
  const [expandedItem, setExpandedItem] = useState<string | null>(null);
  const [itemDetails, setItemDetails] = useState<Record<string, BatchJobItemDetail>>({});
  const [itemDetailLoading, setItemDetailLoading] = useState<Record<string, boolean>>({});
  const [itemDetailErrors, setItemDetailErrors] = useState<Record<string, string>>({});
  const [costs, setCosts] = useState<BatchJobCosts | null>(null);
  const [costsLoading, setCostsLoading] = useState(false);
  const [costsError, setCostsError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const { data: job, loading, refetch } = useApi<BatchJobDetail | null>(
    () => batches.get(batchId!, {
      items_limit: ITEMS_PAGE_SIZE,
      items_offset: itemsPage.offset,
      after_line_number: itemsPage.afterLineNumber,
    }),
    [batchId, itemsPage.offset, itemsPage.afterLineNumber],
  );

  useEffect(() => {
    activeBatchIdRef.current = batchId;
    setItemsPage({ offset: 0, afterLineNumber: null });
    setItemPageCursors([null]);
    setExpandedItem(null);
    setItemDetails({});
    setItemDetailLoading({});
    setItemDetailErrors({});
    setCosts(null);
    setCostsLoading(false);
    setCostsError(null);
  }, [batchId]);

  if (!job || job.batch_id !== batchId) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  const canCancel = Boolean(job.capabilities?.cancel) && ['validating', 'queued', 'in_progress', 'finalizing'].includes(job.status);

  async function handleCancel() {
    if (!batchId || !confirm('Are you sure you want to cancel this batch job?')) return;
    setCancelling(true);
    try {
      await batches.cancel(batchId);
      refetch();
    } catch (e: any) {
      alert(e.message || 'Failed to cancel');
    } finally {
      setCancelling(false);
    }
  }

  async function handleExpandItem(itemId: string) {
    if (!batchId) return;
    const detailKey = itemDetailKey(batchId, itemId);
    if (expandedItem === itemId) {
      setExpandedItem(null);
      return;
    }
    setExpandedItem(itemId);
    if (itemDetails[detailKey] || itemDetailLoading[detailKey]) return;

    setItemDetailErrors((current) => {
      const next = { ...current };
      delete next[detailKey];
      return next;
    });
    setItemDetailLoading((current) => ({ ...current, [detailKey]: true }));
    try {
      const detail = await batches.getItem(batchId, itemId);
      if (activeBatchIdRef.current !== batchId) return;
      setItemDetails((current) => ({ ...current, [detailKey]: detail }));
    } catch (e: any) {
      if (activeBatchIdRef.current !== batchId) return;
      setItemDetailErrors((current) => ({
        ...current,
        [detailKey]: e?.message || 'Failed to load item payload',
      }));
    } finally {
      if (activeBatchIdRef.current !== batchId) return;
      setItemDetailLoading((current) => ({ ...current, [detailKey]: false }));
    }
  }

  async function handleLoadCosts() {
    if (!batchId || costsLoading) return;
    setCostsLoading(true);
    setCostsError(null);
    try {
      const result = await batches.costs(batchId);
      if (activeBatchIdRef.current !== batchId) return;
      setCosts(result);
    } catch (e: any) {
      if (activeBatchIdRef.current !== batchId) return;
      setCostsError(e?.message || 'Failed to load costs');
    } finally {
      if (activeBatchIdRef.current !== batchId) return;
      setCostsLoading(false);
    }
  }

  function handleItemsNextPage() {
    const nextAfterLineNumber = itemsPagination?.next_after_line_number;
    if (nextAfterLineNumber == null) return;
    const nextOffset = itemsPage.offset + ITEMS_PAGE_SIZE;
    const nextIndex = Math.floor(nextOffset / ITEMS_PAGE_SIZE);
    setExpandedItem(null);
    setItemPageCursors((current) => {
      const next = current.slice(0, nextIndex + 1);
      next[nextIndex] = nextAfterLineNumber;
      return next;
    });
    setItemsPage({ offset: nextOffset, afterLineNumber: nextAfterLineNumber });
  }

  function handleItemsPreviousPage() {
    const previousOffset = Math.max(0, itemsPage.offset - ITEMS_PAGE_SIZE);
    const previousIndex = Math.floor(previousOffset / ITEMS_PAGE_SIZE);
    const previousAfterLineNumber = itemPageCursors[previousIndex] ?? null;
    setExpandedItem(null);
    setItemsPage({ offset: previousOffset, afterLineNumber: previousAfterLineNumber });
  }

  const itemsData = job.items?.data || [];
  const itemsPagination = job.items?.pagination;

  const itemColumns = [
    {
      key: 'line_number',
      header: '#',
      render: (row: BatchJobItem) => <span className="font-mono text-xs">{row.line_number}</span>,
    },
    {
      key: 'custom_id',
      header: 'Custom ID',
      render: (row: BatchJobItem) => <span className="font-mono text-xs max-w-[120px] truncate block">{row.custom_id}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      render: (row: BatchJobItem) => <StatusBadge status={row.status} />,
    },
    {
      key: 'attempts',
      header: 'Attempts',
      render: (row: BatchJobItem) => <span className="text-sm">{row.attempts}</span>,
    },
    {
      key: 'billed_cost',
      header: 'Cost',
      render: (row: BatchJobItem) => <span className="text-sm">${(row.billed_cost || 0).toFixed(6)}</span>,
    },
    {
      key: 'last_error',
      header: 'Error',
      render: (row: BatchJobItem) => row.last_error
        ? <span className="text-xs text-red-600 max-w-[200px] truncate block">{row.last_error}</span>
        : <span className="text-xs text-gray-400">--</span>,
    },
    {
      key: 'expand',
      header: '',
      render: (row: BatchJobItem) => (
        <button
          onClick={(e) => { e.stopPropagation(); handleExpandItem(row.item_id); }}
          className="p-1 hover:bg-gray-100 rounded"
          title="View item payload"
        >
          {expandedItem === row.item_id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
      ),
    },
  ];

  const expandedRow = expandedItem ? itemsData.find((item: BatchJobItem) => item.item_id === expandedItem) : null;
  const expandedDetailKey = batchId && expandedItem ? itemDetailKey(batchId, expandedItem) : null;
  const expandedDetail = expandedDetailKey ? itemDetails[expandedDetailKey] : null;

  return (
    <RecordDetailShell
      backAction={(
        <button onClick={() => navigate('/batches')} className="flex items-center gap-1 text-sm text-gray-500 transition-colors hover:text-gray-900">
          <ArrowLeft className="w-4 h-4" /> Back to Batch Jobs
        </button>
      )}
      header={(
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-gray-900">Batch Job</h1>
              <StatusBadge status={job.status} />
            </div>
            <p className="mt-1 font-mono text-sm text-gray-500">{job.batch_id}</p>
          </div>
          {canCancel && (
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="inline-flex items-center gap-2 rounded-lg bg-red-50 px-4 py-2 text-sm font-medium text-red-700 transition-colors hover:bg-red-100 disabled:opacity-50"
            >
              <XCircle className="w-4 h-4" />
              {cancelling ? 'Cancelling...' : 'Cancel Batch'}
            </button>
          )}
        </div>
      )}
    >

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card>
          <div className="p-5">
            <h3 className="text-sm font-semibold text-gray-900 mb-4">Progress</h3>
            <ProgressRing
              total={job.total_items}
              completed={job.completed_items}
              failed={job.failed_items}
              inProgress={job.in_progress_items}
              cancelled={job.cancelled_items}
            />
          </div>
        </Card>

        <Card>
          <div className="p-5 space-y-3">
            <h3 className="text-sm font-semibold text-gray-900 mb-4">Details</h3>
            <div className="grid grid-cols-2 gap-y-3 text-sm">
              <span className="text-gray-500">Endpoint</span>
              <span className="font-medium text-gray-900">{job.endpoint}</span>
              <span className="text-gray-500">Model</span>
              <span className="font-medium text-gray-900">{job.model || '--'}</span>
              <span className="text-gray-500">Team</span>
              <span className="font-medium text-gray-900">{job.team_alias || '--'}</span>
              <span className="text-gray-500">API Key</span>
              <span className="font-mono text-xs text-gray-700">{job.created_by_api_key || '--'}</span>
              <span className="text-gray-500">Mode</span>
              <span className="font-medium text-gray-900 text-xs">{job.execution_mode}</span>
            </div>
          </div>
        </Card>

        <Card>
          <div className="p-5 space-y-4">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-gray-900">Timing & Cost</h3>
              {!costs && (
                <button
                  onClick={handleLoadCosts}
                  disabled={costsLoading}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:opacity-50"
                >
                  <RefreshCw className={clsx('h-3.5 w-3.5', costsLoading && 'animate-spin')} />
                  {costsError ? 'Retry' : 'Load costs'}
                </button>
              )}
            </div>
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-blue-50 rounded-lg">
                  <Clock className="w-4 h-4 text-blue-600" />
                </div>
                <div>
                  <p className="text-xs text-gray-500">Duration</p>
                  <p className="text-sm font-semibold text-gray-900">{formatDuration(job.started_at, job.completed_at)}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="p-2 bg-green-50 rounded-lg">
                  <DollarSign className="w-4 h-4 text-green-600" />
                </div>
                <div>
                  <p className="text-xs text-gray-500">Billed Cost</p>
                  <p className="text-sm font-semibold text-gray-900">{formatCost(costs?.total_billed_cost ?? job.total_billed_cost, costsLoading)}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="p-2 bg-gray-50 rounded-lg">
                  <Hash className="w-4 h-4 text-gray-600" />
                </div>
                <div>
                  <p className="text-xs text-gray-500">Provider Cost</p>
                  <p className="text-sm font-semibold text-gray-900">{formatCost(costs?.total_provider_cost ?? job.total_provider_cost, costsLoading)}</p>
                </div>
              </div>
              {costsError && <p className="text-xs text-red-600">{costsError}</p>}
            </div>
            <div className="pt-3 border-t border-gray-100 space-y-1.5 text-xs text-gray-500">
              <div className="flex justify-between">
                <span>Created</span>
                <span className="text-gray-700">{formatDateTime(job.created_at)}</span>
              </div>
              <div className="flex justify-between">
                <span>Started</span>
                <span className="text-gray-700">{formatDateTime(job.started_at)}</span>
              </div>
              <div className="flex justify-between">
                <span>Completed</span>
                <span className="text-gray-700">{formatDateTime(job.completed_at)}</span>
              </div>
              {job.expires_at && (
                <div className="flex justify-between">
                  <span>Expires</span>
                  <span className="text-gray-700">{formatDateTime(job.expires_at)}</span>
                </div>
              )}
            </div>
          </div>
        </Card>
      </div>

      <Card>
        <div className="p-4 border-b border-gray-100">
          <h3 className="text-sm font-semibold text-gray-900">Batch Items</h3>
          <p className="text-xs text-gray-500 mt-0.5">{itemsPagination?.total ?? 0} items total</p>
        </div>
        <DataTable
          columns={itemColumns}
          data={itemsData}
          loading={loading}
          emptyMessage="No items found"
          pagination={itemsPagination}
          onPreviousPage={handleItemsPreviousPage}
          onNextPage={handleItemsNextPage}
        />
        {expandedItem && expandedRow && (
          <ExpandedItemView
            item={expandedDetail || expandedRow}
            loading={Boolean(expandedDetailKey && itemDetailLoading[expandedDetailKey])}
            error={expandedDetailKey ? itemDetailErrors[expandedDetailKey] : undefined}
          />
        )}
      </Card>
    </RecordDetailShell>
  );
}

function ExpandedItemView({
  item,
  loading,
  error,
}: {
  item: BatchJobItem | BatchJobItemDetail;
  loading?: boolean;
  error?: string | null;
}) {
  if (loading) {
    return (
      <div className="border-t border-gray-100 bg-gray-50 p-4">
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <div className="h-4 w-4 animate-spin rounded-full border-b-2 border-blue-600" />
          Loading item payload...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="border-t border-gray-100 bg-red-50 p-4 text-sm text-red-700">
        {error}
      </div>
    );
  }

  if (!item.request_body && !item.response_body && !item.error_body && !item.usage) {
    return (
      <div className="border-t border-gray-100 bg-gray-50 p-4 text-sm text-gray-500">
        No item payload available.
      </div>
    );
  }

  return (
    <div className="border-t border-gray-100 bg-gray-50 p-4 space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {item.request_body && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">Request</h4>
            <pre className="text-xs bg-white border border-gray-200 rounded-lg p-3 overflow-auto max-h-48">
              {JSON.stringify(item.request_body, null, 2)}
            </pre>
          </div>
        )}
        {item.response_body && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">Response</h4>
            <pre className="text-xs bg-white border border-gray-200 rounded-lg p-3 overflow-auto max-h-48">
              {JSON.stringify(item.response_body, null, 2)}
            </pre>
          </div>
        )}
        {item.error_body && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">Error</h4>
            <pre className="text-xs bg-red-50 border border-red-200 rounded-lg p-3 overflow-auto max-h-48">
              {JSON.stringify(item.error_body, null, 2)}
            </pre>
          </div>
        )}
        {item.usage && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">Usage</h4>
            <pre className="text-xs bg-white border border-gray-200 rounded-lg p-3 overflow-auto max-h-48">
              {JSON.stringify(item.usage, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
