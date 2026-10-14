import type { KeyboardEvent, ReactNode } from 'react';
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
import { TableSkeleton } from '../usage/ReportBits';

interface AuditEventTableProps {
  events: readonly AuditEvent[];
  loading: boolean;
  refreshing: boolean;
  selectedId: string | null;
  columns: readonly AuditColumn[];
  showUtc: boolean;
  onSelect: (event: AuditEvent) => void;
  onFilter: (patch: Partial<AuditFilters>) => void;
}

// Same header and cell sizes as the Usage request list (RequestLogTable).
const HEAD = 'whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-500';
// `relative` keeps each cell's screen-reader-only text inside the table (see RequestLogTable).
// The table has a fixed layout, so a long value is cut with "…" and never makes the table wider.
const CELL = 'relative overflow-hidden text-ellipsis whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] align-middle';
const MUTED = 'text-gray-400';
// Columns leave from the right as the screen narrows; the panel still shows every value.
const LARGE = 'hidden lg:table-cell';
const WIDE = 'hidden xl:table-cell';
// Short values get a set width; the name columns share the rest.
const FIXED_WIDTH: Partial<Record<AuditColumn, string>> = { ip: 'w-[120px]', tokens: 'w-[110px]', request_id: 'w-[112px]' };

/** A value in a row that adds a filter when clicked. The rest of the row opens the panel. */
function FilterValue({ children, title, onClick, width }: { children: ReactNode; title: string; onClick: () => void; width: string }) {
  return (
    <button
      type="button"
      title={title}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      onKeyDown={(event) => event.stopPropagation()}
      className={`${width} min-w-0 truncate border-b border-dotted border-transparent text-left align-bottom hover:border-gray-500 hover:text-brand-primary-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary group-hover:border-gray-400`}
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

function timeLabel(event: AuditEvent, showUtc: boolean, now: Date): string {
  return showUtc ? utcClock(event.occurred_at, now) : relativeTime(event.occurred_at, now.getTime());
}

function selectKeys(onSelect: () => void) {
  return (keyEvent: KeyboardEvent<HTMLElement>) => {
    if (keyEvent.key === 'Enter' || keyEvent.key === ' ') {
      keyEvent.preventDefault();
      onSelect();
    }
  };
}

/** Narrow layout (phones, and tablets with the menu open): two lines per event instead of a table. */
function EventList({ events, selectedId, showUtc, onSelect }: Pick<AuditEventTableProps, 'events' | 'selectedId' | 'showUtc' | 'onSelect'>) {
  const now = new Date();
  return (
    <ul className="min-[880px]:hidden" aria-label="Audit events">
      {events.map((event) => {
        const failed = isFailed(event);
        const actor = actorDisplay(event);
        const name = resourceName(event);
        const target = name ?? (event.resource_type ? resourceTypeLabel(event.resource_type) : null);
        return (
          <li key={event.event_id}>
            <button
              type="button"
              aria-current={selectedId === event.event_id ? 'true' : undefined}
              onClick={() => onSelect(event)}
              className={`grid w-full gap-0.5 border-b border-gray-100 px-2.5 py-2.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${selectedId === event.event_id ? 'bg-brand-primary-soft' : 'hover:bg-gray-50'}`}
            >
              <span className="flex min-w-0 items-center gap-2">
                <i aria-hidden="true" className="h-2 w-2 shrink-0 rounded-sm" style={{ background: categoryInfo(actionCategory(event.action)).color }} />
                <span className={`min-w-0 flex-1 truncate text-[13px] ${failed ? 'text-rose-700' : 'text-gray-900'}`}>
                  {actionLabel(event.action)}
                  {failed ? ` · ${statusLabel(event)}` : ''}
                </span>
                <span className="shrink-0 whitespace-nowrap text-xs text-gray-500">{timeLabel(event, showUtc, now)}</span>
              </span>
              <span className="truncate pl-4 text-xs text-gray-500">
                {actor.name}
                {actor.tail ? ` ${actor.tail}` : ''}
                {target ? ` → ${target}` : ''}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default function AuditEventTable({
  events,
  loading,
  refreshing,
  selectedId,
  columns,
  showUtc,
  onSelect,
  onFilter,
}: AuditEventTableProps) {
  if (loading) return <TableSkeleton rows={10} columns={6} />;
  const optional = OPTIONAL_COLUMNS.filter((column) => columns.includes(column.key));
  const now = new Date();
  return (
    <div className={`transition-opacity ${refreshing ? 'opacity-60' : ''}`} aria-busy={refreshing}>
      <EventList events={events} selectedId={selectedId} showUtc={showUtc} onSelect={onSelect} />
      <div className="hidden overflow-x-auto min-[880px]:block">
        <table className="w-full table-fixed border-collapse text-[12.5px] tabular-nums">
          <thead>
            <tr>
              <th className={`${HEAD} ${showUtc ? 'w-[128px]' : 'w-[100px]'}`}>Time{showUtc ? ' (UTC)' : ''}</th>
              <th className={`${HEAD} w-[78px]`}>Status</th>
              <th className={HEAD}>Action</th>
              <th className={HEAD}>Actor</th>
              <th className={`${HEAD} ${LARGE}`}>Resource</th>
              <th className={`${HEAD} ${WIDE}`}>Organization</th>
              {optional.map((column) => (
                <th key={column.key} className={`${HEAD} ${WIDE} ${FIXED_WIDTH[column.key] ?? ''} ${column.key === 'tokens' ? 'text-right' : ''}`}>{column.label}</th>
              ))}
              <th className={`${HEAD} ${WIDE} w-[84px] text-right`}>Duration</th>
              {/* Room for the Columns button, which sits over this corner. */}
              <th className={`${HEAD} w-9`}><span className="sr-only">Columns</span></th>
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
              const type = event.resource_type ? resourceTypeLabel(event.resource_type) : null;
              return (
                <tr
                  key={event.event_id}
                  tabIndex={0}
                  aria-selected={selected}
                  onClick={() => onSelect(event)}
                  onKeyDown={selectKeys(() => onSelect(event))}
                  className={`group cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${selected ? 'bg-brand-primary-soft' : 'hover:bg-gray-50'}`}
                >
                  <td className={`${CELL} text-gray-700`} title={utcTimestamp(event.occurred_at)}>
                    {timeLabel(event, showUtc, now)}
                  </td>
                  <td className={CELL}>
                    <span className={`inline-flex items-center gap-1.5 font-medium ${failed ? 'text-rose-700' : 'text-gray-900'}`}>
                      <span aria-hidden="true" className={`h-[7px] w-[7px] rounded-full ${failed ? 'bg-rose-600' : 'bg-emerald-600'}`} />
                      {statusLabel(event)}
                      <span className="sr-only">{failed ? 'failed' : 'succeeded'}</span>
                    </span>
                  </td>
                  <td className={`${CELL} text-gray-900`} title={actionLabel(event.action)}>
                    <span className="flex min-w-0 items-center">
                      <i aria-hidden="true" className="mr-2 inline-block h-2 w-2 shrink-0 rounded-sm" style={{ background: category.color }} />
                      <span className="sr-only">{category.label}: </span>
                      <span className="truncate">{actionLabel(event.action)}</span>
                    </span>
                  </td>
                  <td className={`${CELL} text-gray-900`}>
                    <span className="flex min-w-0 items-baseline">
                      {actor.filter ? (
                        <FilterValue width="max-w-full" title={`Show events by ${actor.name}`} onClick={() => onFilter({ actor: actor.filter ?? undefined })}>
                          {actor.name}
                        </FilterValue>
                      ) : (
                        <span className="min-w-0 truncate">{actor.name}</span>
                      )}
                      {actor.tail && <span className="ml-1 shrink-0 font-mono text-[11px] text-gray-500">{actor.tail}</span>}
                    </span>
                  </td>
                  <td className={`${CELL} ${LARGE} text-gray-900`}>
                    {name && event.resource_type ? (
                      <span className="flex min-w-0 items-baseline">
                        <FilterValue
                          width="max-w-full"
                          title={`Show events for ${name}`}
                          onClick={() => onFilter({ resource: { type: event.resource_type ?? '', id: event.resource_id ?? undefined, label: name } })}
                        >
                          {name}
                        </FilterValue>
                        <span className="ml-1 shrink-0 text-xs text-gray-500">{type?.toLowerCase() === 'api key' ? 'API key' : type?.toLowerCase()}</span>
                      </span>
                    ) : (
                      type ?? <span className={MUTED}>—</span>
                    )}
                  </td>
                  <td className={`${CELL} ${WIDE}`}>
                    {event.organization_id && organization ? (
                      <FilterValue
                        width="max-w-full"
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
                    <td key={column.key} className={`${CELL} ${WIDE} text-gray-700 ${column.key === 'tokens' ? 'text-right' : ''}`}>
                      {optionalCell(column.key, event)}
                    </td>
                  ))}
                  <td className={`${CELL} ${WIDE} text-right ${slow ? 'text-rose-700' : 'text-gray-700'}`}>{durationLabel(event.latency_ms)}</td>
                  <td className={CELL} />
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
