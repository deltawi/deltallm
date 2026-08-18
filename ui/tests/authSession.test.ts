import assert from 'node:assert/strict';
import test from 'node:test';

import { classifySessionCheckError, isValidSessionPayload } from '../src/lib/authSession';

function httpError(status: number, retryAfterSeconds?: number): Error {
  return Object.assign(new Error(`HTTP ${status}`), { status, retryAfterSeconds });
}

test('treats explicit authentication rejection as anonymous', () => {
  assert.equal(classifySessionCheckError(httpError(401)).kind, 'anonymous');
  assert.equal(classifySessionCheckError(httpError(403)).kind, 'anonymous');
});

test('keeps transient HTTP and transport failures retryable', () => {
  for (const status of [408, 425, 429, 500, 502, 503, 504]) {
    assert.equal(classifySessionCheckError(httpError(status)).kind, 'retryable');
  }
  assert.equal(classifySessionCheckError(new TypeError('network failed')).kind, 'retryable');

  const aborted = new Error('aborted');
  aborted.name = 'AbortError';
  assert.equal(classifySessionCheckError(aborted).kind, 'retryable');
});

test('preserves Retry-After guidance for rate limiting', () => {
  const failure = classifySessionCheckError(httpError(429, 12));

  assert.equal(failure.kind, 'retryable');
  assert.equal(failure.retryAfterSeconds, 12);
  assert.match(failure.message, /12 seconds/);
});

test('surfaces unexpected HTTP and application failures as fatal', () => {
  for (const status of [400, 404, 409, 422]) {
    assert.equal(classifySessionCheckError(httpError(status)).kind, 'fatal');
  }
  assert.equal(classifySessionCheckError(new Error('unexpected')).kind, 'fatal');
  assert.equal(classifySessionCheckError('unexpected').kind, 'fatal');
});

test('requires a complete session-verification response contract', () => {
  assert.equal(isValidSessionPayload({ authenticated: false }), true);
  assert.equal(isValidSessionPayload({ authenticated: true, auth_mode: 'session' }), true);
  assert.equal(isValidSessionPayload({ authenticated: true, auth_mode: 'master_key' }), true);

  assert.equal(isValidSessionPayload({}), false);
  assert.equal(isValidSessionPayload({ authenticated: 'yes' }), false);
  assert.equal(isValidSessionPayload({ authenticated: true }), false);
  assert.equal(isValidSessionPayload({ authenticated: true, auth_mode: 'unknown' }), false);
});
