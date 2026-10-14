import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, CheckCircle2, X } from 'lucide-react';
import type { AttentionItem } from '../../lib/liveDashboard';
import { Skeleton } from '../usage/ReportBits';

interface AttentionCardProps {
  items: readonly AttentionItem[];
  dismissed: Readonly<Record<string, number>>;
  loading: boolean;
  onDismiss: (id: string) => void;
  onRestore: (id: string) => void;
}

function AttentionRow({
  item,
  dismissed,
  onDismiss,
  onRestore,
}: {
  item: AttentionItem;
  dismissed: boolean;
  onDismiss: (id: string) => void;
  onRestore: (id: string) => void;
}) {
  const critical = item.level === 'critical';
  return (
    <li className={`flex items-start gap-2.5 border-t border-gray-200 px-[18px] py-2.5 ${dismissed ? 'opacity-60' : ''}`}>
      <span
        aria-label={critical ? 'Critical' : 'Warning'}
        className={`mt-[3px] grid h-[18px] w-[18px] flex-none place-items-center rounded-full text-[11px] font-bold ${
          critical ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-700'
        }`}
      >
        !
      </span>
      <div className="min-w-0 flex-1">
        <b className="block text-sm font-semibold text-gray-900">{item.title}</b>
        <p className="text-[12.5px] text-gray-500">{item.detail}</p>
      </div>
      <div className="flex flex-none items-center gap-1.5">
        <Link
          to={item.action.to}
          className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs text-gray-900 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
        >
          {item.action.label} <ArrowRight className="h-3.5 w-3.5" />
        </Link>
        {dismissed ? (
          <button
            type="button"
            onClick={() => onRestore(item.id)}
            className="rounded-lg px-2 py-1.5 text-xs font-medium text-brand-primary-ink hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
          >
            Restore
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onDismiss(item.id)}
            aria-label={`Dismiss: ${item.title}`}
            title="Dismiss. It comes back if this gets worse."
            className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-50 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
    </li>
  );
}

export default function AttentionCard({ items, dismissed, loading, onDismiss, onRestore }: AttentionCardProps) {
  const [showDismissed, setShowDismissed] = useState(false);
  const open = items.filter((item) => !(item.id in dismissed));
  const hidden = items.filter((item) => item.id in dismissed);
  const shown = showDismissed ? [...open, ...hidden] : open;

  return (
    <section className="rounded-xl border border-gray-200 bg-white py-1.5" aria-label="Needs attention" aria-busy={loading}>
      <div className="flex items-center justify-between gap-3 px-[18px] pb-1 pt-2">
        <h2 className="text-sm font-semibold text-gray-900">Needs attention</h2>
        <span className="flex items-center gap-3 text-xs text-gray-500">
          {loading && open.length === 0 ? 'Checking' : `${open.length} item${open.length === 1 ? '' : 's'}`}
          {hidden.length > 0 && (
            <button
              type="button"
              onClick={() => setShowDismissed((value) => !value)}
              className="font-medium text-brand-primary-ink hover:text-brand-primary-ink-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
            >
              {showDismissed ? 'Hide dismissed' : `Show ${hidden.length} dismissed`}
            </button>
          )}
        </span>
      </div>
      {loading && open.length === 0 ? (
        <div className="grid gap-2 border-t border-gray-200 px-[18px] py-3" role="status" aria-label="Checking for problems">
          <Skeleton className="h-3.5 w-1/3" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      ) : shown.length === 0 ? (
        <p className="flex items-center gap-2 border-t border-gray-200 px-[18px] py-3 text-sm text-gray-600">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
          Nothing needs attention right now.
        </p>
      ) : (
        <ul>
          {shown.map((item) => (
            <AttentionRow key={item.id} item={item} dismissed={item.id in dismissed} onDismiss={onDismiss} onRestore={onRestore} />
          ))}
        </ul>
      )}
    </section>
  );
}
