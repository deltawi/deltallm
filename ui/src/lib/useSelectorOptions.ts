import { useEffect, useMemo, useState } from 'react';
import { routeGroups, type SelectorOptionsQuery } from './api/routeGroups';
import { useExplicitReport } from './useExplicitReport';

interface Lookup { groupId: string; query: SelectorOptionsQuery }
const load = (input: Lookup, signal: AbortSignal) => routeGroups.selectorOptions(input.groupId, input.query, signal);

// One bounded page/result, shared abort and stale-result ownership; no polling or persistence.
export function useSelectorOptions(groupId: string, scope: string, selectedId: string, allowed: boolean) {
  const [search, setSearch] = useState('');
  const [querySearch, setQuerySearch] = useState('');
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    if (search.trim() === querySearch) return;
    const timer = setTimeout(() => { setQuerySearch(search.trim()); setOffset(0); }, 250);
    return () => clearTimeout(timer);
  }, [search, querySearch]);
  const input = useMemo<Lookup>(() => ({ groupId, query: {
    search: querySearch, offset, limit: 20, ...(selectedId ? { selected_id: selectedId } : {}),
  } }), [groupId, querySearch, offset, selectedId]);
  const report = useExplicitReport(scope, JSON.stringify(input), load);
  const { run, reset } = report;
  useEffect(() => {
    if (allowed) void run(input);
    else reset();
  }, [allowed, input, run, reset]);
  return {
    ...report, search, setSearch, offset,
    next: () => setOffset((current) => current + 20),
    previous: () => setOffset((current) => Math.max(0, current - 20)),
    refresh: () => run(input),
  };
}
