import assert from 'node:assert/strict';
import test from 'node:test';

import {
  bucketCadence,
  failureRate,
  formatBucketLabel,
  formatBucketTick,
  millisecondsUntilNextUtcDay,
  normalizeReportingSeries,
  parseReportingRangeKey,
  reportingAutoRefreshOptions,
  reportingRangeInclusiveDays,
  resolveCustomReportingRange,
  resolveReportingRange,
  resolveReportingRangeQuery,
  todayIsoDate,
  validateCustomReportingRange,
  withReportingRangeQuery,
  type ReportingSeriesRow,
} from '../src/lib/reportingRange';

const NOW = new Date('2026-08-08T16:00:00Z');

function row(group_key: string, spend = 1): ReportingSeriesRow {
  return {
    group_key,
    request_count: 1,
    successful_requests: 1,
    failed_requests: 0,
    total_spend: spend,
    total_tokens: 1,
  };
}

test('reporting presets use inclusive UTC boundaries and adaptive buckets', () => {
  assert.deepEqual(resolveReportingRange('7d', NOW), {
    key: '7d',
    label: 'Last 7 days',
    shortLabel: '7 days',
    startDate: '2026-08-02',
    endDate: '2026-08-08',
    bucket: 'day',
  });
  assert.equal(resolveReportingRange('30d', NOW).startDate, '2026-07-10');
  assert.equal(resolveReportingRange('3m', NOW).bucket, 'week');
  assert.equal(resolveReportingRange('6m', NOW).startDate, '2026-02-08');
  assert.equal(resolveReportingRange('ytd', NOW).startDate, '2026-01-01');
  assert.deepEqual(resolveReportingRange('all', NOW), {
    key: 'all',
    label: 'All time',
    shortLabel: 'All time',
    bucket: 'month',
  });
  assert.equal(todayIsoDate(NOW), '2026-08-08');
});

test('calendar month presets clamp safely at month end', () => {
  const result = resolveReportingRange('3m', new Date('2024-05-31T12:00:00Z'));
  assert.equal(result.startDate, '2024-02-29');
  assert.equal(result.endDate, '2024-05-31');
});

test('UTC rollover scheduling targets the next reporting day with a safety margin', () => {
  assert.equal(
    millisecondsUntilNextUtcDay(new Date('2026-08-08T23:59:59.500Z')),
    1_500,
  );
  assert.equal(
    millisecondsUntilNextUtcDay(new Date('2026-08-08T12:00:00Z'), 0),
    12 * 60 * 60 * 1_000,
  );
});

test('preset parsing falls back to the 30 day default', () => {
  assert.equal(parseReportingRangeKey('ytd'), 'ytd');
  assert.equal(parseReportingRangeKey('custom'), '30d');
  assert.equal(parseReportingRangeKey('unexpected'), '30d');
  assert.equal(parseReportingRangeKey(null), '30d');
});

test('custom ranges validate required, ordered, real, and non-future dates', () => {
  assert.equal(validateCustomReportingRange('', '', NOW), 'Choose both a start date and an end date.');
  assert.equal(validateCustomReportingRange('2026-02-30', '2026-03-01', NOW), 'Enter valid reporting dates.');
  assert.equal(validateCustomReportingRange('2026-08-02', '2026-08-01', NOW), 'Start date must be on or before end date.');
  assert.equal(validateCustomReportingRange('2026-08-01', '2026-08-09', NOW), 'Reporting dates cannot be in the future.');
  assert.equal(validateCustomReportingRange('2026-08-01', '2026-08-08', NOW), null);
});

test('custom ranges choose buckets that keep the series readable', () => {
  assert.equal(resolveCustomReportingRange('2026-01-01', '2026-02-14', NOW)?.bucket, 'day');
  assert.equal(resolveCustomReportingRange('2026-01-01', '2026-02-15', NOW)?.bucket, 'week');
  assert.equal(resolveCustomReportingRange('2026-01-01', '2026-07-29', NOW)?.bucket, 'week');
  assert.equal(resolveCustomReportingRange('2026-01-01', '2026-07-30', NOW)?.bucket, 'month');
  assert.equal(resolveCustomReportingRange('2026-08-01', '2026-08-09', NOW), null);
});

test('auto refresh policy scales with reporting range cost', () => {
  const shortRange = resolveReportingRange('30d', NOW);
  const mediumRange = resolveReportingRange('3m', NOW);
  const allTime = resolveReportingRange('all', NOW);

  assert.equal(reportingRangeInclusiveDays(shortRange), 30);
  assert.deepEqual(reportingAutoRefreshOptions(shortRange).map((option) => option.value), [0, 60_000, 300_000]);
  assert.deepEqual(reportingAutoRefreshOptions(mediumRange).map((option) => option.value), [0, 300_000]);
  assert.deepEqual(reportingAutoRefreshOptions(allTime).map((option) => option.value), [0]);
});

