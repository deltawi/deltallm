import { useCallback, useEffect, useRef, useState } from 'react';

interface Identity { scope: string; fingerprint: string }
interface Result<T> extends Identity { data: T }

// One request and one result, no polling, persistence or background query cache.
export function useExplicitReport<Input, Output>(
  scope: string, fingerprint: string, loader: (input: Input, signal: AbortSignal) => Promise<Output>,
) {
  const [result, setResult] = useState<Result<Output> | null>(null);
  const [failure, setFailure] = useState<(Identity & { error: unknown }) | null>(null);
  const [pending, setPending] = useState<(Identity & { signal: AbortSignal }) | null>(null);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const mounted = useRef(true);
  const same = (value: Identity | null) => value?.scope === scope && value.fingerprint === fingerprint;
  const abort = useCallback(() => { generation.current++; controller.current?.abort(); }, []);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; abort(); };
  }, [abort]);
  useEffect(() => abort, [scope, fingerprint, abort]);

  const run = useCallback(async (input: Input) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const id = ++generation.current;
    const identity = { scope, fingerprint };
    const accepted = () => mounted.current && id === generation.current && !current.signal.aborted;
    setPending({ ...identity, signal: current.signal });
    setFailure(null);
    try {
      const data = await loader(input, current.signal);
      if (!accepted()) return null;
      setResult({ ...identity, data });
      return data;
    } catch (error: unknown) {
      if (accepted()) setFailure({ ...identity, error });
      return null;
    } finally {
      if (accepted()) setPending(null);
      if (controller.current === current) controller.current = null;
    }
  }, [scope, fingerprint, loader]);

  const reset = useCallback(() => {
    generation.current++;
    controller.current?.abort();
    controller.current = null;
    setPending(null);
    setResult(null);
    setFailure(null);
  }, []);

  return {
    data: result?.scope === scope ? result.data : null,
    error: same(failure) ? failure?.error : null,
    loading: same(pending) && !pending?.signal.aborted,
    stale: result?.scope === scope && !same(result),
    run, reset,
  };
}
