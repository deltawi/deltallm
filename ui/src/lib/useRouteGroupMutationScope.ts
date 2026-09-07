import { useCallback, useEffect, useRef } from 'react';

export function useRouteGroupMutationScope() {
  const pending = useRef<AbortController | null>(null);

  useEffect(() => () => {
    pending.current?.abort();
    pending.current = null;
  }, []);

  const begin = useCallback(() => {
    if (pending.current) return null;
    const controller = new AbortController();
    pending.current = controller;
    return controller;
  }, []);

  const isCurrent = useCallback((controller: AbortController) => (
    pending.current === controller && !controller.signal.aborted
  ), []);

  const finish = useCallback((controller: AbortController) => {
    if (!isCurrent(controller)) return false;
    pending.current = null;
    return true;
  }, [isCurrent]);

  return { begin, isCurrent, finish };
}
