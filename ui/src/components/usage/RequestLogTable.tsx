import type { CSSProperties, ReactNode } from 'react';
import type { RequestLog } from '../../lib/api/usageReports';
import { fmtCompact, fmtDuration, fmtSpendValue } from '../../lib/format';
import {
  callTypeLabel,
  logDurationMs,
  logKeyName,
  logProvider,
  logStatusCode,
  relativeTime,
  REQUEST_LOG_COLUMNS,
  utcTimestamp,
  type RequestLogColumn,
} from '../../lib/requestLogs';
import { keyTail } from '../../lib/usageFilters';
import { TableSkeleton } from './ReportBits';

interface RequestLogTableProps {
  logs: readonly RequestLog[];
  loading: boolean;
  refreshing: boolean;
  selectedId: string | null;
  /** Optional columns the viewer added with the Columns menu. */
  columns?: readonly RequestLogColumn[];
  onSelect: (log: RequestLog) => void;
}

const HEAD = 'whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-500';
// The table has a fixed layout, so a long value is cut with "…" and never makes the table wider.
const CELL = 'overflow-hidden text-ellipsis whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px]';
// Columns hide by priority as the screen gets narrower, so the table never scrolls sideways.
const LARGE = 'hidden lg:table-cell';
const WIDE = 'hidden xl:table-cell';
// Each optional column has a set width, and the table gets wider by that much and
// scrolls sideways, so Model, Team and API key keep their room.
const OPTIONAL_WIDTH: Record<RequestLogColumn, { className: string; px: number }> = {
  person: { className: 'w-[150px]', px: 150 },
  organization: { className: 'w-[130px]', px: 130 },
  endpoint: { className: 'w-[120px]', px: 120 },
  deployment: { className: 'w-[150px]', px: 150 },
  end_user: { className: 'w-[120px]', px: 120 },
  tags: { className: 'w-[140px]', px: 140 },
  error: { className: 'w-[130px]', px: 130 },
  cached: { className: 'w-[96px] text-right', px: 96 },
  request_id: { className: 'w-[180px]', px: 180 },
};
// Room for the default columns at the xl breakpoint, where optional columns show.
const BASE_TABLE_PX = 1000;
const MUTED = <span className="text-gray-400">—</span>;

function optionalCell(column: RequestLogColumn, log: RequestLog): ReactNode {
  switch (column) {
    case 'person':
      return log.user_email || log.user || MUTED;
    case 'organization':
      return log.organization_name || log.organization_id || MUTED;
    case 'endpoint':
      return callTypeLabel(log.call_type);
    case 'deployment':
      return log.deployment_model || log.api_base || MUTED;
    case 'end_user':
      return log.end_user || MUTED;
    case 'tags':
      return log.request_tags?.length ? log.request_tags.join(', ') : MUTED;
    case 'error':
      return log.error_type || MUTED;
    case 'cached':
      return fmtCompact(log.prompt_tokens_cached ?? 0);
    case 'request_id':
      return <span className="font-mono text-[11.5px]">{log.request_id}</span>;
  }
}

