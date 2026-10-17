import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Layers, RefreshCw, Search } from 'lucide-react';
import {
  ApiError,
  batches,
  type BatchItemStatus,
  type BatchJobItem,
  type BatchWebhookDelivery,
} from '../lib/api';
import Button from '../components/Button';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { EntityAvatar, PageShell } from '../components/admin/shells';
import { ActionMenu, CopyButton, EndpointTag, StatusText, type ActionMenuItem } from '../components/batch-jobs/BatchBits';
import BatchItemDrawer from '../components/batch-jobs/BatchItemDrawer';
import BatchItemsTable from '../components/batch-jobs/BatchItemsTable';
import { FailuresCard, FilesCard, ProgressCard, TimelineCard, WebhooksCard } from '../components/batch-jobs/BatchRail';
import { Segmented, Skeleton } from '../components/usage/ReportBits';
import {
  BATCH_ITEM_FILTERS,
  batchProgress,
  endpointLabel,
  fmtAgo,
  fmtDateTime,
  isActiveJob,
  jobStatus,
} from '../lib/batchJobs';
import { errorMessage } from '../lib/errorMessage';
import { navTrail } from '../lib/navigation';
import { useAutoRefresh } from '../lib/useAutoRefresh';
import { useReport } from '../lib/useReport';
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

const ITEMS_PAGE_SIZE = 25;
/** Reload interval while the batch is still running. */
const LIVE_MS = 10_000;

type ItemFilter = BatchItemStatus | 'all';

interface ItemsQuery {
  offset: number;
  afterLineNumber: number | null;
  status: ItemFilter;
  customId: string;
}

const FIRST_PAGE: ItemsQuery = { offset: 0, afterLineNumber: null, status: 'all', customId: '' };

type PendingAction =
  | { kind: 'cancel' }
  | { kind: 'retry_finalization' }
  | { kind: 'requeue_stale' }
  | { kind: 'mark_failed' }
  | { kind: 'replay'; delivery: BatchWebhookDelivery };

const ACTION_COPY: Record<Exclude<PendingAction['kind'], 'replay'>, { title: string; description: string; confirm: string; destructive: boolean }> = {
  cancel: {
    title: 'Cancel this batch?',
    description: 'The batch stops after the lines that are running now. Lines that did not start are cancelled. You cannot undo this.',
    confirm: 'Cancel batch',
    destructive: true,
  },
  retry_finalization: {
    title: 'Retry finalization?',
    description: 'The gateway writes the output and error files again for this batch. Use this when finalization stopped.',
    confirm: 'Retry finalization',
    destructive: false,
  },
  requeue_stale: {
    title: 'Requeue stale lines?',
    description: 'Lines whose worker lease expired go back to the queue. Lines that are running correctly do not change.',
    confirm: 'Requeue lines',
    destructive: false,
  },
  mark_failed: {
    title: 'Mark this batch as failed?',
    description: 'All lines that did not finish are marked failed, and the batch stops. You cannot undo this.',
    confirm: 'Mark failed',
    destructive: true,
  },
};

