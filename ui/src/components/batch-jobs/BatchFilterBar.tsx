import { useEffect, useState } from 'react';
import { Search } from 'lucide-react';
import { batches, type BatchFacetValue, type BatchFacets } from '../../lib/api/batches';
import { BATCH_ENDPOINTS, BATCH_WINDOWS, type BatchListState, type BatchWindow } from '../../lib/batchJobs';
import { useReport } from '../../lib/useReport';
import ChipMenu, { MenuItem, MenuNote, MenuSearch } from '../audit/ChipMenu';
import { Skeleton } from '../usage/ReportBits';

interface BatchFilterBarProps {
  state: BatchListState;
  /** Team names seen in the loaded rows, so an active team chip can show a name. */
  teamLabels: Record<string, string>;
  summary: string | null;
  onChange: (patch: Partial<BatchListState>) => void;
}

function FacetMenu({
  window,
  kind,
  onPick,
}: {
  window: BatchWindow;
  kind: 'models' | 'teams';
  onPick: (value: BatchFacetValue) => void;
}) {
  const [query, setQuery] = useState('');
  // The options load only when a menu opens. Both menus share one cached request.
  const facets = useReport<BatchFacets>(`batch:facets|${window}`, ({ signal }) => {
    const hours = BATCH_WINDOWS.find((item) => item.key === window)?.hours ?? null;
    const createdAfter = hours === null ? undefined : new Date(Date.now() - hours * 3_600_000).toISOString();
    return batches.facets({ created_after: createdAfter }, signal);
  });
  const values = (facets.data?.[kind] ?? []).filter((item) => {
    const needle = query.trim().toLowerCase();
    return !needle || item.label.toLowerCase().includes(needle) || item.value.toLowerCase().includes(needle);
  });
  const noun = kind === 'models' ? 'model' : 'team';
  return (
    <>
      <MenuSearch value={query} onChange={setQuery} placeholder={`Find a ${noun}`} label={`Find a ${noun}`} />
      {facets.loading && (
        <div className="grid gap-1.5 px-2 py-1.5" role="status" aria-label="Loading">
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      )}
      {facets.error && <MenuNote>{facets.error}</MenuNote>}
      {!facets.loading && !facets.error && values.length === 0 && <MenuNote>No {noun}s in this period.</MenuNote>}
      {values.map((item) => (
        <MenuItem key={item.value} hint={item.count.toLocaleString()} onClick={() => onPick(item)}>
          {item.label}
        </MenuItem>
      ))}
    </>
  );
}

export default function BatchFilterBar({ state, teamLabels, summary, onChange }: BatchFilterBarProps) {
  // The typed text belongs to one URL value. When the URL changes (back button,
  // "Clear filters"), the box shows the URL value again.
  const [draft, setDraft] = useState({ base: state.search, value: state.search });
  const search = draft.base === state.search ? draft.value : state.search;
  // Names of teams picked in the menu, for a team that has no row on this page.
  const [pickedTeams, setPickedTeams] = useState<Record<string, string>>({});

  useEffect(() => {
    if (search === state.search) return;
    const timer = window.setTimeout(() => onChange({ search }), 300);
    return () => window.clearTimeout(timer);
  }, [onChange, search, state.search]);

  const endpointName = BATCH_ENDPOINTS.find((item) => item.value === state.endpoint)?.label;
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <label className="relative w-full sm:w-72">
        <span className="sr-only">Search batch ID or model</span>
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
        <input
          type="search"
          value={search}
          onChange={(event) => setDraft({ base: state.search, value: event.target.value })}
          placeholder="Batch ID or model"
          maxLength={200}
          className="control-field-md w-full rounded-lg border border-gray-200 bg-white pl-8 pr-2.5 text-[13px] text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
        />
      </label>
      <ChipMenu label="Model" value={state.model ?? undefined} onClear={() => onChange({ model: null })}>
        {(close) => (
          <FacetMenu
            window={state.window}
            kind="models"
            onPick={(item) => {
              close();
              onChange({ model: item.value });
            }}
          />
        )}
      </ChipMenu>
      <ChipMenu label="Endpoint" value={endpointName} onClear={() => onChange({ endpoint: null })} width="w-48">
        {(close) => BATCH_ENDPOINTS.map((item) => (
          <MenuItem
            key={item.value}
            hint={item.value}
            onClick={() => {
              close();
              onChange({ endpoint: item.value });
            }}
          >
            {item.label}
          </MenuItem>
        ))}
      </ChipMenu>
      <ChipMenu
        label="Team"
        value={state.teamId ? teamLabels[state.teamId] ?? pickedTeams[state.teamId] ?? state.teamId : undefined}
        onClear={() => onChange({ teamId: null })}
      >
        {(close) => (
          <FacetMenu
            window={state.window}
            kind="teams"
            onPick={(item) => {
              close();
              setPickedTeams((current) => ({ ...current, [item.value]: item.label }));
              onChange({ teamId: item.value });
            }}
          />
        )}
      </ChipMenu>
      {summary && <span className="ml-auto text-xs tabular-nums text-gray-500">{summary}</span>}
    </div>
  );
}
