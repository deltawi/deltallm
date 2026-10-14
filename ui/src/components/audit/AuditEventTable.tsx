import type { ReactNode } from 'react';
import type { AuditEvent } from '../../lib/api/audit';
import {
  actionCategory,
  actionLabel,
  actorDisplay,
  categoryInfo,
  clientName,
  durationLabel,
  isFailed,
  OPTIONAL_COLUMNS,
  resourceName,
  resourceTypeLabel,
  shortId,
  SLOW_MS,
  statusLabel,
  tokensLabel,
  utcClock,
  type AuditColumn,
  type AuditFilters,
} from '../../lib/auditEvents';
import { relativeTime, utcTimestamp } from '../../lib/requestLogs';
import { keyTail } from '../../lib/usageFilters';
import { TableSkeleton } from '../usage/ReportBits';

interface AuditEventTableProps {
  events: readonly AuditEvent[];
  loading: boolean;
  refreshing: boolean;
  selectedId: string | null;
  columns: readonly AuditColumn[];
  showUtc: boolean;
  onToggleUtc: () => void;
  onSelect: (event: AuditEvent) => void;
  onFilter: (patch: Partial<AuditFilters>) => void;
}

const HEAD = 'whitespace-nowrap border-b border-gray-200 px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-500';
// `relative` keeps each cell's screen-reader-only text inside the table (see RequestLogTable).
const CELL = 'relative whitespace-nowrap border-b border-gray-100 px-3 py-[9px] align-middle';
const MUTED = 'text-gray-400';

/** A value in a row that adds a filter when clicked. The rest of the row opens the panel. */
function FilterValue({ children, title, onClick }: { children: ReactNode; title: string; onClick: () => void }) {
  return (
    <button
      type="button"
      title={title}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      onKeyDown={(event) => event.stopPropagation()}
      className="max-w-[220px] truncate border-b border-dotted border-transparent text-left align-bottom hover:border-gray-500 hover:text-brand-primary-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary group-hover:border-gray-400"
    >
      {children}
    </button>
  );
}

function optionalCell(column: AuditColumn, event: AuditEvent): ReactNode {
  switch (column) {
    case 'team':
      return event.labels?.team || event.labels?.team_id || <span className={MUTED}>—</span>;
    case 'ip':
      return event.ip ? <span className="font-mono text-xs">{event.ip}</span> : <span className={MUTED}>—</span>;
    case 'tokens': {
      const tokens = tokensLabel(event);
      return tokens === '—' ? <span className={MUTED}>—</span> : tokens;
    }
    case 'request_id':
      return event.request_id ? (
        <span className="font-mono text-xs" title={event.request_id}>{shortId(event.request_id, 10)}</span>
      ) : (
        <span className={MUTED}>—</span>
      );
    case 'client':
      return clientName(event.user_agent) ?? <span className={MUTED}>—</span>;
    default:
      return null;
  }
}

