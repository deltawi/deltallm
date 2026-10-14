import { RefreshCw } from 'lucide-react';
import { AUTO_REFRESH_OPTIONS, type AutoRefreshMs } from '../../lib/useAutoRefresh';

interface RefreshControlProps {
  updatedAt: number | null;
  refreshing: boolean;
  failed?: boolean;
  onRefresh: () => void;
  autoRefreshMs?: AutoRefreshMs;
  onAutoRefreshChange?: (value: AutoRefreshMs) => void;
  label?: string;
}

export default function RefreshControl({
  updatedAt,
  refreshing,
  failed = false,
  onRefresh,
  autoRefreshMs,
  onAutoRefreshChange,
  label,
}: RefreshControlProps) {
  const time = updatedAt ? new Date(updatedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : null;
  return (
    <div className="inline-flex items-stretch overflow-hidden rounded-lg border border-gray-300 bg-white text-xs text-gray-900">
      <button
        type="button"
        onClick={onRefresh}
        title="Load the latest data now"
        className="inline-flex items-center gap-1.5 whitespace-nowrap px-2.5 py-1.5 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary"
      >
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${failed ? 'bg-rose-600' : 'bg-emerald-600'}`} />
        {label ?? (refreshing ? 'Updating' : time ? `Updated ${time}` : 'Loading')}
        <RefreshCw className={`h-3.5 w-3.5 text-gray-500 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
        <span className="sr-only">Refresh now</span>
      </button>
      {onAutoRefreshChange && autoRefreshMs !== undefined && (
        <select
          value={autoRefreshMs}
          onChange={(event) => onAutoRefreshChange(Number(event.target.value) as AutoRefreshMs)}
          aria-label="Auto refresh"
          className="border-0 border-l border-gray-300 bg-white py-1.5 pl-2 pr-7 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-brand-primary"
        >
          {AUTO_REFRESH_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      )}
    </div>
  );
}
