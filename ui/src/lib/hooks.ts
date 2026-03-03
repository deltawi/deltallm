import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  const stableDeps = useMemo(() => [...deps, nonce], [deps, nonce]);

  useEffect(() => {
    let canceled = false;
    setLoading(true);
    setError(null);

    fetcher()
      .then((res) => {
        if (canceled || !mountedRef.current) return;
        setData(res);
      })
      .catch((err) => {
        if (canceled || !mountedRef.current) return;
        setError(err);
        setData(null);
      })
      .finally(() => {
        if (canceled || !mountedRef.current) return;
        setLoading(false);
      });

    return () => {
      canceled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, stableDeps);

  return { data, error, loading, refetch };
}