test('URL range resolution restores valid custom dates and safely falls back', () => {
  const custom = resolveReportingRangeQuery('custom', '2026-07-01', '2026-07-31', NOW);
  assert.equal(custom.key, 'custom');
  assert.equal(custom.startDate, '2026-07-01');
  assert.equal(custom.endDate, '2026-07-31');
  assert.equal(custom.bucket, 'day');

  const invalid = resolveReportingRangeQuery('custom', '2026-08-09', '2026-08-10', NOW);
  assert.equal(invalid.key, '30d');
  assert.equal(invalid.startDate, '2026-07-10');

  assert.equal(resolveReportingRangeQuery('all', null, null, NOW).startDate, undefined);
});

test('canonical reporting query parameters preserve unrelated state', () => {
  const current = new URLSearchParams('tab=logs&range=custom&start=bad&end=values');
  const fallback = resolveReportingRangeQuery('custom', 'bad', 'values', NOW);
  const canonicalFallback = withReportingRangeQuery(current, fallback);

  assert.equal(canonicalFallback.toString(), 'tab=logs&range=30d');

  const custom = resolveCustomReportingRange('2026-07-01', '2026-07-31', NOW);
  assert.ok(custom);
  const canonicalCustom = withReportingRangeQuery(new URLSearchParams('tab=overview'), custom);
  assert.equal(canonicalCustom.toString(), 'tab=overview&range=custom&start=2026-07-01&end=2026-07-31');

  const allTime = withReportingRangeQuery(
    new URLSearchParams('range=custom&start=2026-01-01&end=2026-02-01&tab=logs'),
    resolveReportingRange('all', NOW),
  );
  assert.equal(allTime.toString(), 'range=all&tab=logs');
});

test('reporting series fills missing daily buckets with zero values', () => {
  const result = normalizeReportingSeries([row('2026-08-02', 2), row('2026-08-04', 4)], {
    startDate: '2026-08-02',
    endDate: '2026-08-04',
    bucket: 'day',
  });

  assert.deepEqual(result.map((item) => item.group_key), ['2026-08-02', '2026-08-03', '2026-08-04']);
  assert.equal(result[1].request_count, 0);
  assert.equal(result[1].total_spend, 0);
  assert.equal(result[2].total_spend, 4);
});

test('weekly series aligns requested dates to Monday buckets', () => {
  const result = normalizeReportingSeries([row('2026-08-03')], {
    startDate: '2026-08-08',
    endDate: '2026-08-17',
    bucket: 'week',
  });

  assert.deepEqual(result.map((item) => item.group_key), ['2026-08-03', '2026-08-10', '2026-08-17']);
});

test('all-time monthly series uses returned bounds and fills gaps', () => {
  const result = normalizeReportingSeries([row('2026-03-01'), row('2026-01-01')], { bucket: 'month' });

  assert.deepEqual(result.map((item) => item.group_key), ['2026-01-01', '2026-02-01', '2026-03-01']);
  assert.equal(result[1].request_count, 0);
});

test('bounded monthly series fills missing calendar months', () => {
  const result = normalizeReportingSeries([row('2026-01-01'), row('2026-03-01')], {
    startDate: '2026-01-15',
    endDate: '2026-03-20',
    bucket: 'month',
  });

  assert.deepEqual(result.map((item) => item.group_key), ['2026-01-01', '2026-02-01', '2026-03-01']);
  assert.equal(result[1].total_spend, 0);
});

test('empty and malformed series are handled safely', () => {
  assert.deepEqual(normalizeReportingSeries([], { bucket: 'month' }), []);
  assert.deepEqual(normalizeReportingSeries([row('not-a-date')], { bucket: 'day' }), [row('not-a-date')]);
});

test('bucket labels and cadence describe the selected aggregation', () => {
  assert.ok(formatBucketTick('2026-08-08', 'day').length > 0);
  assert.match(formatBucketLabel('2026-08-03', 'week'), /^Week of /);
  assert.equal(formatBucketLabel('invalid', 'day'), 'invalid');
  assert.equal(bucketCadence('day'), 'daily');
  assert.equal(bucketCadence('week'), 'weekly');
  assert.equal(bucketCadence('month'), 'monthly');
});

test('failure rate is safe for empty periods', () => {
  assert.equal(failureRate(1_399, 21_955).toFixed(1), '6.4');
  assert.equal(failureRate(0, 0), 0);
});