export default function AuditEventTable({
  events,
  loading,
  refreshing,
  selectedId,
  columns,
  showUtc,
  onToggleUtc,
  onSelect,
  onFilter,
}: AuditEventTableProps) {
  if (loading) return <TableSkeleton rows={10} columns={7 + columns.length} />;
  const optional = OPTIONAL_COLUMNS.filter((column) => columns.includes(column.key));
  const now = new Date();
  return (
    <div className={`overflow-x-auto transition-opacity ${refreshing ? 'opacity-60' : ''}`} aria-busy={refreshing}>
      <table className="w-full border-collapse text-[13px] tabular-nums">
        <thead>
          <tr>
            <th className={HEAD}>
              Time
              <button
                type="button"
                onClick={onToggleUtc}
                className="ml-1.5 text-[11px] font-medium normal-case tracking-normal text-brand-primary-ink hover:text-brand-primary-ink-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
              >
                {showUtc ? 'Show relative' : 'Show UTC'}
              </button>
            </th>
            <th className={HEAD}>Status</th>
            <th className={HEAD}>Action</th>
            <th className={HEAD}>Actor</th>
            <th className={HEAD}>Resource</th>
            <th className={HEAD}>Organization</th>
            {optional.map((column) => (
              <th key={column.key} className={`${HEAD} ${column.key === 'tokens' ? 'text-right' : ''}`}>{column.label}</th>
            ))}
            <th className={`${HEAD} text-right`}>Duration</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => {
            const failed = isFailed(event);
            const category = categoryInfo(actionCategory(event.action));
            const actor = actorDisplay(event);
            const name = resourceName(event);
            const selected = selectedId === event.event_id;
            const slow = (event.latency_ms ?? 0) > SLOW_MS;
            const organization = event.labels?.organization || event.organization_id;
            return (
              <tr
                key={event.event_id}
                tabIndex={0}
                aria-selected={selected}
                onClick={() => onSelect(event)}
                onKeyDown={(keyEvent) => {
                  if (keyEvent.key === 'Enter' || keyEvent.key === ' ') {
                    keyEvent.preventDefault();
                    onSelect(event);
                  }
                }}
                className={`group cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${selected ? 'bg-brand-primary-soft' : 'hover:bg-gray-50'}`}
              >
                <td className={`${CELL} text-gray-700`} title={utcTimestamp(event.occurred_at)}>
                  {showUtc ? utcClock(event.occurred_at, now) : relativeTime(event.occurred_at, now.getTime())}
                </td>
                <td className={CELL}>
                  <span className={`inline-flex items-center gap-1.5 ${failed ? 'text-rose-700' : 'text-gray-900'}`}>
                    <span aria-hidden="true" className={`h-[7px] w-[7px] rounded-full ${failed ? 'bg-rose-600' : 'bg-emerald-600'}`} />
                    {statusLabel(event)}
                    <span className="sr-only">{failed ? 'failed' : 'succeeded'}</span>
                  </span>
                </td>
                <td className={`${CELL} text-gray-900`} title={event.action}>
                  <i aria-hidden="true" className="mr-2 inline-block h-2 w-2 rounded-sm align-middle" style={{ background: category.color }} />
                  <span className="sr-only">{category.label}: </span>
                  {actionLabel(event.action)}
                </td>
                <td className={CELL}>
                  <div className="flex flex-col leading-tight">
                    {actor.filter ? (
                      <FilterValue title={`Show events by ${actor.name}`} onClick={() => onFilter({ actor: actor.filter ?? undefined })}>
                        <span className="text-gray-900">{actor.name}</span>
                      </FilterValue>
                    ) : (
                      <span className="text-gray-900">{actor.name}</span>
                    )}
                    <small className="text-[11.5px] text-gray-500">{actor.kind}</small>
                  </div>
                </td>
                <td className={`${CELL} text-gray-900`}>
                  {event.resource_type ? resourceTypeLabel(event.resource_type) : <span className={MUTED}>—</span>}
                  {name && event.resource_type && (
                    <>
                      {' '}
                      <FilterValue
                        title={`Show events for ${name}`}
                        onClick={() => onFilter({ resource: { type: event.resource_type ?? '', id: event.resource_id ?? undefined, label: name } })}
                      >
                        {name}
                      </FilterValue>
                    </>
                  )}
                  {event.resource_type === 'api_key' && event.labels?.resource && event.resource_id && (
                    <span className="text-gray-500"> · {keyTail(event.resource_id)}</span>
                  )}
                </td>
                <td className={CELL}>
                  {event.organization_id && organization ? (
                    <FilterValue
                      title={`Show events in ${organization}`}
                      onClick={() => onFilter({ organization: { value: event.organization_id ?? '', label: organization } })}
                    >
                      <span className="text-gray-900">{organization}</span>
                    </FilterValue>
                  ) : (
                    <span className={MUTED}>Platform</span>
                  )}
                </td>
                {optional.map((column) => (
                  <td key={column.key} className={`${CELL} text-gray-700 ${column.key === 'tokens' ? 'text-right' : ''}`}>
                    {optionalCell(column.key, event)}
                  </td>
                ))}
                <td className={`${CELL} text-right ${slow ? 'text-rose-700' : 'text-gray-700'}`}>{durationLabel(event.latency_ms)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
