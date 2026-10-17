import { useEffect, useState } from 'react';
import { keys } from '../../lib/api';

const POLL_MS = 5_000;
const POLL_LIMIT_MS = 10 * 60_000;

/** Polls one key until the gateway records a use, for the "Check the connection" box. */
export function useKeyFirstUse(token: string | null, initial: string | null = null): string | null {
  const [found, setFound] = useState<{ token: string; lastUsedAt: string } | null>(null);
  useEffect(() => {
    if (!token || initial) return;
    const controller = new AbortController();
    const started = Date.now();
    let timer = 0;
    const poll = async () => {
      try {
        const result = await keys.list({ search: token, coding_agent: true, limit: 1 }, controller.signal);
        const row = result.data.find((item) => item.token === token);
        if (row?.last_used_at) {
          setFound({ token, lastUsedAt: row.last_used_at });
          return;
        }
      } catch {
        if (controller.signal.aborted) return;
      }
      if (Date.now() - started < POLL_LIMIT_MS) timer = window.setTimeout(poll, POLL_MS);
    };
    timer = window.setTimeout(poll, POLL_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [token, initial]);
  if (initial) return initial;
  return found && found.token === token ? found.lastUsedAt : null;
}
