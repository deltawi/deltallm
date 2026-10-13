// Requests carrying this header are authenticated but do not count as activity,
// so auto-refresh and session checks never keep an idle session signed in.
export const BACKGROUND_REQUEST_HEADER = 'X-DeltaLLM-Background';

type Listener = () => void;

const rejectedListeners = new Set<Listener>();
let lastActivityAt = Date.now();

export function isBackgroundRequest(headers: Headers): boolean {
  return headers.get(BACKGROUND_REQUEST_HEADER) === '1';
}

export function backgroundRequestHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  headers.set(BACKGROUND_REQUEST_HEADER, '1');
  return headers;
}

/** Time of the last request the gateway counted as activity. */
export function lastSessionActivityAt(): number {
  return lastActivityAt;
}

export function markSessionActivity(at = Date.now()): void {
  lastActivityAt = Math.max(lastActivityAt, at);
}

/** A gateway call came back 401: the session may have ended. */
export function notifySessionRejected(): void {
  rejectedListeners.forEach((listener) => listener());
}

export function onSessionRejected(listener: Listener): () => void {
  rejectedListeners.add(listener);
  return () => {
    rejectedListeners.delete(listener);
  };
}

// Calls to these paths answer for themselves (wrong password, expired invite);
// a 401 there is not a sign that the current session ended.
const SESSION_NEUTRAL_PREFIXES = ['/auth/'];

export function isSessionNeutralPath(path: string): boolean {
  return SESSION_NEUTRAL_PREFIXES.some((prefix) => path.startsWith(prefix));
}
