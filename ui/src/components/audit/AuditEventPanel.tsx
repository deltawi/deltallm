import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronUp, Copy, X } from 'lucide-react';
import { audit, type AuditEvent, type AuditEventDetail, type AuditPayload, type AuditRelatedEvent } from '../../lib/api/audit';
import type { UIAccess } from '../../lib/authorization';
import {
  actionCategory,
  actionLabel,
  actorDisplay,
  actorRole,
  categoryInfo,
  changedFields,
  clientName,
  errorDetail,
  errorTitle,
  eventSummary,
  isFailed,
  resourceLink,
  resourceName,
  resourceTypeLabel,
  shortId,
  signInMethod,
  stateDisplay,
  statusLabel,
  utcClock,
  utcLong,
  type AuditFilters,
  type NowTone,
} from '../../lib/auditEvents';
import { fmtDuration } from '../../lib/format';
import { errorMessage } from '../../lib/errorMessage';
import { relativeTime } from '../../lib/requestLogs';
import { useReport } from '../../lib/useReport';
import { keyTail } from '../../lib/usageFilters';
import { PanelError, Skeleton } from '../usage/ReportBits';

interface AuditEventPanelProps {
  eventId: string;
  /** The row from the list, so the panel shows at once while its lookups load. */
  row: AuditEvent | null;
  access: Partial<UIAccess>;
  hasPrevious: boolean;
  hasNext: boolean;
  onStep: (direction: -1 | 1) => void;
  onOpen: (eventId: string) => void;
  onClose: () => void;
  onFilter: (patch: Partial<AuditFilters>, replace?: boolean) => void;
}

const PILL: Record<NowTone, string> = {
  ok: 'bg-emerald-50 text-emerald-700',
  bad: 'bg-rose-50 text-rose-700',
  warn: 'bg-amber-50 text-amber-800',
  muted: 'bg-gray-100 text-gray-600',
};

// Same look as the Usage request drawer.
const ICON_BUTTON =
  'rounded-lg border border-gray-300 p-1.5 text-gray-700 hover:bg-gray-50 disabled:opacity-40 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary';
const BUTTON =
  'rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-900 hover:bg-gray-50 disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary';

function Pill({ tone, children }: { tone: NowTone; children: ReactNode }) {
  return <span className={`rounded-full px-2 py-px text-[11.5px] font-medium ${PILL[tone]}`}>{children}</span>;
}

interface FieldRow {
  label: string;
  value: ReactNode;
  /** Adds a filter; shows "⊕ filter" on hover. */
  filter?: () => void;
  title?: string;
  /** Let a long value wrap instead of cutting it off. */
  wrap?: boolean;
}