function optionalTitle(column: RequestLogColumn, log: RequestLog): string | undefined {
  if (column === 'tags') return log.request_tags?.join(', ') || undefined;
  if (column === 'request_id') return log.request_id;
  if (column === 'deployment') return log.deployment_model || log.api_base || undefined;
  if (column === 'person') return log.user_email || log.user || undefined;
  return undefined;
}
export default function RequestLogTable({ logs, loading, refreshing, selectedId, columns = [], onSelect }: RequestLogTableProps) {
  if (loading) return <TableSkeleton rows={8} columns={9} />;
  const optional = REQUEST_LOG_COLUMNS.filter((column) => columns.includes(column.key));
  const minWidth = optional.length ? BASE_TABLE_PX + optional.reduce((sum, column) => sum + OPTIONAL_WIDTH[column.key].px, 0) : 0;
  return (
    <div className={`overflow-x-auto transition-opacity ${refreshing ? 'opacity-60' : ''}`} aria-busy={refreshing}>
      <table
        className={`w-full table-fixed border-collapse text-[12.5px] tabular-nums ${optional.length ? 'xl:min-w-[var(--request-table-min)]' : ''}`}
        style={optional.length ? ({ '--request-table-min': `${minWidth}px` } as CSSProperties) : undefined}
      >
        <thead>
          <tr>
            <th className={`${HEAD} w-[88px] sm:w-[96px]`}>Time</th>
            <th className={`${HEAD} w-[60px] sm:w-[72px]`}>Status</th>
            <th className={HEAD}>Model</th>
            <th className={`${HEAD} ${WIDE}`}>Team</th>
            <th className={`${HEAD} ${LARGE}`}>API key</th>
            <th className={`${HEAD} ${WIDE} w-[132px] text-right`}>Tokens in → out</th>
            <th className={`${HEAD} w-[76px] sm:w-[88px] text-right`}>Cost</th>
            <th className={`${HEAD} ${LARGE} w-[84px] text-right`}>Duration</th>
            <th className={`${HEAD} ${WIDE} w-[68px]`}>Cache</th>
            {optional.map((column) => (
              <th key={column.key} className={`${HEAD} ${WIDE} ${OPTIONAL_WIDTH[column.key].className}`}>{column.label}</th>
            ))}
            {/* Room for the Columns button, which sits over this corner. */}
            <th className={`${HEAD} hidden w-9 min-[880px]:table-cell`}><span className="sr-only">Columns</span></th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => {
            const failed = log.status === 'error';
            const duration = logDurationMs(log);
            const selected = selectedId === log.id;
            return (
              <tr
                key={log.id}
                tabIndex={0}
                aria-selected={selected}
                onClick={() => onSelect(log)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(log);
                  }
                }}
                className={`cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${selected ? 'bg-brand-primary-soft' : 'hover:bg-gray-50'}`}
              >
                <td className={CELL} title={utcTimestamp(log.start_time)}>{relativeTime(log.start_time)}</td>
                <td className={CELL}>
                  <span className={`relative inline-flex items-center gap-1.5 font-medium ${failed ? 'text-rose-700' : 'text-gray-900'}`}>
                    <span aria-hidden="true" className={`h-[7px] w-[7px] rounded-full ${failed ? 'bg-rose-600' : 'bg-emerald-600'}`} />
                    {logStatusCode(log)}
                    <span className="sr-only">{failed ? 'failed' : 'succeeded'}</span>
                  </span>
                </td>
                <td className={CELL}>
                  <span className="flex min-w-0 items-baseline gap-1" title={log.model || undefined}>
                    <span className="min-w-0 truncate">{log.model || '—'}</span>
                    <span className="hidden shrink-0 text-xs text-gray-500 sm:inline">{logProvider(log)}</span>
                  </span>
                </td>
                <td className={`${CELL} ${WIDE}`}>
                  <span className="block truncate">{log.team_alias || log.team_id || <span className="text-gray-400">No team</span>}</span>
                </td>
                <td className={`${CELL} ${LARGE}`}>
                  <span className="flex min-w-0 items-baseline gap-1">
                    <span className="min-w-0 truncate">{logKeyName(log)}</span>
                    {log.key_name && <span className="shrink-0 font-mono text-[11px] text-gray-500">{keyTail(log.api_key)}</span>}
                  </span>
                </td>
                <td className={`${CELL} ${WIDE} text-right`}>
                  {fmtCompact(log.prompt_tokens)} → {fmtCompact(log.completion_tokens)}
                </td>
                <td className={`${CELL} text-right`}>{log.spend ? fmtSpendValue(log.spend) : '—'}</td>
                <td className={`${CELL} ${LARGE} text-right ${duration !== null && duration > 5000 ? 'text-rose-700' : ''}`}>
                  {fmtDuration(duration)}
                </td>
                <td className={`${CELL} ${WIDE}`}>
                  <span className={`rounded-full px-[7px] py-px text-[11px] ${log.cache_hit ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-600'}`}>
                    {log.cache_hit ? 'Hit' : 'Miss'}
                  </span>
                </td>
                {optional.map((column) => (
                  <td
                    key={column.key}
                    title={optionalTitle(column.key, log)}
                    className={`${CELL} ${WIDE} text-gray-700 ${OPTIONAL_WIDTH[column.key].className}`}
                  >
                    {optionalCell(column.key, log)}
                  </td>
                ))}
                <td className={`${CELL} hidden min-[880px]:table-cell`} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
