import assert from 'node:assert/strict';
import test from 'node:test';

import { auth } from '../src/lib/api/auth';
import { apiFetch } from '../src/lib/api/transport';
import {
  lastSessionActivityAt,
  onSessionRejected,
} from '../src/lib/sessionActivity';
import {
  IDLE_WARNING_MS,
  idleDeadline,
  idlePhase,
  sessionClock,
  signedOutMessage,
  signedOutReason,
  type SessionClock,
} from '../src/lib/sessionLifetime';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

function clock(overrides: Partial<SessionClock> = {}): SessionClock {
  return { expiresAt: 12 * HOUR, idleExpiresAt: HOUR, idleTimeoutMs: HOUR, ...overrides };
}

test('session deadlines are converted to the browser clock using server time', () => {
  const receivedAt = Date.parse('2026-10-13T12:00:00Z');
  const value = sessionClock({
    authenticated: true,
    server_time: '2026-10-13T12:05:00Z',
    expires_at: '2026-10-13T20:05:00Z',
    idle_expires_at: '2026-10-13T13:05:00Z',
    idle_timeout_seconds: 3600,
  }, receivedAt);

  assert.equal(value.expiresAt, Date.parse('2026-10-13T20:00:00Z'));
  assert.equal(value.idleExpiresAt, Date.parse('2026-10-13T13:00:00Z'));
  assert.equal(value.idleTimeoutMs, HOUR);
  assert.equal(sessionClock({ authenticated: true, idle_timeout_seconds: 0 }).idleTimeoutMs, null);
});

test('idle deadline follows the latest activity and never passes the hard cap', () => {
  assert.equal(idleDeadline(clock(), 0), HOUR);
  assert.equal(idleDeadline(clock(), 30 * MINUTE), 30 * MINUTE + HOUR - MINUTE);
  assert.equal(idleDeadline(clock({ expiresAt: 40 * MINUTE }), 30 * MINUTE), 40 * MINUTE);
  assert.equal(idleDeadline(clock({ idleTimeoutMs: null }), 0), null);
});

test('idle phase warns, renews after recent interaction, then checks', () => {
  const deadline = HOUR;
  assert.equal(idlePhase(clock(), 0, 0, deadline - IDLE_WARNING_MS - 1), 'active');
  assert.equal(idlePhase(clock(), 0, 0, deadline - MINUTE), 'warn');
  assert.equal(idlePhase(clock(), 0, deadline - 2 * MINUTE, deadline - MINUTE), 'renew');
  // An old interaction does not keep an unattended tab alive.
  assert.equal(idlePhase(clock(), 0, 1, deadline - MINUTE), 'warn');
  assert.equal(idlePhase(clock(), 0, 0, deadline), 'check');
  assert.equal(idlePhase(clock({ idleTimeoutMs: null }), 0, 0, 13 * HOUR), 'check');
  assert.equal(idlePhase(clock({ idleTimeoutMs: null }), 0, 0, HOUR), 'active');
});

test('a hard-capped session is not offered a renewal it cannot get', () => {
  const capped = clock({ expiresAt: HOUR, idleExpiresAt: 2 * HOUR });
  assert.equal(idlePhase(capped, 0, 0, HOUR - MINUTE), 'active');
  assert.equal(idlePhase(capped, 0, 0, HOUR), 'check');
});

test('sign-out reason distinguishes idle, the hard cap and other endings', () => {
  assert.equal(signedOutReason(clock(), 0, HOUR), 'idle');
  assert.equal(signedOutReason(clock(), 0, 12 * HOUR), 'expired');
  assert.equal(signedOutReason(clock(), 0, 10 * MINUTE), 'ended');
  assert.equal(signedOutReason(null, 0, HOUR), 'ended');
  assert.match(signedOutMessage('idle', HOUR), /60 minutes of inactivity/);
  assert.match(signedOutMessage('expired', HOUR), /time limit/);
  assert.match(signedOutMessage('ended', null), /has ended/);
});

test('a 401 from a gateway call reports a rejected session, but not from auth routes', async () => {
  const originalFetch = globalThis.fetch;
  let rejected = 0;
  const stop = onSessionRejected(() => {
    rejected += 1;
  });
  globalThis.fetch = (async () => new Response(JSON.stringify({ detail: 'Authentication required' }), {
    status: 401,
    headers: { 'content-type': 'application/json' },
  })) as typeof fetch;
  try {
    await assert.rejects(apiFetch('/ui/api/models'));
    assert.equal(rejected, 1);
    await assert.rejects(apiFetch('/auth/internal/login', { method: 'POST', json: {} }));
    assert.equal(rejected, 1);
  } finally {
    stop();
    globalThis.fetch = originalFetch;
  }
});

test('only foreground requests count as session activity', async () => {
  const originalFetch = globalThis.fetch;
  const sent: Headers[] = [];
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    sent.push(new Headers(init?.headers));
    return new Response(JSON.stringify({ authenticated: false }), {
      headers: { 'content-type': 'application/json' },
    });
  }) as typeof fetch;
  try {
    const before = lastSessionActivityAt();
    await new Promise((resolve) => setTimeout(resolve, 5));
    await auth.me({ background: true });
    assert.equal(lastSessionActivityAt(), before);
    assert.equal(sent[0].get('X-DeltaLLM-Background'), '1');

    await auth.me();
    assert.ok(lastSessionActivityAt() > before);
    assert.equal(sent[1].get('X-DeltaLLM-Background'), null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
