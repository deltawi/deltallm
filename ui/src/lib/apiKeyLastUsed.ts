export interface KeyLastUsedDisplay {
  label: string;
  /** Full local time for a tooltip, or null when there is no recorded use. */
  title: string | null;
}

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * Label for the key "Last used" value. The gateway writes this value in
 * batches, so it can be about one minute old; finer labels would be false.
 */
export function keyLastUsedDisplay(value: string | null | undefined, now: number = Date.now()): KeyLastUsedDisplay {
  if (!value) return { label: 'Never', title: null };
  const used = new Date(value);
  const usedMs = used.getTime();
  if (Number.isNaN(usedMs)) return { label: '—', title: null };
  const title = used.toLocaleString();
  // A small future value comes from clock skew between replicas and the browser.
  const elapsed = Math.max(0, now - usedMs);
  if (elapsed < MINUTE_MS) return { label: 'Just now', title };
  if (elapsed < HOUR_MS) return { label: `${Math.floor(elapsed / MINUTE_MS)} min ago`, title };
  if (elapsed < DAY_MS) return { label: `${Math.floor(elapsed / HOUR_MS)} h ago`, title };
  if (elapsed < 30 * DAY_MS) return { label: `${Math.floor(elapsed / DAY_MS)} d ago`, title };
  return {
    label: used.toLocaleDateString(undefined, { month: 'short', day: '2-digit', year: 'numeric' }),
    title,
  };
}
