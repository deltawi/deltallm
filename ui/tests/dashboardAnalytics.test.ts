import assert from 'node:assert/strict';
import test from 'node:test';

import {
  beginDashboardReport,
  completeDashboardReport,
  dashboardReportError,
  dashboardReportPending,
  dashboardReportingRangesMatch,
  failDashboardReport,
  initialDashboardReportState,
} from '../src/lib/dashboardAnalytics';

test('a provider failure preserves the last successful provider data', () => {
  const initial = initialDashboardReportState<{ total: number }>();
  const loaded = completeDashboardReport(
    beginDashboardReport(initial, 'range-a:0'),
    'range-a:0',
    { total: 12 },
  );
  const refreshing = beginDashboardReport(loaded, 'range-a:1');
  const failed = failDashboardReport(refreshing, 'range-a:1', 'provider unavailable');

  assert.deepEqual(failed.data, { total: 12 });
  assert.equal(dashboardReportError(failed, 'range-a:1'), 'provider unavailable');
  assert.equal(dashboardReportPending(failed, 'range-a:1'), false);
});

test('stale dashboard completions and failures cannot overwrite a newer generation', () => {
  const initial = initialDashboardReportState<{ total: number }>();
  const first = beginDashboardReport(initial, 'range-a');
  const second = beginDashboardReport(first, 'range-b');

  assert.equal(completeDashboardReport(second, 'range-a', { total: 10 }), second);
  assert.equal(failDashboardReport(second, 'range-a', 'stale failure'), second);
  assert.equal(dashboardReportPending(second, 'range-b'), true);
  assert.equal(dashboardReportError(second, 'range-a'), null);
});

test('starting a new range retains data while marking the new generation pending', () => {
  const initial = initialDashboardReportState<{ range: string }>();
  const loaded = completeDashboardReport(
    beginDashboardReport(initial, 'range-a'),
    'range-a',
    { range: 'range-a' },
  );
  const next = beginDashboardReport(loaded, 'range-b');

  assert.deepEqual(next.data, { range: 'range-a' });
  assert.equal(dashboardReportPending(next, 'range-b'), true);
  assert.equal(next.error, null);
});

test('provider data is reusable only for the exact applied reporting window', () => {
  const current = { startDate: '2026-07-13', endDate: '2026-08-11', bucket: 'day' };

  assert.equal(dashboardReportingRangesMatch(current, { ...current }), true);
  assert.equal(dashboardReportingRangesMatch(current, { ...current, startDate: '2026-08-05' }), false);
  assert.equal(dashboardReportingRangesMatch(current, { ...current, bucket: 'week' }), false);
  assert.equal(dashboardReportingRangesMatch(null, current), false);
});
