import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Search,
  Copy,
  Check,
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
  Eye,
  Ban,
  Inbox,
  Clock,
  type LucideIcon,
} from 'lucide-react';
import clsx from 'clsx';
import type { BatchJobListItem } from '../../lib/api';

type Pagination = {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

type StatusTab = { value: string; label: string };

type Props = {
  items: BatchJobListItem[];
  loading: boolean;
  pagination?: Pagination | null;
  pageSize: number;
  onPageChange: (offset: number) => void;
  searchValue: string;
  onSearchChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (status: string) => void;
  statusTabs: StatusTab[];
  emptyMessage: string;
  onView: (batchId: string) => void;
  onCancel?: (batchId: string) => void;
};

const STATUS_STYLES: Record<string, string> = {
  validating: 'bg-purple-50 text-purple-700 border-purple-200',
  queued: 'bg-blue-50 text-blue-700 border-blue-200',
  in_progress: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  finalizing: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  completed: 'bg-green-50 text-green-700 border-green-200',
  failed: 'bg-red-50 text-red-700 border-red-200',
  cancelled: 'bg-gray-100 text-gray-700 border-gray-200',
  expired: 'bg-gray-100 text-gray-700 border-gray-200',
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

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium border', STATUS_STYLES[status] || 'bg-gray-100 text-gray-700 border-gray-200')}>
      {STATUS_LABELS[status] || status}
    </span>
  );
}