function ResourceReloadNotice({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="rounded-lg bg-amber-50 px-4 py-2.5 text-sm text-amber-800">{message}</p>;
}

function DetailSkeleton() {
  return (
    <div className="grid gap-3.5" role="status" aria-label="Loading batch">
      <Skeleton className="h-8 w-80" />
      <div className="grid gap-3.5 xl:grid-cols-[minmax(0,1fr)_330px]">
        <Skeleton className="h-[420px] w-full rounded-xl" />
        <Skeleton className="h-[420px] w-full rounded-xl" />
      </div>
    </div>
  );
}

export default function BatchJobDetail() {
  const { batchId } = useParams<{ batchId: string }>();
  const { pushToast } = useToast();
  const activeBatchIdRef = useRef(batchId);
  const [itemsQuery, setItemsQuery] = useState<ItemsQuery>(FIRST_PAGE);
  const [itemPageCursors, setItemPageCursors] = useState<Array<number | null>>([null]);
  const [customIdInput, setCustomIdInput] = useState('');
  // A copy of the open line, so the panel stays open when a reload moves the line off this page.
  const [selectedSnapshot, setSelectedSnapshot] = useState<BatchJobItem | null>(null);
  const selectedItemId = selectedSnapshot?.item_id ?? null;
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [failReason, setFailReason] = useState('');
  const [actionRunning, setActionRunning] = useState(false);
  const [replayingWebhook, setReplayingWebhook] = useState<string | null>(null);
  const [webhookReplayNotice, setWebhookReplayNotice] = useState<string | null>(null);
  const [resourceReloadNotice, setResourceReloadNotice] = useState<string | null>(null);
  const [resource, setResource] = useState<BatchDetailResource | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [loadedAt, setLoadedAt] = useState(() => Date.now());
  // The query of the lines on screen. The table dims only while other lines load, not on a live reload.
  const [loadedQuery, setLoadedQuery] = useState<ItemsQuery | null>(null);
  const itemsQueryRef = useRef(itemsQuery);
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
    itemsQueryRef.current = FIRST_PAGE;
    setItemsQuery(FIRST_PAGE);
    setItemPageCursors([null]);
    setCustomIdInput('');
    setSelectedSnapshot(null);
    setPendingAction(null);
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
    const query = itemsQueryRef.current;
    setLoading(true);
    setLoadError(null);
    try {
      const next = await loadBatchDetailResource(batches, batchId, {
        items_limit: ITEMS_PAGE_SIZE,
        items_offset: query.offset,
        after_line_number: query.afterLineNumber,
        item_status: query.status === 'all' ? undefined : query.status,
        custom_id: query.customId || undefined,
      });
      if (
        activeBatchIdRef.current === batchId
        && requestGenerationRef.current === requestGeneration
      ) {
        setResourceReloadNotice(null);
        setLoadedAt(Date.now());
        setLoadedQuery(query);
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
          setResourceReloadNotice('The batch could not be refreshed. The page shows the last state that loaded.');
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
  }, [itemsQuery, reloadResource]);

  const job = resource?.kind === 'live' ? resource.job : null;
  const active = job ? isActiveJob(job.status) : false;
  useAutoRefresh(active ? LIVE_MS : 0, () => {
    // A slow load is not stopped by the next tick. The tick waits for it.
    if (loading) return;
    void reloadResource({ preserveCurrentOnError: true }).catch(() => undefined);
  });

  const changeItemsQuery = useCallback((patch: Partial<ItemsQuery>) => {
    const next = { ...itemsQueryRef.current, ...patch };
    itemsQueryRef.current = next;
    setItemsQuery(next);
  }, []);

  const setItemFilter = (status: ItemFilter) => {
    setSelectedSnapshot(null);
    setItemPageCursors([null]);
    changeItemsQuery({ status, offset: 0, afterLineNumber: null });
  };

  // The custom ID box waits for a pause in typing before it loads.
  useEffect(() => {
    const value = customIdInput.trim();
    if (value === itemsQueryRef.current.customId) return;
    const timer = window.setTimeout(() => {
      setSelectedSnapshot(null);
      setItemPageCursors([null]);
      changeItemsQuery({ customId: value, offset: 0, afterLineNumber: null });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [changeItemsQuery, customIdInput]);

  const progress = useMemo(() => (job ? batchProgress({ ...job, total: job.total_items }) : null), [job]);
  // Costs and error groups are sums over every line. Load them again only when the counts move.
  const costBucket = progress && progress.total > 0 ? Math.floor(((progress.done + progress.failed) / progress.total) * 20) : 0;
  const jobId = job?.batch_id ?? '';
  const costs = useReport(
    job ? `batch:costs|${jobId}|${job.status}|${costBucket}` : null,
    ({ signal }) => batches.costs(jobId, signal),
    { maxAgeMs: 300_000 },
  );
  const errors = useReport(
    job && job.failed_items > 0 ? `batch:errors|${jobId}|${job.failed_items}` : null,
    ({ signal }) => batches.errors(jobId, signal),
    { maxAgeMs: 300_000 },
  );

  const isCurrentRoute = (requestBatchId: string, requestRouteGeneration: number) => isCurrentBatchDetailRoute(
    activeBatchIdRef.current,
    routeGenerationRef.current,
    requestBatchId,
    requestRouteGeneration,
  );

  async function runJobAction(kind: Exclude<PendingAction['kind'], 'replay'>) {
    if (!batchId) return;
    const actionBatchId = batchId;
    const routeGeneration = routeGenerationRef.current;
    setActionRunning(true);
    setResourceReloadNotice(null);
    try {
      if (kind === 'cancel') {
        await runMutationAndRefresh(
          () => batches.cancel(actionBatchId),
          async (cancellation) => {
            if (!isCurrentRoute(actionBatchId, routeGeneration)) return;
            setResource((current) => (current ? applyBatchDetailCancellation(current, cancellation) : current));
            await reloadResource({ preserveCurrentOnError: true });
          },
        );
        pushToast({ tone: 'success', title: 'Cancel requested', message: 'The batch stops after the lines that are running now.' });
      } else {
        let message = '';
        if (kind === 'retry_finalization') {
          await batches.retryFinalization(actionBatchId);
          message = 'Finalization starts again.';
        } else if (kind === 'requeue_stale') {
          const result = await batches.requeueStale(actionBatchId);
          message = `${result.requeued_items.toLocaleString()} line${result.requeued_items === 1 ? '' : 's'} went back to the queue.`;
        } else {
          const result = await batches.markFailed(actionBatchId, failReason.trim() || undefined);
          message = `${result.failed_items.toLocaleString()} line${result.failed_items === 1 ? '' : 's'} marked failed.`;
        }
        pushToast({ tone: 'success', title: ACTION_COPY[kind].confirm, message });
        if (isCurrentRoute(actionBatchId, routeGeneration)) {
          await reloadResource({ preserveCurrentOnError: true }).catch(() => undefined);
        }
      }
      if (isCurrentRoute(actionBatchId, routeGeneration)) setPendingAction(null);
    } catch (error: unknown) {
      if (isCurrentRoute(actionBatchId, routeGeneration)) {
        pushToast({ tone: 'error', title: `${ACTION_COPY[kind].confirm} failed`, message: errorMessage(error, 'The action could not be completed.') });
      }
    } finally {
      if (isCurrentRoute(actionBatchId, routeGeneration)) setActionRunning(false);
    }
  }

  async function replayWebhook(eventId: string) {
    if (!batchId || replayingWebhook) return;
    const replayBatchId = batchId;
    const routeGeneration = routeGenerationRef.current;
    setReplayingWebhook(eventId);
    setWebhookReplayNotice(null);
    setResourceReloadNotice(null);
    setPendingAction(null);
    webhookStateGenerationRef.current += 1;
    try {
      const outcome = await runMutationAndRefresh(
        () => batches.replayWebhook(replayBatchId, eventId),
        async (replay) => {
          if (!isCurrentRoute(replayBatchId, routeGeneration)) return;
          webhookStateGenerationRef.current += 1;
          setResource((current) => (current ? replaceBatchDetailWebhookDelivery(current, replay.delivery) : current));
          const refreshed = await batches.webhookDeliveries(replayBatchId);
          if (!isCurrentRoute(replayBatchId, routeGeneration)) return;
          webhookStateGenerationRef.current += 1;
          setResource((current) => (current ? mergeBatchDetailWebhookDeliveries(current, refreshed) : current));
        },
      );
      if (isCurrentRoute(replayBatchId, routeGeneration)) {
        if (outcome.refreshError) {
          setWebhookReplayNotice('The replay is scheduled, but the latest delivery state could not be loaded.');
        } else {
          pushToast({ tone: 'success', title: 'Replay scheduled', message: 'The webhook is sent again.' });
        }
      }
    } catch (error: unknown) {
      if (isCurrentRoute(replayBatchId, routeGeneration)) {
        pushToast({ tone: 'error', title: 'Replay failed', message: errorMessage(error, 'The webhook could not be sent again.') });
      }
    } finally {
      if (isCurrentRoute(replayBatchId, routeGeneration)) {
        setReplayingWebhook((current) => (current === eventId ? null : current));
      }
    }
  }

  const items = useMemo(() => job?.items?.data ?? [], [job]);
  const selectedIndex = selectedItemId ? items.findIndex((item) => item.item_id === selectedItemId) : -1;
  const stepItem = useCallback((direction: -1 | 1) => {
    if (selectedIndex < 0) return;
    const target = items[selectedIndex + direction];
    if (target) setSelectedSnapshot(target);
  }, [items, selectedIndex]);

  const confirmDialog = pendingAction && (
    pendingAction.kind === 'replay' ? (
      <ConfirmDialog
        open
        title="Send this webhook again?"
        description={`The gateway sends the ${pendingAction.delivery.event_type} event to the webhook URL again.`}
        confirmLabel="Replay"
        confirming={replayingWebhook !== null}
        onConfirm={() => void replayWebhook(pendingAction.delivery.event_id)}
        onClose={() => setPendingAction(null)}
      />
    ) : (
      <ConfirmDialog
        open
        title={ACTION_COPY[pendingAction.kind].title}
        description={ACTION_COPY[pendingAction.kind].description}
        confirmLabel={ACTION_COPY[pendingAction.kind].confirm}
        cancelLabel="Keep as is"
        destructive={ACTION_COPY[pendingAction.kind].destructive}
        confirming={actionRunning}
        onConfirm={() => void runJobAction(pendingAction.kind as Exclude<PendingAction['kind'], 'replay'>)}
        onClose={() => setPendingAction(null)}
      >
        {pendingAction.kind === 'mark_failed' && (
          <label className="grid gap-1 text-sm text-gray-700">
            Reason (optional)
            <input
              type="text"
              value={failReason}
              maxLength={500}
              onChange={(event) => setFailReason(event.target.value)}
              placeholder="Marked failed by operator"
              className="control-field rounded-lg border border-gray-200 px-3 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            />
          </label>
        )}
      </ConfirmDialog>
    )
  );

  const resourceBatchId = resource?.kind === 'live' ? resource.job.batch_id : resource?.delivery.batch_id;
  const breadcrumbs = [...navTrail('/batches'), { label: batchId ?? 'Batch job' }];

  if ((loading && !resource && !loadError) || (resourceBatchId && resourceBatchId !== batchId)) {
    return (
      <PageShell breadcrumbs={breadcrumbs} avatar={<EntityAvatar icon={Layers} />} title={<span className="font-mono text-lg">{batchId}</span>}>
        <DetailSkeleton />
      </PageShell>
    );
  }

  if (loadError || !resource) {
    const notFound = loadError instanceof ApiError && loadError.status === 404;
    return (
      <PageShell breadcrumbs={breadcrumbs} avatar={<EntityAvatar icon={Layers} />} title={<span className="font-mono text-lg">{batchId}</span>}>
        <div className="grid gap-3.5">
          <ResourceReloadNotice message={resourceReloadNotice} />
          <div className="rounded-xl border border-gray-200 bg-white p-8 text-center">
            <h2 className="text-base font-semibold text-gray-900">{notFound ? 'Batch job not found' : 'The batch job could not be loaded'}</h2>
            <p className="mt-2 text-sm text-gray-500">
              {notFound
                ? 'There is no batch with this ID, and no stored webhook state for it.'
                : errorMessage(loadError, 'The request could not be completed.')}
            </p>
            <button
              type="button"
              onClick={() => { void reloadResource().catch(() => undefined); }}
              className="control-md mt-4 inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white font-medium text-gray-900 hover:bg-gray-50"
            >
              <RefreshCw className="h-4 w-4" aria-hidden="true" /> Retry
            </button>
          </div>
        </div>
      </PageShell>
    );
  }

  if (resource.kind === 'archived') {
    return (
      <PageShell
        breadcrumbs={[...navTrail('/batches'), { label: resource.delivery.batch_id }]}
        avatar={<EntityAvatar icon={Layers} />}
        title={<span className="font-mono text-lg">{resource.delivery.batch_id}</span>}
        badges={<StatusText label="Archived" tone="idle" />}
        meta="The batch record was cleaned up. Its webhook state stays here until its own retention period ends."
      >
        <div className="grid max-w-xl gap-3.5">
          <ResourceReloadNotice message={resourceReloadNotice} />
          <WebhooksCard
            deliveries={resource.delivery.data}
            capabilities={resource.delivery.capabilities}
            replayingEventId={replayingWebhook}
            notice={webhookReplayNotice}
            onReplay={(delivery) => setPendingAction({ kind: 'replay', delivery })}
          />
        </div>
        {confirmDialog}
      </PageShell>
    );
  }

  const liveJob = resource.job;
  const jobProgress = progress ?? batchProgress({ ...liveJob, total: liveJob.total_items });
  const status = jobStatus(liveJob.status);
  const canCancel = canCancelBatchDetailJob(liveJob);
  const itemsPagination = liveJob.items?.pagination;
  const deliveries = liveJob.webhook_deliveries ?? [];
  const now = loadedAt;

  const menuItems: ActionMenuItem[] = [
    ...(liveJob.capabilities.retry_finalization ? [{ label: 'Retry finalization', hint: 'Write the output files again', onSelect: () => setPendingAction({ kind: 'retry_finalization' }) }] : []),
    ...(liveJob.capabilities.requeue_stale ? [{ label: 'Requeue stale lines', hint: 'For lines whose worker stopped', onSelect: () => setPendingAction({ kind: 'requeue_stale' }) }] : []),
    ...(liveJob.capabilities.mark_failed ? [{ label: 'Mark as failed', danger: true, hint: 'Stop the batch and fail open lines', onSelect: () => { setFailReason(''); setPendingAction({ kind: 'mark_failed' }); } }] : []),
  ];

  const handleItemsNextPage = () => {
    const nextAfterLineNumber = itemsPagination?.next_after_line_number;
    if (nextAfterLineNumber == null) return;
    const nextOffset = itemsQuery.offset + ITEMS_PAGE_SIZE;
    const nextIndex = Math.floor(nextOffset / ITEMS_PAGE_SIZE);
    setSelectedSnapshot(null);
    setItemPageCursors((current) => {
      const next = current.slice(0, nextIndex + 1);
      next[nextIndex] = nextAfterLineNumber;
      return next;
    });
    changeItemsQuery({ offset: nextOffset, afterLineNumber: nextAfterLineNumber });
  };

  const handleItemsPreviousPage = () => {
    const previousOffset = Math.max(0, itemsQuery.offset - ITEMS_PAGE_SIZE);
    const previousIndex = Math.floor(previousOffset / ITEMS_PAGE_SIZE);
    setSelectedSnapshot(null);
    changeItemsQuery({ offset: previousOffset, afterLineNumber: itemPageCursors[previousIndex] ?? null });
  };

  const firstLine = items.length > 0 ? itemsQuery.offset + 1 : 0;
  const lastLine = itemsQuery.offset + items.length;
  const filteredTotal = itemsPagination?.total ?? null;
  const filterOptions = BATCH_ITEM_FILTERS.map((entry) => ({
    key: entry.key,
    label: `${entry.label} ${entry.count(jobProgress).toLocaleString()}`,
  }));
  const selectedItem: BatchJobItem | null = selectedIndex >= 0 ? items[selectedIndex] : selectedSnapshot;

  return (
    <PageShell
      breadcrumbs={[...navTrail('/batches'), { label: liveJob.batch_id }]}
      avatar={<EntityAvatar icon={Layers} />}
      title={(
        <span className="inline-flex items-center gap-1">
          <span className="font-mono text-lg">{liveJob.batch_id}</span>
          <CopyButton value={liveJob.batch_id} label="Copy batch ID" />
        </span>
      )}
      badges={<StatusText label={liveJob.cancel_requested_at && active ? 'Cancelling' : status.label} tone={status.tone} />}
      meta={(
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <EndpointTag label={endpointLabel(liveJob.endpoint)} />
          <span className="text-gray-900">{liveJob.model}</span>
          <span>Team <span className="text-gray-900">{liveJob.team_alias || liveJob.created_by_team_id || 'none'}</span></span>
          {liveJob.created_by_api_key && <span>Key <span className="font-mono text-gray-900">{liveJob.created_by_api_key}</span></span>}
          <span title={fmtDateTime(liveJob.created_at)}>Created {fmtAgo(liveJob.created_at, now)}</span>
        </span>
      )}
      actions={(
        <>
          {active && (
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700" title="This page loads again every 10 seconds while the batch runs">
              <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${resourceReloadNotice ? 'bg-rose-600' : 'bg-emerald-600 motion-safe:animate-pulse'}`} />
              Live
            </span>
          )}
          {canCancel && (
            <Button variant="danger-outline" size="sm" onClick={() => setPendingAction({ kind: 'cancel' })} disabled={actionRunning}>
              Cancel batch
            </Button>
          )}
          <ActionMenu label="More batch actions" items={menuItems} />
        </>
      )}
    >
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3.5">
        <ResourceReloadNotice message={resourceReloadNotice} />
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="max-w-full overflow-x-auto [&_button]:whitespace-nowrap">
            <Segmented<ItemFilter> label="Line status" value={itemsQuery.status} options={filterOptions} onChange={setItemFilter} />
          </div>
          <label className="relative w-full sm:w-56">
            <span className="sr-only">Find a custom ID</span>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
            <input
              type="search"
              value={customIdInput}
              maxLength={256}
              onChange={(event) => setCustomIdInput(event.target.value)}
              placeholder="Custom ID"
              className="control-field-md w-full rounded-lg border border-gray-200 bg-white pl-8 pr-2.5 text-[13px] text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            />
          </label>
        </div>
        {/* The rail stretches to the height of the table, and the table to the rail. */}
        <div className="grid items-stretch gap-3.5 xl:grid-cols-[minmax(0,1fr)_330px]">
          <section aria-label="Batch lines" className="flex min-w-0 flex-col rounded-xl border border-gray-200 bg-white px-2 py-1">
            {items.length === 0 && !loading ? (
              <p className="px-4 py-10 text-center text-sm text-gray-500">
                {itemsQuery.customId
                  ? `No line has the custom ID "${itemsQuery.customId}".`
                  : itemsQuery.status !== 'all'
                    ? 'No lines have this status.'
                    : 'This batch has no lines yet.'}
              </p>
            ) : (
              <BatchItemsTable
                items={items}
                loading={false}
                refreshing={loading && loadedQuery !== itemsQuery}
                selectedId={selectedItemId}
                onSelect={setSelectedSnapshot}
              />
            )}
            <div className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-2.5 py-2 text-[12.5px] text-gray-500">
              <span className="tabular-nums">
                {items.length > 0
                  ? `Lines ${firstLine.toLocaleString()}–${lastLine.toLocaleString()}${filteredTotal !== null ? ` of ${filteredTotal.toLocaleString()}` : ''}`
                  : 'No lines'}
              </span>
              <span className="flex gap-2">
                <button
                  type="button"
                  onClick={handleItemsPreviousPage}
                  disabled={itemsQuery.offset === 0 || loading}
                  className="control-sm rounded-lg border border-gray-200 bg-white text-gray-900 hover:bg-gray-50 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={handleItemsNextPage}
                  disabled={itemsPagination?.next_after_line_number == null || loading}
                  className="control-sm rounded-lg border border-gray-200 bg-white text-gray-900 hover:bg-gray-50 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
                >
                  Next
                </button>
              </span>
            </div>
          </section>
          <aside aria-label="Batch facts" className="flex flex-col gap-3">
            <ProgressCard
              job={liveJob}
              progress={jobProgress}
              costs={costs.data}
              costsError={costs.error}
              onRetryCosts={costs.retry}
              now={now}
            />
            <FailuresCard
              failed={liveJob.failed_items}
              groups={errors.data?.data ?? null}
              loading={errors.loading}
              error={errors.error}
              onRetry={errors.retry}
              onShowFailed={() => setItemFilter('failed')}
            />
            <TimelineCard job={liveJob} progress={jobProgress} now={now} />
            {deliveries.length > 0 && (
              <WebhooksCard
                deliveries={deliveries}
                capabilities={liveJob.capabilities}
                replayingEventId={replayingWebhook}
                notice={webhookReplayNotice}
                onReplay={(delivery) => setPendingAction({ kind: 'replay', delivery })}
              />
            )}
            <FilesCard job={liveJob} />
          </aside>
        </div>
      </div>
      {selectedItem && batchId && (
        <BatchItemDrawer
          batchId={batchId}
          item={selectedItem}
          hasPrevious={selectedIndex > 0}
          hasNext={selectedIndex >= 0 && selectedIndex < items.length - 1}
          onStep={stepItem}
          onClose={() => setSelectedSnapshot(null)}
        />
      )}
      {confirmDialog}
    </PageShell>
  );
}
