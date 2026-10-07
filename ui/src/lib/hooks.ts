import { useSessionPrincipal } from './authContext';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export function useApi<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const principal = useSessionPrincipal();
  const dataPrincipal = useRef(principal);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const mountedRef = useRef(true);
  const requestIdRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  const stableDeps = useMemo(() => [...deps, nonce, principal], [deps, nonce, principal]);

  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);

    fetcher(controller.signal)
      .then((res) => {
        if (requestId !== requestIdRef.current || !mountedRef.current) return;
        if (controller.signal.aborted) return;
        dataPrincipal.current = principal;
        setData(res);
      })
      .catch((err) => {
        if (
          requestId !== requestIdRef.current
          || !mountedRef.current
          || controller.signal.aborted
        ) return;
        setError(err);
      })
      .finally(() => {
        if (requestId !== requestIdRef.current || !mountedRef.current) return;
        setLoading(false);
      });

    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, stableDeps);

  return { data: dataPrincipal.current === principal ? data : null, error, loading, refetch };
}
