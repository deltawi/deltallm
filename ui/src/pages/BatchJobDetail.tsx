import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ApiError,
  batches,
  type BatchCapabilities,
  type BatchJobCosts,
  type BatchJobItem,
  type BatchJobItemDetail,
  type BatchWebhookDelivery,
} from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import { RecordDetailShell } from '../components/admin/shells';
import { ArrowLeft, XCircle, Clock, DollarSign, Hash, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import clsx from 'clsx';
import {
  applyBatchDetailCancellation,
  canCancelBatchDetailJob,
  isCurrentBatchDetailRoute,
  loadBatchDetailResource,
  mergeBatchDetailWebhookDeliveries,
  reconcileBatchDetailReload,
  replaceBatchDetailWebhookDelivery,
  runMutationAndRefresh,
  shouldPreserveBatchDetailResourceOnReloadError,
  type BatchDetailResource,
} from './batchDetailResource';

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
  processing: 'bg-yellow-100 text-yellow-700',
  retrying: 'bg-orange-100 text-orange-700',
  delivered: 'bg-green-100 text-green-700',
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
  processing: 'Processing',
  retrying: 'Retrying',
  delivered: 'Delivered',
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

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
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

function WebhookDeliveriesCard({
  deliveries,
  capabilities,
  replayingEventId,
  replayNotice,
  onReplay,
}: {
  deliveries: BatchWebhookDelivery[];
  capabilities: BatchCapabilities;
  replayingEventId: string | null;
  replayNotice: string | null;
  onReplay: (eventId: string) => void;
}) {
  if (deliveries.length === 0) return null;

  return (
    <Card>
      <div className="border-b border-gray-100 p-4">
        <h3 className="text-sm font-semibold text-gray-900">Webhook Delivery</h3>
        <p className="mt-0.5 text-xs text-gray-500">
          Delivery state is shown without the destination URL, headers, payload, or signing secret.
        </p>
        {replayNotice && (
          <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {replayNotice}
          </p>
        )}
      </div>
      <div className="divide-y divide-gray-100">
        {deliveries.map((delivery) => (
          <div key={delivery.event_id} className="p-4">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="grid flex-1 grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <div>
                  <p className="text-xs text-gray-500">Event</p>
                  <p className="font-mono text-xs text-gray-900">{delivery.event_id}</p>
                  <p className="mt-1 text-xs text-gray-500">{delivery.event_type}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Status</p>
                  <div className="mt-1"><StatusBadge status={delivery.status} /></div>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Attempts</p>
                  <p className="font-medium text-gray-900">{delivery.attempt_count} / {delivery.max_attempts}</p>
                  <p className="mt-1 text-xs text-gray-500">HTTP {delivery.last_status_class || 'none'}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Last update</p>
                  <p className="text-gray-900">{formatDateTime(delivery.updated_at)}</p>
                  {delivery.last_error && <p className="mt-1 text-xs text-red-600">{delivery.last_error}</p>}
                </div>
              </div>
              {capabilities.replay_webhook && delivery.status === 'failed' && (
                <button
                  onClick={() => onReplay(delivery.event_id)}
                  disabled={replayingEventId !== null}
                  className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-100 disabled:opacity-50"
                >
                  <RefreshCw className={clsx('h-4 w-4', replayingEventId === delivery.event_id && 'animate-spin')} />
                  {replayingEventId === delivery.event_id ? 'Scheduling...' : 'Replay delivery'}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function ResourceReloadNotice({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="rounded-md bg-amber-50 px-4 py-3 text-sm text-amber-800">
      {message}
    </p>
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
  const [replayingWebhook, setReplayingWebhook] = useState<string | null>(null);
  const [webhookReplayNotice, setWebhookReplayNotice] = useState<string | null>(null);
  const [resourceReloadNotice, setResourceReloadNotice] = useState<string | null>(null);
  const [resource, setResource] = useState<BatchDetailResource | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const itemsPageRef = useRef(itemsPage);
  const requestGenerationRef = useRef(0);
  const routeGenerationRef = useRef(0);
  const webhookStateGenerationRef = useRef(0);
  const resourceRef = useRef<BatchDetailResource | null>(null);

  useEffect(() => {
    resourceRef.current = resource;
  }, [resource]);

  useEffect(() => {
    activeBatchIdRef.current = batchId;
    requestGenerationRef.current += 1;
    routeGenerationRef.current += 1;
    webhookStateGenerationRef.current += 1;
    setResource(null);
    setLoadError(null);
    const initialItemsPage = { offset: 0, afterLineNumber: null };
    itemsPageRef.current = initialItemsPage;
    setItemsPage(initialItemsPage);
    setItemPageCursors([null]);
    setExpandedItem(null);
    setItemDetails({});
    setItemDetailLoading({});
    setItemDetailErrors({});
    setCosts(null);
    setCostsLoading(false);
    setCostsError(null);
    setReplayingWebhook(null);
    setWebhookReplayNotice(null);
    setResourceReloadNotice(null);
  }, [batchId]);

  const reloadResource = useCallback(async (
    { preserveCurrentOnError = false }: { preserveCurrentOnError?: boolean } = {},
  ): Promise<BatchDetailResource | null> => {
    if (!batchId) return null;
    const requestGeneration = ++requestGenerationRef.current;
    const requestedWebhookGeneration = webhookStateGenerationRef.current;
    const requestedItemsPage = itemsPageRef.current;
    setLoading(true);
    setLoadError(null);
    try {
      const next = await loadBatchDetailResource(batches, batchId, {
        items_limit: ITEMS_PAGE_SIZE,
        items_offset: requestedItemsPage.offset,
        after_line_number: requestedItemsPage.afterLineNumber,
      });
      if (
        activeBatchIdRef.current === batchId
        && requestGenerationRef.current === requestGeneration
      ) {
        setResourceReloadNotice(null);
        setResource((current) => reconcileBatchDetailReload(
          current,
          next,
          requestedWebhookGeneration,
          webhookStateGenerationRef.current,
        ));
      }
      return next;
    } catch (error: unknown) {
      if (
        activeBatchIdRef.current === batchId
        && requestGenerationRef.current === requestGeneration
      ) {
        if (shouldPreserveBatchDetailResourceOnReloadError(
          resourceRef.current,
          requestedWebhookGeneration,
          webhookStateGenerationRef.current,
          preserveCurrentOnError,
        )) {
          setResourceReloadNotice(
            'The batch details could not be refreshed. The current state has been preserved.',
          );
        } else {
          setResourceReloadNotice(null);
          setResource(null);
          setLoadError(error);
        }
      }
      throw error;
    } finally {
      if (
        activeBatchIdRef.current === batchId
        && requestGenerationRef.current === requestGeneration
      ) {
        setLoading(false);
      }
    }
  }, [batchId]);

  useEffect(() => {
    void reloadResource().catch(() => undefined);
    return () => {
      requestGenerationRef.current += 1;
    };
  }, [itemsPage.afterLineNumber, itemsPage.offset, reloadResource]);

  async function handleCancel() {
    if (!batchId || !confirm('Are you sure you want to cancel this batch job?')) return;
    const cancelBatchId = batchId;
    const cancelRouteGeneration = routeGenerationRef.current;
    const isCurrentCancelRoute = () => isCurrentBatchDetailRoute(
      activeBatchIdRef.current,
      routeGenerationRef.current,
      cancelBatchId,
      cancelRouteGeneration,
    );
    setCancelling(true);
    setResourceReloadNotice(null);
    try {
      await runMutationAndRefresh(
        () => batches.cancel(cancelBatchId),
        async (cancellation) => {
          if (!isCurrentCancelRoute()) return;
          setResource((current) => current
            ? applyBatchDetailCancellation(current, cancellation)
            : current);
          await reloadResource({ preserveCurrentOnError: true });
        },
      );
    } catch (error: unknown) {
      if (isCurrentCancelRoute()) {
        alert(errorMessage(error, 'Failed to cancel'));
      }
    } finally {
      if (isCurrentCancelRoute()) {
        setCancelling(false);
      }
    }
  }

  async function handleReplayWebhook(eventId: string) {
    if (!batchId || replayingWebhook || !confirm('Replay this failed webhook delivery?')) return;
    const replayBatchId = batchId;
    const replayRouteGeneration = routeGenerationRef.current;
    const isCurrentReplayRoute = () => isCurrentBatchDetailRoute(
      activeBatchIdRef.current,
      routeGenerationRef.current,
      replayBatchId,
      replayRouteGeneration,
    );
    setReplayingWebhook(eventId);
    setWebhookReplayNotice(null);
    setResourceReloadNotice(null);
    webhookStateGenerationRef.current += 1;
    try {
      const outcome = await runMutationAndRefresh(
        () => batches.replayWebhook(replayBatchId, eventId),
        async (replay) => {
          if (!isCurrentReplayRoute()) return;
          webhookStateGenerationRef.current += 1;
          setResource((current) => current
            ? replaceBatchDetailWebhookDelivery(current, replay.delivery)
            : current);
          const refreshed = await batches.webhookDeliveries(replayBatchId);
          if (!isCurrentReplayRoute()) return;
          webhookStateGenerationRef.current += 1;
          setResource((current) => current
            ? mergeBatchDetailWebhookDeliveries(current, refreshed)
            : current);
        },
      );
      if (outcome.refreshError && isCurrentReplayRoute()) {
        setWebhookReplayNotice(
          'Replay was scheduled, but the latest delivery state could not be refreshed.',
        );
      }
    } catch (error: unknown) {
      if (isCurrentReplayRoute()) {
        alert(error instanceof Error && error.message ? error.message : 'Failed to replay webhook delivery');
      }
    } finally {
      if (isCurrentReplayRoute()) {
        setReplayingWebhook((current) => current === eventId ? null : current);
      }
    }
  }

  const resourceBatchId = resource?.kind === 'live'
    ? resource.job.batch_id
    : resource?.delivery.batch_id;
  if ((loading && !resource) || (resourceBatchId && resourceBatchId !== batchId)) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-brand-primary" />
      </div>
    );
  }

  if (loadError || !resource) {
    const notFound = loadError instanceof ApiError && loadError.status === 404;
    return (
      <RecordDetailShell
        backAction={(
          <button onClick={() => navigate('/batches')} className="flex items-center gap-1 text-sm text-gray-500 transition-colors hover:text-gray-900">
            <ArrowLeft className="w-4 h-4" /> Back to Batch Jobs
          </button>
        )}
        header={<h1 className="text-2xl font-bold text-gray-900">Batch Job</h1>}
      >
        <ResourceReloadNotice message={resourceReloadNotice} />
        <Card>
          <div className="p-8 text-center">
            <h2 className="text-base font-semibold text-gray-900">
              {notFound ? 'Batch job not found' : 'Unable to load batch job'}
            </h2>
            <p className="mt-2 text-sm text-gray-500">
              {notFound
                ? 'Neither live batch metadata nor retained webhook delivery state is available.'
                : loadError instanceof Error ? loadError.message : 'The request could not be completed.'}
            </p>
            <button
              onClick={() => { void reloadResource().catch(() => undefined); }}
              className="mt-4 inline-flex items-center gap-2 rounded-lg bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-100"
            >
              <RefreshCw className="h-4 w-4" /> Retry
            </button>
          </div>
        </Card>
      </RecordDetailShell>
    );
  }

  if (resource.kind === 'archived') {
    return (
      <RecordDetailShell
        backAction={(
          <button onClick={() => navigate('/batches')} className="flex items-center gap-1 text-sm text-gray-500 transition-colors hover:text-gray-900">
            <ArrowLeft className="w-4 h-4" /> Back to Batch Jobs
          </button>
        )}
        header={(
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Archived Webhook Delivery</h1>
            <p className="mt-1 font-mono text-sm text-gray-500">{resource.delivery.batch_id}</p>
          </div>
        )}
      >
        <ResourceReloadNotice message={resourceReloadNotice} />
        <Card>
          <div className="p-5">
            <h2 className="text-sm font-semibold text-gray-900">Batch metadata has been cleaned up</h2>
            <p className="mt-1 text-sm text-gray-500">
              Retained webhook delivery state remains available for inspection and replay until its separate retention period expires.
            </p>
          </div>
        </Card>
        <WebhookDeliveriesCard
          deliveries={resource.delivery.data}
          capabilities={resource.delivery.capabilities}
          replayingEventId={replayingWebhook}
          replayNotice={webhookReplayNotice}
          onReplay={(eventId) => { void handleReplayWebhook(eventId); }}
        />
      </RecordDetailShell>
    );
  }

  const job = resource.job;
  const canCancel = canCancelBatchDetailJob(job);

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
    } catch (error: unknown) {
      if (activeBatchIdRef.current === batchId) {
        setItemDetailErrors((current) => ({
          ...current,
          [detailKey]: errorMessage(error, 'Failed to load item payload'),
        }));
      }
    } finally {
      if (activeBatchIdRef.current === batchId) {
        setItemDetailLoading((current) => ({ ...current, [detailKey]: false }));
      }
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
    } catch (error: unknown) {
      if (activeBatchIdRef.current === batchId) {
        setCostsError(errorMessage(error, 'Failed to load costs'));
      }
    } finally {
      if (activeBatchIdRef.current === batchId) {
        setCostsLoading(false);
      }
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
    const nextItemsPage = { offset: nextOffset, afterLineNumber: nextAfterLineNumber };
    itemsPageRef.current = nextItemsPage;
    setItemsPage(nextItemsPage);
  }

  function handleItemsPreviousPage() {
    const previousOffset = Math.max(0, itemsPage.offset - ITEMS_PAGE_SIZE);
    const previousIndex = Math.floor(previousOffset / ITEMS_PAGE_SIZE);
    const previousAfterLineNumber = itemPageCursors[previousIndex] ?? null;
    setExpandedItem(null);
    const previousItemsPage = {
      offset: previousOffset,
      afterLineNumber: previousAfterLineNumber,
    };
    itemsPageRef.current = previousItemsPage;
    setItemsPage(previousItemsPage);
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

      <ResourceReloadNotice message={resourceReloadNotice} />

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
                  <Clock className="w-4 h-4 text-brand-primary-ink" />
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

      <WebhookDeliveriesCard
        deliveries={job.webhook_deliveries || []}
        capabilities={job.capabilities}
        replayingEventId={replayingWebhook}
        replayNotice={webhookReplayNotice}
        onReplay={(eventId) => { void handleReplayWebhook(eventId); }}
      />

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
          <div className="h-4 w-4 animate-spin rounded-full border-b-2 border-brand-primary" />
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
