import { useCallback, useEffect, useRef, useState } from 'react';
import { useSessionPrincipal } from './authContext';
import { errorMessage } from './errorMessage';
import { isAbortError, reportCache, reportCacheFresh, reportQueue } from './reportLoader';

export interface ReportLoadOptions {
  signal: AbortSignal;
  force: boolean;
  /** Set for an automatic reload, so it does not count as session activity. */
  background: boolean;
}

export interface ReportState<T> {
  data: T | null;
  error: string | null;
  /** No result yet for this key: show a skeleton. */
  loading: boolean;
  /** A result is shown and a newer one is loading. */
  refreshing: boolean;
  updatedAt: number | null;
  retry: () => void;
}

interface Settled<T> {
  key: string;
  data: T | null;
  error: string | null;
  updatedAt: number | null;
}

const DEFAULT_MAX_AGE_MS = 60_000;

/**
 * Load one report panel. Pass ``key = null`` while the panel is hidden: nothing
 * loads until it becomes visible. A ``refreshToken`` change (the Refresh
 * button) forces a reload that also asks the server to skip its cache. A
 * ``reloadToken`` change (auto refresh) reloads but lets the server answer from
 * its shared cache, so many open pages cost one database read. Auto-refresh
 * requests are marked as background, so they never keep an idle session alive.
 */
export function useReport<T>(
  key: string | null,
  load: (options: ReportLoadOptions) => Promise<T>,
  {
    refreshToken = 0,
    reloadToken = 0,
    maxAgeMs = DEFAULT_MAX_AGE_MS,
  }: { refreshToken?: number; reloadToken?: number; maxAgeMs?: number } = {},
): ReportState<T> {
  const principal = useSessionPrincipal();
  const fullKey = key === null ? null : `${principal}|${key}`;
  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const [inflightKey, setInflightKey] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const loadRef = useRef(load);
  const lastRefreshToken = useRef(refreshToken);
  const lastRetryNonce = useRef(retryNonce);
  const lastReloadToken = useRef(reloadToken);

  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    if (fullKey === null) return;
    const forced = lastRefreshToken.current !== refreshToken;
    const userRetry = lastRetryNonce.current !== retryNonce;
    const reloaded = lastReloadToken.current !== reloadToken;
    const retried = userRetry || reloaded;
    // Only a reload that the timer started is background work. A key change in
    // the same render (a rolling window moving forward) is part of that reload.
    const background = reloaded && !forced && !userRetry;
    lastRefreshToken.current = refreshToken;
    lastRetryNonce.current = retryNonce;
    lastReloadToken.current = reloadToken;
    const cached = reportCache.get<T>(fullKey);
    if (!forced && !retried && reportCacheFresh(cached, maxAgeMs)) return;

    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) setInflightKey(fullKey);
    });
    reportQueue
      .run(() => loadRef.current({ signal: controller.signal, force: forced, background }), controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        const storedAt = Date.now();
        reportCache.set(fullKey, value, storedAt);
        setSettled({ key: fullKey, data: value, error: null, updatedAt: storedAt });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        const previous = reportCache.get<T>(fullKey);
        setSettled({
          key: fullKey,
          data: previous?.value ?? null,
          error: errorMessage(error, 'This report could not be loaded.'),
          updatedAt: previous?.storedAt ?? null,
        });
      })
      .finally(() => {
        if (!controller.signal.aborted) setInflightKey((current) => (current === fullKey ? null : current));
      });
    return () => controller.abort();
  }, [fullKey, maxAgeMs, refreshToken, reloadToken, retryNonce]);

  const retry = useCallback(() => setRetryNonce((value) => value + 1), []);

  if (fullKey === null) {
    return { data: null, error: null, loading: false, refreshing: false, updatedAt: null, retry };
  }
  const cached = reportCache.get<T>(fullKey);
  const own = settled?.key === fullKey ? settled : null;
  const data = own?.data ?? cached?.value ?? null;
  const error = own?.error ?? null;
  return {
    data,
    error,
    loading: data === null && error === null,
    refreshing: data !== null && inflightKey === fullKey,
    updatedAt: own?.updatedAt ?? cached?.storedAt ?? null,
    retry,
  };
}
