import type { SessionInfo } from './authTypes';

export type SignedOutReason = 'idle' | 'expired' | 'ended';

/** Session deadlines converted to this browser's clock. */
export interface SessionClock {
  expiresAt: number | null;
  idleExpiresAt: number | null;
  idleTimeoutMs: number | null;
}

export type IdlePhase = 'active' | 'renew' | 'warn' | 'check';

// The gateway records activity at most once a minute, so its idle clock can start
// up to a minute before the browser's.
const LAST_SEEN_SLACK_MS = 60_000;
export const IDLE_WARNING_MS = 2 * 60_000;
// Recent keyboard or pointer use near the deadline renews quietly instead of prompting.
const RECENT_INTERACTION_MS = 5 * 60_000;
const REASON_TOLERANCE_MS = 2 * 60_000;

function parseTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function sessionClock(session: SessionInfo, receivedAt = Date.now()): SessionClock {
  const serverTime = parseTime(session.server_time);
  const skew = serverTime === null ? 0 : serverTime - receivedAt;
  const local = (value: string | null | undefined) => {
    const parsed = parseTime(value);
    return parsed === null ? null : parsed - skew;
  };
  const idleTimeoutSeconds = session.idle_timeout_seconds;
  return {
    expiresAt: local(session.expires_at),
    idleExpiresAt: local(session.idle_expires_at),
    idleTimeoutMs: idleTimeoutSeconds && idleTimeoutSeconds > 0 ? idleTimeoutSeconds * 1000 : null,
  };
}

export function idleDeadline(clock: SessionClock, lastActivityAt: number): number | null {
  if (clock.idleTimeoutMs === null) return null;
  const fromActivity = lastActivityAt + clock.idleTimeoutMs - LAST_SEEN_SLACK_MS;
  const deadline = Math.max(clock.idleExpiresAt ?? fromActivity, fromActivity);
  return clock.expiresAt === null ? deadline : Math.min(deadline, clock.expiresAt);
}

export function idlePhase(
  clock: SessionClock,
  lastActivityAt: number,
  lastInteractionAt: number,
  now = Date.now(),
): IdlePhase {
  if (clock.expiresAt !== null && now >= clock.expiresAt) return 'check';
  const deadline = idleDeadline(clock, lastActivityAt);
  if (deadline === null) return 'active';
  if (now >= deadline) return 'check';
  if (now < deadline - IDLE_WARNING_MS) return 'active';
  // Close to the hard cap, renewing cannot help; let the session run out.
  if (clock.expiresAt !== null && deadline >= clock.expiresAt) return 'active';
  const interactedSinceActivity = lastInteractionAt > lastActivityAt;
  return interactedSinceActivity && now - lastInteractionAt <= RECENT_INTERACTION_MS ? 'renew' : 'warn';
}

export function signedOutReason(
  clock: SessionClock | null,
  lastActivityAt: number,
  now = Date.now(),
): SignedOutReason {
  if (!clock) return 'ended';
  if (clock.expiresAt !== null && now >= clock.expiresAt - REASON_TOLERANCE_MS) return 'expired';
  const deadline = idleDeadline(clock, lastActivityAt);
  if (deadline !== null && now >= deadline - REASON_TOLERANCE_MS) return 'idle';
  return 'ended';
}

export function signedOutMessage(reason: SignedOutReason, idleTimeoutMs: number | null): string {
  if (reason === 'idle') {
    const minutes = idleTimeoutMs ? Math.round(idleTimeoutMs / 60_000) : null;
    return minutes
      ? `You were signed out after ${minutes} minutes of inactivity. Sign in again to continue.`
      : 'You were signed out after a period of inactivity. Sign in again to continue.';
  }
  if (reason === 'expired') return 'Your session reached its time limit. Sign in again to continue.';
  return 'Your session has ended. Sign in again to continue.';
}
