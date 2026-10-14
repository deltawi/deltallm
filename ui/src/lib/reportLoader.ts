/**
 * Loading rules for report panels on the Usage page and the Dashboard.
 *
 * - A panel loads only while it is visible (its key is not null).
 * - At most two report requests run at the same time; the others wait. The
 *   gateway also limits reporting queries, so this keeps the page from
 *   getting "busy" answers when many panels mount together.
 * - Results stay in a small in-memory cache for the page session, so going
 *   back to a tab or a filter shows the last result at once while it
 *   refreshes.
 */

export interface ReportQueue {
  run<T>(task: () => Promise<T>, signal?: AbortSignal): Promise<T>;
  readonly active: number;
  readonly waiting: number;
}

function abortError(): Error {
  const error = new Error('The report request was cancelled.');
  error.name = 'AbortError';
  return error;
}

export function createReportQueue(maxConcurrent: number): ReportQueue {
  let active = 0;
  const waiting: Array<{ start: () => void; signal?: AbortSignal; cancel: () => void }> = [];

  function next() {
    while (active < maxConcurrent && waiting.length > 0) {
      const entry = waiting.shift();
      if (!entry) return;
      if (entry.signal?.aborted) {
        entry.cancel();
        continue;
      }
      entry.start();
    }
  }

  return {
    get active() {
      return active;
    },
    get waiting() {
      return waiting.length;
    },
    run<T>(task: () => Promise<T>, signal?: AbortSignal): Promise<T> {
      if (signal?.aborted) return Promise.reject(abortError());
      return new Promise<T>((resolve, reject) => {
        const entry = {
          signal,
          cancel: () => reject(abortError()),
          start: () => {
            active += 1;
            signal?.removeEventListener('abort', onAbort);
            task().then(resolve, reject).finally(() => {
              active -= 1;
              next();
            });
          },
        };
        function onAbort() {
          const index = waiting.indexOf(entry);
          if (index >= 0) {
            waiting.splice(index, 1);
            entry.cancel();
          }
        }
        signal?.addEventListener('abort', onAbort, { once: true });
        waiting.push(entry);
        next();
      });
    },
  };
}

export interface ReportCacheEntry<T> {
  value: T;
  storedAt: number;
}

export interface ReportCache {
  get<T>(key: string): ReportCacheEntry<T> | undefined;
  set<T>(key: string, value: T, now?: number): void;
  clear(): void;
  readonly size: number;
}

export function createReportCache(maxEntries: number): ReportCache {
  const entries = new Map<string, ReportCacheEntry<unknown>>();
  return {
    get size() {
      return entries.size;
    },
    get<T>(key: string) {
      const entry = entries.get(key);
      if (!entry) return undefined;
      // Refresh recency: a Map keeps insertion order.
      entries.delete(key);
      entries.set(key, entry);
      return entry as ReportCacheEntry<T>;
    },
    set<T>(key: string, value: T, now = Date.now()) {
      entries.delete(key);
      entries.set(key, { value, storedAt: now });
      while (entries.size > maxEntries) {
        const oldest = entries.keys().next().value;
        if (oldest === undefined) break;
        entries.delete(oldest);
      }
    },
    clear() {
      entries.clear();
    },
  };
}

/** True when a cached result is recent enough to show without a new request. */
export function reportCacheFresh(entry: ReportCacheEntry<unknown> | undefined, maxAgeMs: number, now = Date.now()): boolean {
  return entry !== undefined && now - entry.storedAt < maxAgeMs;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export const reportQueue = createReportQueue(2);
export const reportCache = createReportCache(60);
