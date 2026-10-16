import { useEffect, useMemo, useState } from 'react';
import { models, type ModelDeploymentDetail } from './api';
import { useApi } from './hooks';

const SEARCH_DELAY_MS = 250;
const PAGE_SIZE = 20;

/**
 * Server-side search for deployments that can join a route group.
 * Nothing is fetched while `active` is false, so closed pickers make no requests.
 */
export function useDeploymentCandidates(mode: string, active: boolean, excludeIds: readonly string[] = []) {
  const [input, setInput] = useState('');
  const [search, setSearch] = useState('');

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(input.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [input]);

  const result = useApi(
    (signal) => (active ? models.list({ search, mode, limit: PAGE_SIZE, offset: 0 }, signal) : Promise.resolve(null)),
    [active, search, mode],
  );
  const excluded = useMemo(() => new Set(excludeIds), [excludeIds]);
  const options = useMemo<ModelDeploymentDetail[]>(
    () => (result.data?.data ?? []).filter((item) => !excluded.has(item.deployment_id)),
    [excluded, result.data?.data],
  );

  return {
    search: input,
    setSearch: setInput,
    options,
    loading: active && result.loading,
    error: active ? result.error : null,
    hasMore: !!result.data?.pagination.has_more,
    reset: () => { setInput(''); setSearch(''); },
  };
}
