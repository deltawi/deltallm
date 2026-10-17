import { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { audit, type AuditEvent } from '../../lib/api/audit';
import {
  actionCategory,
  actionLabel,
  actorDisplay,
  AUDIT_CATEGORIES,
  categoryInfo,
  hasAuditFilters,
  resourceName,
  resourceTypeLabel,
  type ActorFilter,
  type AuditFilters,
} from '../../lib/auditEvents';
import { useReport } from '../../lib/useReport';
import { Skeleton } from '../usage/ReportBits';
import { FilterChip } from '../usage/UsageFilterChips';
import ChipMenu, { MenuItem, MenuNote, MenuSearch } from './ChipMenu';

interface AuditFilterBarProps {
  filters: AuditFilters;
  /** Rows on screen: they suggest actors, resources and IP addresses without a query. */
  events: readonly AuditEvent[];
  onChange: (filters: AuditFilters) => void;
}

// Same box as the Usage request search; the placeholder must fit inside it.
const SEARCH_INPUT =
  'control-field-md w-[260px] max-w-full text-ellipsis rounded-lg border border-gray-300 bg-white pl-8 pr-2.5 text-xs text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary';

function useDebounced(value: string, delay = 250): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [delay, value]);
  return debounced;
}

function ActionMenu({ category, onPick }: { category: AuditFilters['category']; onPick: (action: string) => void }) {
  const [text, setText] = useState('');
  // A fixed list from the server: no database read, cached for the visit.
  const actions = useReport('audit:actions', (options) => audit.actions(options), { maxAgeMs: 24 * 3_600_000 });
  const needle = text.trim().toLowerCase();
  const items = (actions.data?.actions ?? [])
    .filter((item) => !category || item.category === category)
    .map((item) => ({ ...item, label: actionLabel(item.action) }))
    .filter((item) => !needle || item.label.toLowerCase().includes(needle) || item.action.toLowerCase().includes(needle))
    .sort((a, b) => a.category.localeCompare(b.category) || a.label.localeCompare(b.label));
  return (
    <>
      <MenuSearch label="Search actions" placeholder="Search actions…" value={text} onChange={setText} />
      {actions.loading ? (
        <div className="grid gap-2 p-2">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-3.5 w-full" />)}</div>
      ) : actions.error ? (
        <MenuNote>{actions.error}</MenuNote>
      ) : items.length ? (
        items.map((item) => (
          <MenuItem key={item.action} swatch={categoryInfo(item.category).color} onClick={() => onPick(item.action)}>
            {item.label}
          </MenuItem>
        ))
      ) : (
        <MenuNote>No action matches.</MenuNote>
      )}
      <MenuNote>{category ? `Actions in ${categoryInfo(category).label}.` : 'Every action, grouped by category.'}</MenuNote>
    </>
  );
}

function ActorMenu({ events, onPick }: { events: readonly AuditEvent[]; onPick: (actor: ActorFilter) => void }) {
  const [text, setText] = useState('');
  const options = useMemo(() => {
    const seen = new Map<string, { filter: ActorFilter; kind: string }>();
    for (const event of events) {
      const actor = actorDisplay(event);
      if (actor.filter && actor.named && !seen.has(actor.filter.value)) {
        seen.set(actor.filter.value, { filter: actor.filter, kind: event.actor_type === 'api_key' ? 'API key' : 'person' });
      }
    }
    return [
      ...seen.values(),
      { filter: { value: 'master_key', label: 'Master key', type: true }, kind: 'master key' },
      { filter: { value: 'system', label: 'System', type: true }, kind: 'system' },
    ];
  }, [events]);
  const needle = text.trim().toLowerCase();
  const shown = options.filter((option) => !needle || option.filter.label.toLowerCase().includes(needle)).slice(0, 12);
  const submit = () => {
    if (text.trim()) onPick({ value: text.trim(), label: text.trim() });
  };
  return (
    <>
      <MenuSearch label="Search people or keys" placeholder="Search people or keys…" value={text} onChange={setText} onSubmit={submit} />
      {text.trim() && (
        <MenuItem onClick={submit} hint="email or key name">
          Find “{text.trim()}”
        </MenuItem>
      )}
      {shown.map((option) => (
        <MenuItem key={`${option.filter.type ? 'type' : 'id'}:${option.filter.value}`} hint={option.kind} onClick={() => onPick(option.filter)}>
          {option.filter.label}
        </MenuItem>
      ))}
    </>
  );
}

