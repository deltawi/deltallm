import { useEffect, useRef } from 'react';

export type AutoRefreshMs = 0 | 60_000 | 300_000;

export const AUTO_REFRESH_OPTIONS: ReadonlyArray<{ value: AutoRefreshMs; label: string }> = [
  { value: 0, label: 'Auto refresh off' },
  { value: 60_000, label: 'Every minute' },
  { value: 300_000, label: 'Every 5 minutes' },
];

/** Call ``onTick`` every ``intervalMs`` while the browser tab is visible. */
export function useAutoRefresh(intervalMs: number, onTick: () => void) {
  const tick = useRef(onTick);
  useEffect(() => {
    tick.current = onTick;
  });
  useEffect(() => {
    if (!intervalMs) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') tick.current();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
}