function MultiProgressBar({ row }: { row: BatchJobListItem }) {
  const total = row.total_items || 0;
  const completed = row.completed_items || 0;
  const failed = row.failed_items || 0;
  const inProgress = row.in_progress_items || 0;

  if (row.status === 'queued' || total === 0) {
    return (
      <div className="w-full mt-3">
        <div className="h-2 w-full bg-gray-100 rounded-full overflow-hidden" />
        <div className="mt-1.5 flex justify-between items-center text-xs text-gray-500">
          <span>{row.status === 'queued' ? 'Pending' : '--'}</span>
          <span>0/{total}</span>
        </div>
      </div>
    );
  }

  const completedPct = (completed / total) * 100;
  const failedPct = (failed / total) * 100;
  const inProgressPct = (inProgress / total) * 100;
  const totalProcessed = completed + failed;
  const overallPct = Math.round((totalProcessed / total) * 100);

  return (
    <div className="w-full mt-3">
      <div className="h-2 w-full bg-gray-100 rounded-full overflow-hidden flex">
        {completedPct > 0 && <div style={{ width: `${completedPct}%` }} className="bg-green-500" />}
        {failedPct > 0 && <div style={{ width: `${failedPct}%` }} className="bg-red-500" />}
        {inProgressPct > 0 && <div style={{ width: `${inProgressPct}%` }} className="bg-yellow-400" />}
      </div>
      <div className="mt-1.5 flex justify-between items-center text-xs">
        <span className="text-gray-500">{totalProcessed}/{total}</span>
        <span className="font-medium text-gray-700">{overallPct}%</span>
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

export default function BatchJobsMobileList({
  items,
  loading,
  pagination,
  pageSize,
  onPageChange,
  searchValue,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
  statusTabs,
  emptyMessage,
  onView,
  onCancel,
}: Props) {
  const [actionSheetFor, setActionSheetFor] = useState<BatchJobListItem | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | null>(null);
  const copiedTimer = useRef<number | null>(null);
  const sheetTitleId = 'batch-jobs-action-sheet-title';
  const sheetReturnFocus = useRef<HTMLElement | null>(null);
  const sheetFirstActionRef = useRef<HTMLButtonElement | null>(null);

  const total = pagination?.total ?? items.length;
  const offset = pagination?.offset ?? 0;
  const limit = pagination?.limit ?? pageSize;
  const hasMore = pagination?.has_more ?? false;
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, limit)));
  const currentPage = Math.floor(offset / Math.max(1, limit)) + 1;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + items.length, total);

  const showToast = (msg: string) => {
    setToast(msg);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 1800);
  };

  useEffect(() => {
    return () => {
      if (toastTimer.current) window.clearTimeout(toastTimer.current);
      if (copiedTimer.current) window.clearTimeout(copiedTimer.current);
    };
  }, []);

  const handleCopy = async (batchId: string) => {
    try {
      await navigator.clipboard.writeText(batchId);
      setCopiedId(batchId);
      showToast('Batch ID copied');
      if (copiedTimer.current) window.clearTimeout(copiedTimer.current);
      copiedTimer.current = window.setTimeout(() => {
        setCopiedId((current) => (current === batchId ? null : current));
      }, 1500);
    } catch {
      showToast('Copy failed');
    }
  };

  const openSheet = (row: BatchJobListItem, trigger: HTMLElement | null) => {
    sheetReturnFocus.current = trigger;
    setActionSheetFor(row);
  };

  const closeSheet = () => {
    setActionSheetFor(null);
    const restoreTo = sheetReturnFocus.current;
    sheetReturnFocus.current = null;
    if (restoreTo && typeof restoreTo.focus === 'function') {
      window.setTimeout(() => restoreTo.focus(), 0);
    }
  };

  useEffect(() => {
    if (!actionSheetFor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeSheet();
      }
    };
    window.addEventListener('keydown', onKey);
    const focusTimer = window.setTimeout(() => {
      sheetFirstActionRef.current?.focus();
    }, 0);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.clearTimeout(focusTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionSheetFor]);

  const sheetActions = useMemo(() => {
    if (!actionSheetFor) return [] as { label: string; icon: LucideIcon; tone?: 'danger'; onClick: () => void }[];
    const row = actionSheetFor;
    const list: { label: string; icon: LucideIcon; tone?: 'danger'; onClick: () => void }[] = [];
    list.push({
      label: 'View details',
      icon: Eye,
      onClick: () => {
        closeSheet();
        onView(row.batch_id);
      },
    });
    list.push({
      label: 'Copy batch ID',
      icon: Copy,
      onClick: () => {
        closeSheet();
        handleCopy(row.batch_id);
      },
    });
    if (onCancel && row.capabilities?.cancel) {
      list.push({
        label: 'Cancel job',
        icon: Ban,
        tone: 'danger',
        onClick: () => {
          closeSheet();
          onCancel(row.batch_id);
        },
      });
    }
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionSheetFor, onCancel]);

  return (
    <div className="relative">
      {/* Search */}
      <div className="mb-3">
        <div className="relative">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search batch ID or model..."
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full bg-gray-100 border-transparent rounded-lg py-2 pl-9 pr-4 text-sm focus:bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-200 outline-none transition-all"
          />
        </div>
      </div>

      {/* Status filter chips */}
      <div className="mb-3 -mx-1 px-1 flex gap-2 overflow-x-auto pb-1 scrollbar-hide">
        {statusTabs.map((tab) => {
          const selected = statusFilter === tab.value;
          return (
            <button
              key={tab.value || 'all'}
              type="button"
              onClick={() => onStatusFilterChange(tab.value)}
              aria-pressed={selected}
              className={clsx(
                'whitespace-nowrap px-3 py-1.5 rounded-full text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
                selected
                  ? 'bg-gray-900 text-white'
                  : 'bg-white border border-gray-200 text-gray-700 hover:bg-gray-50',
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* List */}
      <div className="space-y-3">
        {loading && items.length === 0 ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="bg-white rounded-xl border border-gray-200 p-4 animate-pulse">
                <div className="flex items-center justify-between mb-3">
                  <div className="h-4 bg-gray-200 rounded-full w-20" />
                  <div className="h-4 bg-gray-100 rounded w-16" />
                </div>
                <div className="h-4 bg-gray-200 rounded w-1/2 mb-2" />
                <div className="h-2 bg-gray-100 rounded w-full" />
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl border border-gray-200">
            <div className="w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
              <Inbox className="w-6 h-6 text-gray-400" />
            </div>
            <p className="text-gray-500 text-sm">{emptyMessage}</p>
          </div>
        ) : (
          items.map((job) => {
            const justCopied = copiedId === job.batch_id;
            const cost = typeof job.total_cost === 'number' ? `$${job.total_cost.toFixed(4)}` : '$0.0000';
            const duration = formatDuration(job.started_at, job.completed_at);
            const created = formatDate(job.created_at);

            return (
              <div
                key={job.batch_id}
                className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm relative active:bg-gray-50 transition-colors cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset"
                role="button"
                tabIndex={0}
                onClick={() => onView(job.batch_id)}
                onKeyDown={(e) => {
                  if (e.target !== e.currentTarget) return;
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onView(job.batch_id);
                  }
                }}
                aria-label={`View details for batch ${job.batch_id}`}
              >
                {/* Top row: status + ID + actions */}
                <div className="flex items-center justify-between mb-3 gap-2">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <StatusBadge status={job.status} />
                    <code className="font-mono text-xs text-gray-500 truncate">
                      {job.batch_id}
                    </code>
                    <button
                      type="button"
                      className="text-gray-400 hover:text-gray-600 p-1 -m-1 rounded shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                      onClick={(e) => { e.stopPropagation(); handleCopy(job.batch_id); }}
                      aria-label="Copy batch id"
                    >
                      {justCopied ? <Check className="w-3.5 h-3.5 text-green-600" /> : <Copy className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                  <button
                    type="button"
                    className="p-1 -mr-1 text-gray-400 hover:text-gray-600 active:bg-gray-100 rounded shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                    onClick={(e) => { e.stopPropagation(); openSheet(job, e.currentTarget); }}
                    aria-label="More actions"
                    aria-haspopup="dialog"
                  >
                    <MoreHorizontal className="w-5 h-5" />
                  </button>
                </div>

                {/* Model + Team */}
                <div className="flex flex-col gap-1.5 mb-1">
                  <h3 className="font-semibold text-gray-900 text-sm truncate">{job.model || '--'}</h3>
                  {job.team_alias && (
                    <div className="flex items-center">
                      <span className="text-xs text-gray-600 bg-gray-100 px-2 py-0.5 rounded-md truncate max-w-full">
                        {job.team_alias}
                      </span>
                    </div>
                  )}
                </div>

                {/* Progress */}
                <MultiProgressBar row={job} />

                {/* Footer */}
                <div className="mt-4 pt-3 border-t border-gray-100 flex items-center justify-between text-xs text-gray-500 gap-2">
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="font-medium text-gray-700 shrink-0">{cost}</span>
                    <span className="truncate">{created}</span>
                  </div>
                  {job.started_at ? (
                    <div className="flex items-center gap-1.5 shrink-0">
                      <Clock className="w-3.5 h-3.5" />
                      <span>{duration}</span>
                    </div>
                  ) : (
                    <span className="shrink-0">--</span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Pagination */}
      <nav className="mt-4 bg-white/95 backdrop-blur border border-gray-200 rounded-xl px-3 py-2.5 flex items-center justify-between">
          <span className="text-xs text-gray-500">
            {total === 0 ? 'No results' : `${rangeStart}–${rangeEnd} of ${total}`}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onPageChange(Math.max(0, offset - limit))}
              disabled={offset <= 0 || loading}
              aria-label="Previous page"
              className="w-10 h-10 -my-2 flex items-center justify-center rounded-lg border border-gray-200 text-gray-600 disabled:text-gray-300 disabled:bg-gray-50 active:bg-gray-100"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-xs text-gray-700 font-medium tabular-nums min-w-[44px] text-center">
              {currentPage} / {totalPages}
            </span>
            <button
              type="button"
              onClick={() => onPageChange(offset + limit)}
              disabled={!hasMore || loading}
              aria-label="Next page"
              className="w-10 h-10 -my-2 flex items-center justify-center rounded-lg border border-gray-200 text-gray-600 disabled:text-gray-300 disabled:bg-gray-50 active:bg-gray-100"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </nav>

      {/* Action sheet */}
      {actionSheetFor && (
        <div
          className="fixed inset-0 z-40 bg-black/40 flex items-end sm:items-center justify-center"
          onClick={closeSheet}
          role="dialog"
          aria-modal="true"
          aria-labelledby={sheetTitleId}
        >
          <div
            className="bg-white w-full sm:w-80 sm:rounded-2xl rounded-t-2xl shadow-xl p-2 pb-4 sm:pb-2 max-w-md"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-3 pt-2 pb-3 border-b border-gray-100">
              <div id={sheetTitleId} className="text-sm font-semibold text-gray-900 truncate">
                {actionSheetFor.model || 'Batch job'}
              </div>
              <div className="text-xs text-gray-500 font-mono mt-0.5 truncate">
                {actionSheetFor.batch_id}
              </div>
            </div>
            <div className="py-1">
              {sheetActions.map((action, idx) => {
                const Icon = action.icon;
                return (
                  <button
                    key={action.label}
                    ref={idx === 0 ? sheetFirstActionRef : undefined}
                    type="button"
                    onClick={action.onClick}
                    className={clsx(
                      'w-full flex items-center gap-3 px-4 py-3 text-sm rounded-lg active:bg-gray-100 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
                      action.tone === 'danger' ? 'text-red-600' : 'text-gray-700',
                    )}
                  >
                    <Icon className="w-4 h-4" />
                    <span className="font-medium">{action.label}</span>
                  </button>
                );
              })}
              <button
                type="button"
                onClick={closeSheet}
                className="w-full mt-1 px-4 py-3 text-sm font-medium text-gray-500 rounded-lg active:bg-gray-100 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div
          className="fixed left-1/2 -translate-x-1/2 bottom-6 z-50 bg-gray-900 text-white text-xs font-medium px-3 py-2 rounded-lg shadow-lg pointer-events-none"
          role="status"
        >
          {toast}
        </div>
      )}
    </div>
  );
}