function OrganizationMenu({ onPick }: { onPick: (organization: { value: string; label: string }) => void }) {
  const [text, setText] = useState('');
  const search = useDebounced(text.trim());
  // Only the organizations whose audit log the viewer can read; loads while the menu is open.
  const organizations = useReport(`audit:organizations|${search}`, (options) => audit.organizations(search, options));
  const rows = organizations.data?.organizations ?? [];
  return (
    <>
      <MenuSearch label="Search organizations" placeholder="Search organizations…" value={text} onChange={setText} />
      {organizations.loading ? (
        <div className="grid gap-2 p-2">{Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-3.5 w-full" />)}</div>
      ) : organizations.error ? (
        <MenuNote>{organizations.error}</MenuNote>
      ) : rows.length ? (
        rows.map((row) => (
          <MenuItem
            key={row.organization_id}
            onClick={() => onPick({ value: row.organization_id, label: row.organization_name || row.organization_id })}
          >
            {row.organization_name || row.organization_id}
          </MenuItem>
        ))
      ) : (
        <MenuNote>No organization matches.</MenuNote>
      )}
    </>
  );
}

const COMMON_RESOURCE_TYPES = ['model', 'model_deployment', 'api_key', 'route_group', 'mcp_server', 'team', 'organization', 'account', 'batch'];

function ResourceMenu({ events, onPick }: { events: readonly AuditEvent[]; onPick: (resource: NonNullable<AuditFilters['resource']>) => void }) {
  const [text, setText] = useState('');
  const recent = useMemo(() => {
    const seen = new Map<string, NonNullable<AuditFilters['resource']>>();
    for (const event of events) {
      if (!event.resource_type || !event.resource_id) continue;
      const key = `${event.resource_type}:${event.resource_id}`;
      if (!seen.has(key)) seen.set(key, { type: event.resource_type, id: event.resource_id, label: resourceName(event) ?? event.resource_id });
    }
    return [...seen.values()];
  }, [events]);
  const types = useMemo(
    () => [...new Set([...events.map((event) => event.resource_type).filter((type): type is string => Boolean(type)), ...COMMON_RESOURCE_TYPES])],
    [events],
  );
  const needle = text.trim().toLowerCase();
  const matchingRecent = recent
    .filter((item) => !needle || `${resourceTypeLabel(item.type)} ${item.label}`.toLowerCase().includes(needle))
    .slice(0, 6);
  const matchingTypes = types.filter((type) => !needle || resourceTypeLabel(type).toLowerCase().includes(needle));
  return (
    <>
      <MenuSearch label="Search resources" placeholder="Search resources on screen…" value={text} onChange={setText} />
      {matchingRecent.length > 0 && <MenuNote>On screen</MenuNote>}
      {matchingRecent.map((item) => (
        <MenuItem key={`${item.type}:${item.id}`} hint={resourceTypeLabel(item.type)} onClick={() => onPick(item)}>
          {item.label}
        </MenuItem>
      ))}
      <MenuNote>Any of one type</MenuNote>
      {matchingTypes.map((type) => (
        <MenuItem key={type} onClick={() => onPick({ type })}>
          Any {resourceTypeLabel(type).toLowerCase()}
          {type === 'model_deployment' ? ' deployment' : ''}
        </MenuItem>
      ))}
    </>
  );
}

function IpMenu({ events, onPick }: { events: readonly AuditEvent[]; onPick: (ip: string) => void }) {
  const [text, setText] = useState('');
  const ips = useMemo(() => [...new Set(events.map((event) => event.ip).filter((ip): ip is string => Boolean(ip)))], [events]);
  const needle = text.trim();
  const shown = ips.filter((ip) => !needle || ip.includes(needle)).slice(0, 8);
  return (
    <>
      <MenuSearch label="IP address" placeholder="Type an IP address" value={text} onChange={setText} onSubmit={() => needle && onPick(needle)} />
      {needle && !ips.includes(needle) && <MenuItem onClick={() => onPick(needle)}>Find {needle}</MenuItem>}
      {shown.map((ip) => (
        <MenuItem key={ip} onClick={() => onPick(ip)}>
          <span className="font-mono">{ip}</span>
        </MenuItem>
      ))}
    </>
  );
}

export default function AuditFilterBar({ filters, events, onChange }: AuditFilterBarProps) {
  const [draft, setDraft] = useState(filters.search ?? '');
  const [draftFor, setDraftFor] = useState(filters.search ?? '');
  if (draftFor !== (filters.search ?? '')) {
    // The URL changed (back or forward): show its search, not an old draft.
    setDraftFor(filters.search ?? '');
    setDraft(filters.search ?? '');
  }
  const set = (patch: Partial<AuditFilters>) => onChange({ ...filters, ...patch });
  const applySearch = () => {
    if (draft.trim() !== (filters.search ?? '')) set({ search: draft.trim() || undefined });
  };
  const resource = filters.resource;
  const resourceValue = resource
    ? resource.id
      ? `${resourceTypeLabel(resource.type)} ${resource.label || resource.id}`
      : `any ${resourceTypeLabel(resource.type).toLowerCase()}`
    : undefined;

  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          applySearch();
        }}
        className="relative"
      >
        <label htmlFor="audit-search" className="sr-only">Find a request ID, event ID or resource ID</label>
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
        <input
          id="audit-search"
          type="search"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={applySearch}
          placeholder="Request, event or resource ID"
          title="Find a request ID, event ID or resource ID"
          className={SEARCH_INPUT}
        />
      </form>
      <ChipMenu
        label="Category"
        value={filters.category ? categoryInfo(filters.category).label : undefined}
        onClear={() => set({ category: undefined })}
      >
        {(close) => AUDIT_CATEGORIES.map((category) => (
          <MenuItem
            key={category.key}
            swatch={category.color}
            onClick={() => {
              close();
              // An action from another category would match nothing.
              const keepAction = filters.action && actionCategory(filters.action) === category.key;
              set({ category: category.key, action: keepAction ? filters.action : undefined });
            }}
          >
            {category.label}
          </MenuItem>
        ))}
      </ChipMenu>
      <ChipMenu
        label="Status"
        value={filters.status === 'error' ? 'Failed' : filters.status === 'success' ? 'Succeeded' : undefined}
        onClear={() => set({ status: undefined })}
        width="w-44"
      >
        {(close) => (
          <>
            <MenuItem swatch="#059669" onClick={() => { close(); set({ status: 'success' }); }}>Succeeded</MenuItem>
            <MenuItem swatch="#e11d48" onClick={() => { close(); set({ status: 'error' }); }}>Failed</MenuItem>
          </>
        )}
      </ChipMenu>
      <ChipMenu
        label="Action"
        value={filters.action ? actionLabel(filters.action) : undefined}
        onClear={() => set({ action: undefined })}
        width="w-72"
      >
        {(close) => <ActionMenu category={filters.category} onPick={(action) => { close(); set({ action }); }} />}
      </ChipMenu>
      <ChipMenu label="Actor" value={filters.actor?.label} onClear={() => set({ actor: undefined })}>
        {(close) => <ActorMenu events={events} onPick={(actor) => { close(); set({ actor }); }} />}
      </ChipMenu>
      <ChipMenu label="Organization" value={filters.organization?.label} onClear={() => set({ organization: undefined })}>
        {(close) => <OrganizationMenu onPick={(organization) => { close(); set({ organization }); }} />}
      </ChipMenu>
      <ChipMenu label="Resource" value={resourceValue} onClear={() => set({ resource: undefined })} width="w-72">
        {(close) => <ResourceMenu events={events} onPick={(picked) => { close(); set({ resource: picked }); }} />}
      </ChipMenu>
      <ChipMenu label="IP address" value={filters.ip} onClear={() => set({ ip: undefined })} width="w-56">
        {(close) => <IpMenu events={events} onPick={(ip) => { close(); set({ ip }); }} />}
      </ChipMenu>
      {filters.requestId && (
        <FilterChip label="Request" value={filters.requestId} active onClick={() => set({ requestId: undefined })} />
      )}
      {hasAuditFilters(filters) && (
        <button
          type="button"
          onClick={() => {
            setDraft('');
            onChange({});
          }}
          className="rounded px-1 text-xs font-medium text-brand-primary-ink hover:text-brand-primary-ink-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}
