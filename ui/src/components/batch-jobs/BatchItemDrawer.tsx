import { useEffect, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import SideDrawer from '../SideDrawer';
import { batches, type BatchJobItem, type BatchJobItemDetail } from '../../lib/api/batches';
import { elapsedMs, fmtCost, fmtDateTime, itemStatus } from '../../lib/batchJobs';
import { fmtDuration } from '../../lib/format';
import { useReport } from '../../lib/useReport';
import { TextTabs } from '../admin/shells';
import { PanelError, Skeleton } from '../usage/ReportBits';
import { CopyButton, StatusText } from './BatchBits';

type PayloadTab = 'request' | 'response' | 'error' | 'usage';

const TABS: ReadonlyArray<{ id: PayloadTab; label: string; field: keyof BatchJobItemDetail; flag: keyof BatchJobItem }> = [
  { id: 'request', label: 'Request', field: 'request_body', flag: 'has_request_body' },
  { id: 'response', label: 'Response', field: 'response_body', flag: 'has_response_body' },
  { id: 'error', label: 'Error', field: 'error_body', flag: 'has_error_body' },
  { id: 'usage', label: 'Usage', field: 'usage', flag: 'has_usage' },
];

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wider text-gray-500">{label}</dt>
      <dd className="mt-0.5 truncate text-[13px] tabular-nums text-gray-900">{children}</dd>
    </div>
  );
}

interface BatchItemDrawerProps {
  batchId: string;
  item: BatchJobItem;
  hasPrevious: boolean;
  hasNext: boolean;
  onStep: (direction: -1 | 1) => void;
  onClose: () => void;
}

export default function BatchItemDrawer({ batchId, item, hasPrevious, hasNext, onStep, onClose }: BatchItemDrawerProps) {
  const detail = useReport<BatchJobItemDetail>(
    `batch:item|${batchId}|${item.item_id}|${item.status}|${item.attempts}`,
    ({ signal }) => batches.getItem(batchId, item.item_id, signal),
    { maxAgeMs: 300_000 },
  );
  const available = TABS.filter((tab) => item[tab.flag]);
  const preferred: PayloadTab = item.status === 'failed' && item.has_error_body ? 'error' : 'request';
  // A picked tab belongs to one line. Each new line opens on its most useful tab.
  const [picked, setPicked] = useState<{ itemId: string; tab: PayloadTab } | null>(null);
  const tab = picked?.itemId === item.item_id ? picked.tab : preferred;
  const setTab = (next: PayloadTab) => setPicked({ itemId: item.item_id, tab: next });
  const activeTab = available.some((entry) => entry.id === tab) ? tab : available[0]?.id ?? 'request';

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || isTyping(event.target)) return;
      // In the payload, the arrow keys scroll the text.
      if (event.target instanceof HTMLElement && event.target.tagName === 'PRE') return;
      if (event.key === 'ArrowDown' && hasNext) {
        event.preventDefault();
        onStep(1);
      } else if (event.key === 'ArrowUp' && hasPrevious) {
        event.preventDefault();
        onStep(-1);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [hasNext, hasPrevious, onStep]);

  const status = itemStatus(item.status);
  const value = detail.data ? detail.data[TABS.find((entry) => entry.id === activeTab)!.field] : null;
  const runtime = elapsedMs(detail.data?.started_at, detail.data?.completed_at);

  return (
    <SideDrawer
      title={`Line ${item.line_number}`}
      description={item.custom_id ? `Custom ID ${item.custom_id}` : 'No custom ID'}
      saving={false}
      size="md"
      overlayLabel="Close line details"
      onClose={onClose}
      footer={(
        <>
          <span className="mr-auto text-xs text-gray-500">Use ↑ and ↓ to move between lines.</span>
          <button
            type="button"
            onClick={() => onStep(-1)}
            disabled={!hasPrevious}
            className="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-900 hover:bg-gray-50 disabled:opacity-50"
          >
            <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" /> Previous line
          </button>
          <button
            type="button"
            onClick={() => onStep(1)}
            disabled={!hasNext}
            className="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-900 hover:bg-gray-50 disabled:opacity-50"
          >
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" /> Next line
          </button>
        </>
      )}
    >
      <div className="grid gap-4">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border border-gray-200 bg-white p-4 sm:grid-cols-3">
          <Fact label="Status"><StatusText label={status.label} tone={status.tone} /></Fact>
          <Fact label="Attempts">{item.attempts}</Fact>
          <Fact label="Billed">{item.billed_cost ? fmtCost(item.billed_cost) : '—'}</Fact>
          <Fact label="Started">{detail.data ? fmtDateTime(detail.data.started_at) : <Skeleton className="h-3.5 w-20" />}</Fact>
          <Fact label="Finished">{detail.data ? fmtDateTime(detail.data.completed_at) : <Skeleton className="h-3.5 w-20" />}</Fact>
          <Fact label="Run time">{detail.data ? fmtDuration(runtime) : <Skeleton className="h-3.5 w-12" />}</Fact>
        </dl>
        {item.last_error && (
          <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[12.5px] text-rose-800">{item.last_error}</p>
        )}
        {available.length === 0 ? (
          <p className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500">This line has no stored request or response.</p>
        ) : (
          <section className="rounded-xl border border-gray-200 bg-white" aria-label="Line payload">
            <div className="flex items-center justify-between gap-2 border-b border-gray-200 pr-2">
              <div className="flex gap-4 px-2 pt-3">
                <TextTabs
                  label="Line payload"
                  items={available.map((entry) => ({ id: entry.id, label: entry.label }))}
                  active={activeTab}
                  onChange={setTab}
                />
              </div>
              {value != null && <CopyButton value={JSON.stringify(value, null, 2)} label="Copy as JSON" />}
            </div>
            <div className="p-3" role="tabpanel" aria-label={`${TABS.find((entry) => entry.id === activeTab)?.label ?? ''} payload`}>
              {detail.error && <PanelError message={detail.error} onRetry={detail.retry} />}
              {detail.loading && (
                <div className="grid gap-2" role="status" aria-label="Loading">
                  <Skeleton className="h-3.5 w-2/3" />
                  <Skeleton className="h-3.5 w-1/2" />
                  <Skeleton className="h-3.5 w-3/4" />
                </div>
              )}
              {detail.data && (
                <pre tabIndex={0} className="max-h-[55vh] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary overflow-auto whitespace-pre-wrap break-words rounded-lg bg-gray-50 p-3 font-mono text-[12px] text-gray-800">
                  {value == null ? 'Nothing stored.' : JSON.stringify(value, null, 2)}
                </pre>
              )}
            </div>
          </section>
        )}
      </div>
    </SideDrawer>
  );
}
