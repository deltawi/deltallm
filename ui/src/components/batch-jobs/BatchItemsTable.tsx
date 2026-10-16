import type { BatchJobItem } from '../../lib/api/batches';
import { fmtCost, itemStatus } from '../../lib/batchJobs';
import { TableSkeleton } from '../usage/ReportBits';
import { StatusText } from './BatchBits';

const HEAD = 'whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-500';
const CELL = 'whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] align-middle';

interface BatchItemsTableProps {
  items: BatchJobItem[];
  loading: boolean;
  refreshing: boolean;
  selectedId: string | null;
  onSelect: (item: BatchJobItem) => void;
}

export default function BatchItemsTable({ items, loading, refreshing, selectedId, onSelect }: BatchItemsTableProps) {
  if (loading) return <TableSkeleton rows={10} columns={5} />;
  return (
    <div className={`overflow-x-auto transition-opacity ${refreshing ? 'opacity-70' : ''}`} aria-busy={refreshing}>
      <table className="w-full table-fixed border-collapse text-[12.5px] tabular-nums">
        <thead>
          <tr>
            <th className={`${HEAD} w-[64px] text-right`}>Line</th>
            <th className={`${HEAD} w-[28%]`}>Custom ID</th>
            <th className={`${HEAD} w-[96px]`}>Status</th>
            <th className={`${HEAD} hidden w-[76px] text-right sm:table-cell`}>Attempts</th>
            <th className={`${HEAD} w-[88px] text-right`}>Cost</th>
            <th className={`${HEAD} hidden md:table-cell`}>Error</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const status = itemStatus(item.status);
            const selected = item.item_id === selectedId;
            return (
              <tr
                key={item.item_id}
                tabIndex={0}
                aria-current={selected ? 'true' : undefined}
                aria-label={`Line ${item.line_number}, ${status.label}. Open details`}
                onClick={() => onSelect(item)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(item);
                  }
                }}
                className={`cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${selected ? 'bg-brand-primary-soft' : 'hover:bg-gray-50'}`}
              >
                <td className={`${CELL} text-right font-mono text-[12px] text-gray-500`}>{item.line_number}</td>
                <td className={`${CELL} truncate font-mono text-[12px] text-gray-900`} title={item.custom_id ?? undefined}>{item.custom_id || <span className="text-gray-400">—</span>}</td>
                <td className={CELL}><StatusText label={status.label} tone={status.tone} /></td>
                <td className={`${CELL} hidden text-right text-gray-700 sm:table-cell`}>{item.attempts}</td>
                <td className={`${CELL} text-right text-gray-900`}>{item.billed_cost ? fmtCost(item.billed_cost) : <span className="text-gray-400">—</span>}</td>
                <td className={`${CELL} hidden truncate md:table-cell ${item.last_error ? 'text-rose-700' : 'text-gray-400'}`} title={item.last_error ?? undefined}>
                  {item.last_error || '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
