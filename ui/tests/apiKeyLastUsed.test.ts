import assert from 'node:assert/strict';
import test from 'node:test';

import { keyLastUsedDisplay } from '../src/lib/apiKeyLastUsed';

const NOW = Date.parse('2026-10-12T12:00:00Z');

function labelFor(offsetMs: number): string {
  return keyLastUsedDisplay(new Date(NOW - offsetMs).toISOString(), NOW).label;
}

test('last used shows Never for a key without a recorded use', () => {
  assert.deepEqual(keyLastUsedDisplay(null, NOW), { label: 'Never', title: null });
  assert.deepEqual(keyLastUsedDisplay(undefined, NOW), { label: 'Never', title: null });
  assert.deepEqual(keyLastUsedDisplay('', NOW), { label: 'Never', title: null });
});

test('last used shows coarse relative time', () => {
  assert.equal(labelFor(20_000), 'Just now');
  assert.equal(labelFor(-30_000), 'Just now');
  assert.equal(labelFor(5 * 60_000), '5 min ago');
  assert.equal(labelFor(3 * 3_600_000), '3 h ago');
  assert.equal(labelFor(2 * 86_400_000), '2 d ago');
});

test('last used shows a date for old values and keeps the full time as a title', () => {
  const value = '2026-07-01T08:30:00+00:00';
  const display = keyLastUsedDisplay(value, NOW);
  assert.equal(display.label, new Date(value).toLocaleDateString(undefined, { month: 'short', day: '2-digit', year: 'numeric' }));
  assert.equal(display.title, new Date(value).toLocaleString());
});

test('last used does not show an invalid value as a time', () => {
  assert.deepEqual(keyLastUsedDisplay('not-a-date', NOW), { label: '—', title: null });
});
