import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BATCH_TABS,
  batchListFilters,
  batchPace,
  batchProgress,
  canCancelJob,
  fmtAgo,
  fmtCost,
  hasBatchFilters,
  itemStatus,
  jobStatus,
  parseBatchListState,
  writeBatchListState,
} from '../src/lib/batchJobs';

const NOW = Date.parse('2026-10-16T12:00:00Z');

test('status names use plain words and an unknown status shows as is', () => {
  assert.deepEqual(jobStatus('in_progress'), { label: 'Running', tone: 'run' });
  assert.deepEqual(itemStatus('pending'), { label: 'Waiting', tone: 'idle' });
  assert.deepEqual(jobStatus('paused'), { label: 'paused', tone: 'idle' });
});

test('list state reads the URL, drops unknown values and writes back only what is set', () => {
  const state = parseBatchListState(new URLSearchParams('status=failed&window=7d&q=%20doc%20&model=bge-m3&endpoint=/v1/other&team=t1&page=3'));
  assert.deepEqual(state, {
    tab: 'failed',
    window: '7d',
    search: ' doc ',
    model: 'bge-m3',
    endpoint: null,
    teamId: 't1',
    page: 3,
  });
  assert.equal(writeBatchListState(state).toString(), 'status=failed&window=7d&q=doc&model=bge-m3&team=t1&page=3');

  const clean = parseBatchListState(new URLSearchParams('status=nope&window=1y&page=-2'));
  assert.equal(clean.tab, 'all');
  assert.equal(clean.window, 'all');
  assert.equal(clean.page, 1);
  assert.equal(writeBatchListState(clean).toString(), '');
  assert.equal(hasBatchFilters(clean), false);
  assert.equal(hasBatchFilters({ ...clean, search: '  ' }), false);
  assert.equal(hasBatchFilters({ ...clean, model: 'm' }), true);
});

test('API filters map the tab to statuses and the window to a start time', () => {
  const state = parseBatchListState(new URLSearchParams('status=running&window=24h&endpoint=/v1/embeddings'));
  assert.deepEqual(batchListFilters(state, new Date(NOW)), {
    search: undefined,
    status: 'in_progress,finalizing',
    model: undefined,
    endpoint: '/v1/embeddings',
    team_id: undefined,
    created_after: '2026-10-15T12:00:00.000Z',
  });
  const all = batchListFilters(parseBatchListState(new URLSearchParams()), new Date(NOW));
  assert.equal(all.status, undefined);
  assert.equal(all.created_after, undefined);
});

test('tab counts come from the summary, and old servers without new keys count zero', () => {
  const summary = { total: 9, queued: 1, in_progress: 3, completed: 4, failed: 1, cancelled: 0 };
  const counts = Object.fromEntries(BATCH_TABS.map((tab) => [tab.key, tab.count(summary)]));
  assert.deepEqual(counts, { all: 9, running: 3, queued: 1, completed: 4, failed: 1, cancelled: 0, expired: 0 });
});

test('progress counts finished lines and rounds the percent down', () => {
  const progress = batchProgress({ total: 3, completed_items: 1, failed_items: 1, in_progress_items: 1, cancelled_items: 0 });
  assert.deepEqual(progress, { total: 3, done: 1, failed: 1, running: 1, cancelled: 0, waiting: 0, percent: 66 });
  assert.equal(batchProgress({ total: 0, completed_items: 0, failed_items: 0, in_progress_items: 0, cancelled_items: 0 }).percent, 0);
  // Counters that run ahead of the total never show more than 100%.
  assert.equal(batchProgress({ total: 2, completed_items: 3, failed_items: 0, in_progress_items: 0, cancelled_items: 0 }).percent, 100);
});

test('pace needs a minute of run time and a few finished lines', () => {
  const progress = batchProgress({ total: 1000, completed_items: 100, failed_items: 0, in_progress_items: 0, cancelled_items: 0 });
  const started = new Date(NOW - 10 * 60_000).toISOString();
  const pace = batchPace(progress, started, NOW);
  assert.ok(pace);
  assert.equal(pace.perMinute, 10);
  assert.equal(pace.remainingMs, 90 * 60_000);
  assert.equal(batchPace(progress, new Date(NOW - 30_000).toISOString(), NOW), null);
  assert.equal(batchPace(batchProgress({ total: 10, completed_items: 2, failed_items: 0, in_progress_items: 0, cancelled_items: 0 }), started, NOW), null);
  assert.equal(batchPace(progress, null, NOW), null);
});

test('cancel shows only for a running job the caller may change', () => {
  const base = { status: 'in_progress', capabilities: { cancel: true }, cancel_requested_at: null };
  assert.equal(canCancelJob(base), true);
  assert.equal(canCancelJob({ ...base, status: 'completed' }), false);
  assert.equal(canCancelJob({ ...base, capabilities: { cancel: false } }), false);
  assert.equal(canCancelJob({ ...base, cancel_requested_at: '2026-10-16T11:00:00Z' }), false);
});

test('times and money read well in a table', () => {
  assert.equal(fmtAgo(new Date(NOW - 20_000).toISOString(), NOW), 'Just now');
  assert.equal(fmtAgo(new Date(NOW - 5 * 60_000).toISOString(), NOW), '5 min ago');
  assert.equal(fmtAgo(new Date(NOW - 3 * 3_600_000).toISOString(), NOW), '3 h ago');
  assert.equal(fmtAgo(null, NOW), '—');
  assert.equal(fmtCost(2.034), '$2.03');
  assert.equal(fmtCost(0.00025), '$0.00025');
  assert.equal(fmtCost(0), '$0');
  assert.equal(fmtCost(null), '—');
});
