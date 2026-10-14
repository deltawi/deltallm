import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, RefreshCw } from 'lucide-react';
import type { Delta } from '../../lib/usageExplorer';

export function Skeleton({ className = '' }: { className?: string }) {
  return <span aria-hidden="true" className={`block animate-pulse rounded bg-gray-100 ${className}`} />;
}

export function TableSkeleton({ rows = 5, columns = 6 }: { rows?: number; columns?: number }) {
  return (
    <div role="status" aria-label="Loading" className="divide-y divide-gray-100">
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex items-center gap-4 px-2.5 py-3">
          {Array.from({ length: columns }, (_, column) => (
            <Skeleton key={column} className={`h-3.5 ${column === 0 ? 'w-40' : 'flex-1'}`} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function Sparkline({ values, color, label }: { values: readonly number[]; color: string; label?: string }) {
  if (values.length < 2) return <svg className="mt-1 h-7 w-full" aria-hidden="true" />;
  const width = 160;
  const height = 28;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const points = values.map((value, index) => [
    (index * width) / (values.length - 1),
    height - 3 - ((height - 6) * (value - min)) / (max - min || 1),
  ]);
  const path = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  const [lastX, lastY] = points[points.length - 1];
  return (
    <svg
      className="mt-1 h-7 w-full overflow-visible"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <path d={`${path} L${width} ${height} L0 ${height}Z`} fill={color} fillOpacity={0.1} />
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      <circle cx={lastX} cy={lastY} r={2.5} fill={color} />
    </svg>
  );
}

const DELTA_CLASSES: Record<Delta['tone'], string> = {
  bad: 'bg-rose-50 text-rose-700',
  good: 'bg-emerald-50 text-emerald-700',
  flat: 'bg-gray-100 text-gray-600',
};

export function DeltaBadge({ delta }: { delta: Delta | null }) {
  if (!delta) return null;
  return (
    <span className={`rounded px-1.5 py-px font-semibold tabular-nums ${DELTA_CLASSES[delta.tone]}`}>{delta.label}</span>
  );
}

interface KpiTileProps {
  label: string;
  value: ReactNode;
  delta?: Delta | null;
  note?: ReactNode;
  spark?: ReactNode;
  to?: string;
  loading?: boolean;
  dimmed?: boolean;
  title?: string;
}

export function KpiTile({ label, value, delta = null, note, spark, to, loading = false, dimmed = false, title }: KpiTileProps) {
  const body = (
    <>
      <span className="text-xs text-gray-600">{label}</span>
      {loading ? (
        <>
          <Skeleton className="mt-1.5 h-6 w-24" />
          <Skeleton className="mt-2 h-3 w-32" />
          <Skeleton className="mt-2 h-6 w-full" />
        </>
      ) : (
        <>
          <span className="truncate text-[22px] font-semibold tabular-nums tracking-tight text-gray-900" title={title}>{value}</span>
          <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-gray-500">
            <DeltaBadge delta={delta} />
            <span className="truncate">{note}</span>
          </span>
          {spark}
        </>
      )}
    </>
  );
  const className = `grid min-w-0 content-start gap-0.5 rounded-xl border border-gray-200 bg-white px-3.5 py-3 transition-opacity ${dimmed ? 'opacity-60' : ''}`;
  if (to && !loading) {
    return (
      <Link to={to} className={`${className} hover:border-gray-300 hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary`}>
        {body}
      </Link>
    );
  }
  return <div className={className} aria-busy={loading}>{body}</div>;
}

interface SegmentedProps<T extends string> {
  label: string;
  value: T;
  options: ReadonlyArray<{ key: T; label: string; disabled?: boolean }>;
  onChange: (value: T) => void;
}

export function Segmented<T extends string>({ label, value, options, onChange }: SegmentedProps<T>) {
  return (
    <div role="group" aria-label={label} className="inline-flex gap-0.5 rounded-lg border border-gray-200 bg-gray-50 p-[3px]">
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          aria-pressed={option.key === value}
          disabled={option.disabled}
          onClick={() => onChange(option.key)}
          className={`rounded-md px-2.5 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed disabled:opacity-40 ${
            option.key === value ? 'bg-white font-semibold text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function PanelError({ message, onRetry, stale = false }: { message: string; onRetry: () => void; stale?: boolean }) {
  return (
    <div role="alert" className="flex items-start justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
      <span className="flex items-start gap-1.5">
        <AlertCircle className="mt-px h-3.5 w-3.5 shrink-0" />
        {stale ? `Could not refresh. Showing the last result. ${message}` : message}
      </span>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex shrink-0 items-center gap-1 font-semibold hover:text-rose-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
      >
        <RefreshCw className="h-3 w-3" /> Retry
      </button>
    </div>
  );
}

export function ShareBar({ share, color, wide = false }: { share: number; color: string; wide?: boolean }) {
  const width = Math.max(0, Math.min(1, share)) * 100;
  return (
    <span className={`inline-block h-1.5 overflow-hidden rounded-sm bg-gray-100 align-middle ${wide ? 'w-full' : 'mr-2 w-[90px]'}`}>
      <span className="block h-full rounded-sm" style={{ width: `${width}%`, background: color }} />
    </span>
  );
}

export function Swatch({ color, round = false }: { color: string; round?: boolean }) {
  return <span aria-hidden="true" className={`mr-2 inline-block h-[9px] w-[9px] align-middle ${round ? 'rounded-full' : 'rounded-sm'}`} style={{ background: color }} />;
}