function Fields({ rows }: { rows: ReadonlyArray<FieldRow | null | false> }) {
  const shown = rows.filter((row): row is FieldRow => Boolean(row));
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm">
      {shown.map((row) => (
        <div key={row.label} className="group/field min-w-0">
          <dt className="block text-[11px] uppercase tracking-wider text-gray-500">{row.label}</dt>
          <dd className="flex min-w-0 items-center gap-1 font-medium tabular-nums text-gray-900" title={row.title}>
            <span className={`min-w-0 ${row.wrap ? 'break-words' : 'truncate'}`}>{row.value}</span>
            {row.filter && (
              <button
                type="button"
                onClick={row.filter}
                className="shrink-0 text-[11px] font-normal text-brand-primary-ink opacity-0 focus:opacity-100 focus:outline-none group-hover/field:opacity-100"
              >
                ⊕ filter
              </button>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Section({ title, extra, children }: { title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section className="grid gap-2">
      <h3 className="flex items-center gap-1.5 text-xs text-gray-500">
        {title}
        {extra}
      </h3>
      {children}
    </section>
  );
}

function CopyChip({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => setCopied(true), () => setCopied(false));
      }}
      title={`Copy ${value}`}
      className="inline-flex items-center gap-1 rounded border border-gray-200 px-1 font-mono text-[11px] font-normal normal-case tracking-normal text-gray-500 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
    >
      {copied ? 'Copied' : label ?? shortId(value, 12)}
      <Copy className="h-2.5 w-2.5" aria-hidden="true" />
    </button>
  );
}

function Trail({
  rows,
  currentId,
  onOpen,
}: {
  rows: readonly AuditRelatedEvent[];
  currentId: string;
  onOpen: (eventId: string) => void;
}) {
  const now = new Date();
  return (
    <ol className="ml-1 grid border-l-2 border-gray-200">
      {rows.map((row) => {
        const here = row.event_id === currentId;
        const failed = isFailed(row);
        return (
          <li key={row.event_id} className="relative py-[3px] pl-3.5 text-[12.5px]">
            <span
              aria-hidden="true"
              className={`absolute -left-[5px] top-[9px] h-2 w-2 rounded-full border-2 ${here ? 'border-brand-primary bg-brand-primary' : 'border-gray-400 bg-white'}`}
            />
            <button
              type="button"
              disabled={here}
              onClick={() => onOpen(row.event_id)}
              className="flex w-full gap-2 text-left hover:text-brand-primary-ink disabled:cursor-default disabled:hover:text-inherit focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
            >
              <span className="min-w-[62px] shrink-0 whitespace-nowrap tabular-nums text-gray-500">{utcClock(row.occurred_at, now)}</span>
              <span className={`min-w-0 ${failed ? 'text-rose-700' : 'text-gray-900'}`}>
                {actionLabel(row.action)}
                {failed ? ' failed' : ''}
                {here && <span className="text-gray-500"> (this event)</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function PayloadBodies({ payloads }: { payloads: readonly AuditPayload[] }) {
  if (!payloads.length) return <p className="text-xs text-gray-500">No bodies were stored for this event.</p>;
  return (
    <div className="grid gap-3">
      {payloads.map((payload) => (
        <div key={payload.payload_id || payload.kind} className="grid gap-1">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-gray-500">
            {payload.kind}
            {payload.redacted && <Pill tone="warn">Redacted</Pill>}
            {payload.size_bytes != null && <span className="normal-case tracking-normal">{payload.size_bytes.toLocaleString()} bytes</span>}
          </div>
          {payload.content_json ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-gray-50 p-2.5 font-mono text-[11.5px] text-gray-800">
              {typeof payload.content_json === 'string' ? payload.content_json : JSON.stringify(payload.content_json, null, 2)}
            </pre>
          ) : payload.storage_uri ? (
            <p className="text-xs text-gray-500">Stored at {payload.storage_uri}</p>
          ) : (
            <p className="text-xs text-gray-500">No content available.</p>
          )}
        </div>
      ))}
    </div>
  );
}

function Details({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details>
      <summary className="cursor-pointer text-xs text-gray-700">{summary}</summary>
      <div className="mt-2 grid gap-2.5">{children}</div>
    </details>
  );
}

export default function AuditEventPanel({
  eventId,
  row,
  access,
  hasPrevious,
  hasNext,
  onStep,
  onOpen,
  onClose,
  onFilter,
}: AuditEventPanelProps) {
  // Loaded only while the panel is open: a few primary-key lookups and two small indexed queries.
  const detail = useReport<AuditEventDetail>(`audit:event|${eventId}`, (options) => audit.get(eventId, options), { maxAgeMs: 30_000 });
  const related = useReport(`audit:related|${eventId}`, (options) => audit.related(eventId, options), { maxAgeMs: 30_000 });
  const [payloads, setPayloads] = useState<{ id: string; rows: AuditPayload[] | null; error: string | null; loading: boolean } | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const scrollRef = useRef<HTMLElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: 0 });
  }, [eventId]);

  const event: AuditEventDetail | null = detail.data ?? row;
  if (!event) {
    return (
      <PanelFrame onClose={onClose} hasPrevious={hasPrevious} hasNext={hasNext} onStep={onStep}>
        <div className="grid gap-3">
          {detail.error ? (
            <PanelError message={detail.error} onRetry={detail.retry} />
          ) : (
            Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-4 w-full" />)
          )}
        </div>
      </PanelFrame>
    );
  }

  const context = detail.data?.context ?? null;
  const labels = context?.labels ?? event.labels;
  const withLabels: AuditEvent = { ...event, labels };
  const category = actionCategory(event.action);
  const categoryMeta = categoryInfo(category);
  const failed = isFailed(event);
  const actor = actorDisplay(withLabels);
  const organization = labels?.organization || event.organization_id;
  const resource = resourceName(withLabels);
  const resourceType = resourceTypeLabel(event.resource_type);
  const client = clientName(event.user_agent);
  const changed = changedFields(event);
  const summary = detail.data?.payload_summary;
  const bodiesStored = summary ? summary.count > 0 : event.content_stored;
  const badge = bodiesStored ? (summary?.redacted ? 'Redacted' : 'Bodies stored') : null;
  const resourceState = stateDisplay(event.resource_type, context?.resource);
  const keyState = context?.key ? stateDisplay('api_key', context.key) : null;
  const link = resourceState?.label === 'No longer exists' || resourceState?.label === 'Revoked or deleted'
    ? null
    : resourceLink(event.resource_type, event.resource_id, access);
  const showPayloads = payloads?.id === eventId ? payloads : null;
  const metadata = event.metadata ?? {};
  const mcpServer = typeof metadata.server_key === 'string' ? metadata.server_key : null;
  const role = actorRole(context?.actor ?? null, labels?.organization ?? null);
  const resourceFilter = event.resource_type && event.resource_id
    ? () => onFilter({ resource: { type: event.resource_type ?? '', id: event.resource_id ?? undefined, label: resource ?? undefined } })
    : undefined;

  const loadPayloads = () => {
    setPayloads({ id: eventId, rows: null, error: null, loading: true });
    audit.payloads(eventId).then(
      (response) => setPayloads({ id: eventId, rows: response.payloads, error: null, loading: false }),
      (error: unknown) => setPayloads({ id: eventId, rows: null, error: errorMessage(error, 'Bodies could not be loaded.'), loading: false }),
    );
  };

  const copyLink = () => {
    const url = new URL(window.location.href);
    url.searchParams.set('event', eventId);
    void navigator.clipboard?.writeText(url.toString()).then(() => setLinkCopied(true), () => setLinkCopied(false));
  };

  const who: Array<FieldRow | null | false> = [
    {
      label: 'Actor',
      value: actor.name,
      filter: actor.filter ? () => onFilter({ actor: actor.filter ?? undefined }) : undefined,
    },
    event.actor_type === 'platform_account' && {
      label: 'Role',
      value: context?.actor?.state === 'missing'
        ? <span className="text-gray-500">Account no longer exists</span>
        : role
          ? <>{role} <span className="font-normal text-gray-500">(current)</span></>
          : detail.loading ? <Skeleton className="h-3.5 w-24" /> : '—',
      wrap: true,
    },
    event.actor_type !== 'platform_account' && { label: 'Type', value: actor.kind.charAt(0).toUpperCase() + actor.kind.slice(1) },
    (context?.key?.team || labels?.team) ? { label: 'Team', value: context?.key?.team || labels?.team } : false,
    context?.key?.owner ? { label: 'Key owner', value: context.key.owner } : false,
    event.organization_id && organization
      ? { label: 'Organization', value: organization, filter: () => onFilter({ organization: { value: event.organization_id ?? '', label: organization } }) }
      : false,
    event.ip ? { label: 'IP address', value: <span className="font-mono text-xs">{event.ip}</span>, filter: () => onFilter({ ip: event.ip ?? undefined }) } : false,
    client ? { label: 'Client', value: client, title: event.user_agent ?? undefined } : false,
  ];

  const what: Array<FieldRow | null | false> = [
    category === 'mcp'
      ? mcpServer ? { label: 'Server', value: mcpServer } : false
      : event.resource_type
        ? { label: 'Resource', value: resource ? `${resourceType} ${resource}` : resourceType, filter: resourceFilter }
        : false,
    category === 'mcp' && resource ? { label: 'Tool', value: resource, filter: resourceFilter } : false,
    event.resource_type === 'api_key' && event.resource_id ? { label: 'Key', value: <span className="font-mono text-xs">{keyTail(event.resource_id)}</span> } : false,
    event.resource_type === 'api_key' && context?.resource?.team ? { label: 'Team', value: context.resource.team } : false,
    event.resource_type === 'api_key' && context?.resource?.owner ? { label: 'Key owner', value: context.resource.owner } : false,
    signInMethod(event.action) ? { label: 'Method', value: signInMethod(event.action) } : false,
    event.input_tokens != null || event.output_tokens != null
      ? { label: 'Tokens', value: `${(event.input_tokens ?? 0).toLocaleString()} in · ${(event.output_tokens ?? 0).toLocaleString()} out` }
      : false,
    event.latency_ms != null ? { label: 'Duration', value: fmtDuration(event.latency_ms) } : false,
    context?.resource?.total_items ? { label: 'Requests', value: context.resource.total_items.toLocaleString() } : false,
    resourceState
      ? {
          label: event.resource_type === 'batch' ? 'Batch now' : 'Now',
          value: <Pill tone={resourceState.tone}>{resourceState.label}</Pill>,
        }
      : false,
    keyState && event.resource_type !== 'api_key' ? { label: 'Key now', value: <Pill tone={keyState.tone}>{keyState.label}</Pill> } : false,
  ];
  const whatRows = what.filter(Boolean);

  const requestTrail = related.data?.request ?? [];
  const resourceTrail = related.data?.resource ?? [];
  const signins = related.data?.actor_signins ?? [];
  const usageLink = event.request_id && access.usage && category === 'api'
    ? `/usage/requests?request_id=${encodeURIComponent(event.request_id)}`
    : null;

  return (
    <PanelFrame
      onClose={onClose}
      hasPrevious={hasPrevious}
      hasNext={hasNext}
      onStep={onStep}
      scrollRef={scrollRef}
      header={(
        <>
          <span className="flex items-center text-xs text-gray-500">
            <i aria-hidden="true" className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: categoryMeta.color }} />
            {categoryMeta.label}
          </span>
          <h2 className="text-[17px] font-semibold text-gray-900">{actionLabel(event.action)}</h2>
        </>
      )}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={`inline-flex items-center gap-1.5 font-medium ${failed ? 'text-rose-700' : 'text-gray-900'}`}>
          <span aria-hidden="true" className={`h-[7px] w-[7px] rounded-full ${failed ? 'bg-rose-600' : 'bg-emerald-600'}`} />
          {failed ? (statusLabel(event) !== 'Failed' ? `${statusLabel(event)} Failed` : 'Failed') : 'Succeeded'}
        </span>
        <span className="text-xs text-gray-500" title={utcLong(event.occurred_at)}>
          · {relativeTime(event.occurred_at)}{event.latency_ms != null ? ` · ${fmtDuration(event.latency_ms)}` : ''}
        </span>
        {badge && <span className={`rounded-full px-2 py-px text-[11.5px] font-medium ${badge === 'Redacted' ? PILL.warn : 'bg-sky-50 text-sky-700'}`}>{badge}</span>}
      </div>
      {detail.error && <PanelError message={detail.error} onRetry={detail.retry} stale={detail.data !== null} />}
      <p className="text-sm text-gray-900">
        {eventSummary(withLabels).map((part, index) =>
          typeof part === 'string' ? <span key={index}>{part}</span> : <b key={index} className="font-semibold">{part.bold}</b>,
        )}
      </p>
      {failed && (
        <div className="rounded-lg bg-rose-50 px-3 py-2.5 text-xs text-rose-700">
          <b className="block">{errorTitle(event)}</b>
          {errorDetail(event) ?? 'No error details were recorded.'}
        </div>
      )}
      <Section title="Who">
        <Fields rows={who} />
      </Section>
      {(whatRows.length > 0 || changed.length > 0) && (
        <Section title="What">
          <Fields rows={what} />
          {changed.length > 0 && (
            <div>
              <span className="block text-[11px] uppercase tracking-wider text-gray-500">Changed</span>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {changed.map((field) => (
                  <code key={field} className="rounded bg-brand-primary-soft px-1.5 py-px font-mono text-xs text-brand-primary-ink">{field}</code>
                ))}
              </div>
              <p className="mt-1.5 text-xs text-gray-500">
                {bodiesStored ? 'The new values are in the request body below.' : 'Values were not captured for this change.'}
              </p>
            </div>
          )}
        </Section>
      )}
      {related.loading ? (
        <div className="grid gap-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-3.5 w-3/4" />
        </div>
      ) : related.error ? (
        <PanelError message={related.error} onRetry={related.retry} />
      ) : (
        <>
          {event.request_id && requestTrail.length > 1 && (
            <Section title="Request" extra={<CopyChip value={event.request_id} />}>
              <Trail rows={requestTrail} currentId={event.event_id} onOpen={onOpen} />
            </Section>
          )}
          {signins.length > 0 && (
            <Section title="Recent sign-ins by this person">
              <Trail rows={signins} currentId={event.event_id} onOpen={onOpen} />
            </Section>
          )}
          {resourceTrail.length > 1 && (
            <Section
              title={event.resource_type === 'batch' ? 'Same batch' : `History of this ${resourceType.toLowerCase()}`}
              extra={event.resource_type === 'batch' && event.resource_id ? <CopyChip value={event.resource_id} /> : undefined}
            >
              <Trail rows={[...resourceTrail].reverse()} currentId={event.event_id} onOpen={onOpen} />
            </Section>
          )}
        </>
      )}
      <Details summary="Technical details">
        <Fields
          rows={[
            { label: 'Event ID', value: <CopyChip value={event.event_id} /> },
            { label: 'Action code', value: <span className="font-mono text-xs">{event.action}</span>, title: event.action },
            event.request_id ? { label: 'Request ID', value: <CopyChip value={event.request_id} /> } : false,
            event.correlation_id && event.correlation_id !== event.request_id
              ? { label: 'Correlation ID', value: <CopyChip value={event.correlation_id} /> }
              : false,
            event.actor_id ? { label: 'Actor ID', value: <span className="font-mono text-xs">{event.actor_id}</span>, title: event.actor_id } : false,
            event.resource_id ? { label: 'Resource ID', value: <span className="font-mono text-xs">{event.resource_id}</span>, title: event.resource_id } : false,
            event.organization_id ? { label: 'Organization ID', value: <span className="font-mono text-xs">{event.organization_id}</span>, title: event.organization_id } : false,
            event.error_type ? { label: 'Error type', value: event.error_type } : false,
            event.user_agent ? { label: 'User agent', value: event.user_agent, title: event.user_agent } : false,
            event.event_hash ? { label: 'Event hash', value: <span className="font-mono text-xs">{shortId(event.event_hash, 12)}</span>, title: event.event_hash } : false,
          ]}
        />
      </Details>
      <Details summary="Metadata">
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-2.5 font-mono text-[11px] text-gray-800">
          {Object.keys(metadata).length ? JSON.stringify(metadata, null, 2) : 'No metadata'}
        </pre>
      </Details>
      {bodiesStored && (
        <div className="grid gap-2.5">
          <p className="text-xs text-amber-800">
            {summary?.redacted
              ? 'Bodies were stored with secrets and personal data redacted.'
              : 'Request and response bodies were stored for this event. They may contain customer data.'}
          </p>
          {!showPayloads?.rows && (
            <div>
              <button type="button" onClick={loadPayloads} disabled={showPayloads?.loading} className={BUTTON}>
                {showPayloads?.loading ? 'Loading payloads' : 'Show payloads'}
              </button>
            </div>
          )}
          {showPayloads?.error && <PanelError message={showPayloads.error} onRetry={loadPayloads} />}
          {showPayloads?.rows && <PayloadBodies payloads={showPayloads.rows} />}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {usageLink && <Link to={usageLink} className={BUTTON}>Open in Usage requests</Link>}
        {link && <Link to={link} className={BUTTON}>Open {resourceType.toLowerCase() === 'api key' ? 'API keys' : resourceType.toLowerCase()}</Link>}
        {event.ip && (
          <button type="button" onClick={() => onFilter({ ip: event.ip ?? undefined }, true)} className={BUTTON}>
            Events from this IP
          </button>
        )}
        <button type="button" onClick={copyLink} className={BUTTON}>
          {linkCopied ? 'Link copied' : 'Copy link'}
        </button>
      </div>
    </PanelFrame>
  );
}

function PanelFrame({
  header,
  scrollRef,
  hasPrevious,
  hasNext,
  onStep,
  onClose,
  children,
}: {
  header?: ReactNode;
  scrollRef?: RefObject<HTMLElement | null>;
  hasPrevious: boolean;
  hasNext: boolean;
  onStep: (direction: -1 | 1) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  // One scrolling column, the same frame as the Usage request drawer.
  return (
    <aside
      ref={scrollRef}
      aria-label="Event detail"
      className="fixed inset-y-0 right-0 z-40 grid w-full max-w-[420px] content-start gap-3.5 overflow-y-auto border-l border-gray-300 bg-white px-5 py-[18px] shadow-[-12px_0_30px_rgba(0,0,0,0.1)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">{header}</div>
        <div className="flex shrink-0 gap-1.5">
          <button type="button" className={ICON_BUTTON} disabled={!hasPrevious} onClick={() => onStep(-1)} aria-label="Previous event" title="Previous event (↑)">
            <ChevronUp className="h-3.5 w-3.5" />
          </button>
          <button type="button" className={ICON_BUTTON} disabled={!hasNext} onClick={() => onStep(1)} aria-label="Next event" title="Next event (↓)">
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          <button type="button" className={ICON_BUTTON} onClick={onClose} aria-label="Close" title="Close (Esc)">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {children}
    </aside>
  );
}
