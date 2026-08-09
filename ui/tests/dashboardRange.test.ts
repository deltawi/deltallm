import assert from 'node:assert/strict';
import test from 'node:test';

import {
  failureRate,
  normalizeRequestSeries,
  parseDashboardRangeKey,
  resolveDashboardRange,
  type RequestSeriesRow,
} from '../src/lib/dashboardRange';

const NOW = new Date('2026-08-08T16:00:00Z');

test('dashboard ranges use inclusive UTC boundaries and adaptive buckets', () => {
  assert.deepEqual(resolveDashboardRange('7d', NOW), {
    key: '7d',
    label: 'Last 7 days',
    shortLabel: '7 days',
    startDate: '2026-08-02',
    endDate: '2026-08-08',
    bucket: 'day',
  });
  assert.equal(resolveDashboardRange('30d', NOW).startDate, '2026-07-10');
  assert.equal(resolveDashboardRange('3m', NOW).bucket, 'week');
  assert.equal(resolveDashboardRange('6m', NOW).startDate, '2026-02-08');
  assert.equal(resolveDashboardRange('ytd', NOW).startDate, '2026-01-01');
  assert.deepEqual(resolveDashboardRange('all', NOW), {
    key: 'all',
    label: 'All time',
    shortLabel: 'All time',
    bucket: 'month',
  });
});

test('calendar month ranges clamp safely at month end', () => {
  const result = resolveDashboardRange('3m', new Date('2024-05-31T12:00:00Z'));
  assert.equal(result.startDate, '2024-02-29');
  assert.equal(result.endDate, '2024-05-31');
});

test('range parsing falls back to the 30 day default', () => {
  assert.equal(parseDashboardRangeKey('ytd'), 'ytd');
  assert.equal(parseDashboardRangeKey('unexpected'), '30d');
  assert.equal(parseDashboardRangeKey(null), '30d');
});

test('request series fills missing daily buckets with zero values', () => {
  const rows: RequestSeriesRow[] = [
    {
      group_key: '2026-08-02',
      request_count: 8,
      successful_requests: 7,
      failed_requests: 1,
      total_spend: 1,
      total_tokens: 100,
    },
    {
      group_key: '2026-08-04',
      request_count: 4,
      successful_requests: 4,
      failed_requests: 0,
      total_spend: 0.5,
      total_tokens: 50,
    },
  ];

  const result = normalizeRequestSeries(rows, {
    startDate: '2026-08-02',
    endDate: '2026-08-04',
    bucket: 'day',
  });

  assert.equal(result.length, 3);
  assert.equal(result[1].group_key, '2026-08-03');
  assert.equal(result[1].request_count, 0);
  assert.equal(result[2].request_count, 4);
});

test('weekly series aligns the requested range to Monday buckets', () => {
  const rows: RequestSeriesRow[] = [
    {
      group_key: '2026-08-03',
      request_count: 10,
      successful_requests: 9,
      failed_requests: 1,
      total_spend: 1,
      total_tokens: 100,
    },
  ];
  const result = normalizeRequestSeries(rows, {
    startDate: '2026-08-08',
    endDate: '2026-08-17',
    bucket: 'week',
  });

  assert.deepEqual(result.map((row) => row.group_key), ['2026-08-03', '2026-08-10', '2026-08-17']);
});

test('all-time monthly series fills gaps using the returned bounds', () => {
  const row = (group_key: string): RequestSeriesRow => ({
    group_key,
    request_count: 1,
    successful_requests: 1,
    failed_requests: 0,
    total_spend: 1,
    total_tokens: 1,
  });
  const result = normalizeRequestSeries([row('2026-03-01'), row('2026-01-01')], { bucket: 'month' });

  assert.deepEqual(result.map((item) => item.group_key), ['2026-01-01', '2026-02-01', '2026-03-01']);
  assert.equal(result[1].request_count, 0);
});

test('failure rate is safe for empty periods', () => {
  assert.equal(failureRate(1_399, 21_955).toFixed(1), '6.4');
  assert.equal(failureRate(0, 0), 0);
});
