import assert from 'node:assert/strict';
import test from 'node:test';

import type { UsageSeriesResponse } from '../src/lib/api/usageReports';
import {
  bucketKeys,
  OTHER_COLOR,
  percentDelta,
  periodChange,
  pointDelta,
  SERIES_COLORS,
  sparklineValues,
  usageChartData,
} from '../src/lib/usageExplorer';

test('spend going up is bad, volume changes stay neutral', () => {
  assert.deepEqual(percentDelta(120, 100, { higherIsBad: true }), { label: '+20%', tone: 'bad' });
  assert.deepEqual(percentDelta(80, 100, { higherIsBad: true }), { label: '−20%', tone: 'good' });
  assert.deepEqual(percentDelta(120, 100, { neutral: true }), { label: '+20%', tone: 'flat' });
  assert.deepEqual(percentDelta(5, 0), { label: 'New', tone: 'flat' });
  assert.equal(percentDelta(5, null), null);
});

test('big growth is shown as a capped multiple, not a giant percent', () => {
  // Load tests on a near-idle model: $0.0000017 -> $0.0232 used to read +1379975%.
  assert.deepEqual(percentDelta(0.0232, 0.0000017, { higherIsBad: true }), { label: '>100×', tone: 'bad' });
  assert.deepEqual(percentDelta(1.36, 0.0063, { higherIsBad: true }), { label: '>100×', tone: 'bad' });
  assert.deepEqual(percentDelta(0.00723952, 0.000576), { label: '+12×', tone: 'good' });
  assert.deepEqual(percentDelta(240, 100, { neutral: true }), { label: '+2.4×', tone: 'flat' });
  assert.deepEqual(percentDelta(200, 100, { neutral: true }), { label: '+2×', tone: 'flat' });
  assert.deepEqual(percentDelta(199, 100, { neutral: true }), { label: '+99%', tone: 'flat' });
  assert.deepEqual(percentDelta(9_990, 1_000, { neutral: true }), { label: '+10×', tone: 'flat' });
  assert.deepEqual(percentDelta(1, 100, { neutral: true }), { label: '−99%', tone: 'flat' });
});

test('period change gives a direction and a size without a sign', () => {
  assert.deepEqual(periodChange(177, 100), { direction: 'up', size: '77%', ratio: 0.77 });
  assert.deepEqual(periodChange(37, 100), { direction: 'down', size: '63%', ratio: -0.63 });
  assert.deepEqual(periodChange(100, 100), { direction: 'flat', size: '0%', ratio: 0 });
  assert.equal(periodChange(100, 100.01)?.direction, 'flat');
  assert.equal(periodChange(5, 0), null);
  assert.equal(periodChange(5, null), null);
  assert.equal(periodChange(130, 10)?.size, '13×');
  assert.equal(periodChange(1_000, 10)?.size, '>100×');
});

test('error rate changes are shown in points', () => {
  assert.deepEqual(pointDelta(0.031, 0.012), { label: '+1.9 pt', tone: 'bad' });
  assert.deepEqual(pointDelta(0.01, 0.02), { label: '−1 pt', tone: 'good' });
  assert.deepEqual(pointDelta(0.02, 0.0201), { label: '0 pt', tone: 'flat' });
});

test('chart buckets cover every day and week in the window', () => {
  assert.deepEqual(bucketKeys('2026-10-01', '2026-10-03', 'day', []), ['2026-10-01', '2026-10-02', '2026-10-03']);
  // 2026-10-01 is a Thursday, so the first week starts on Monday 2026-09-28.
  assert.deepEqual(bucketKeys('2026-10-01', '2026-10-12', 'week', []), ['2026-09-28', '2026-10-05', '2026-10-12']);
  assert.deepEqual(bucketKeys(null, '2026-10-02', 'day', ['2026-10-01']), ['2026-10-01', '2026-10-02']);
});

const series: UsageSeriesResponse = {
  interval: 'day',
  split_by: 'model',
  window: { start_date: '2026-10-01', end_date: '2026-10-03', previous_start_date: null, previous_end_date: null },
  groups: [
    { series_index: 1, group_key: 'gpt-4o', display_name: null, is_unassigned: false, is_other: false, total_spend: 9, total_tokens: 90, request_count: 3, failed_requests: 1 },
    { series_index: 2, group_key: 'claude', display_name: 'Sonnet', is_unassigned: false, is_other: false, total_spend: 4, total_tokens: 40, request_count: 2, failed_requests: 0 },
    { series_index: 0, group_key: null, display_name: null, is_unassigned: false, is_other: true, total_spend: 1, total_tokens: 10, request_count: 1, failed_requests: 0 },
  ],
  buckets: [
    { bucket: '2026-10-01', series_index: 1, total_spend: 5, total_tokens: 50, request_count: 2, failed_requests: 1 },
    { bucket: '2026-10-01', series_index: 0, total_spend: 1, total_tokens: 10, request_count: 1, failed_requests: 0 },
    { bucket: '2026-10-03T00:00:00', series_index: 1, total_spend: 4, total_tokens: 40, request_count: 1, failed_requests: 0 },
    { bucket: '2026-10-03', series_index: 2, total_spend: 4, total_tokens: 40, request_count: 2, failed_requests: 0 },
  ],
};

test('chart data stacks ranked groups, then "Other" in grey', () => {
  const chart = usageChartData(series, 'spend');
  assert.deepEqual(chart.series.map((item) => [item.label, item.color, item.total]), [
    ['gpt-4o', SERIES_COLORS[0], 9],
    ['Sonnet', SERIES_COLORS[1], 4],
    ['Other models', OTHER_COLOR, 1],
  ]);
  assert.deepEqual(chart.rows.map((row) => [row.bucket, row.total, row.s1, row.s2, row.s0]), [
    ['2026-10-01', 6, 5, 0, 1],
    ['2026-10-02', 0, 0, 0, 0],
    ['2026-10-03', 8, 4, 4, 0],
  ]);
  assert.deepEqual(usageChartData(series, 'errors').rows.map((row) => row.total), [1, 0, 0]);
});

test('sparklines add all groups for each day', () => {
  assert.deepEqual(sparklineValues(series, (values) => values.request_count), [3, 0, 3]);
  assert.deepEqual(sparklineValues(null, (values) => values.request_count), []);
});
