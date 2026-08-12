export type ReportingRangeKey = '7d' | '30d' | '3m' | '6m' | 'ytd' | 'all';
export type ReportingRangeSelectionKey = ReportingRangeKey | 'custom';
export type CompactReportingRangeSelection = ReportingRangeKey | 'custom-current' | 'custom-action';
export type SpendBucket = 'day' | 'week' | 'month';
export type ReportingAutoRefreshMs = 0 | 60_000 | 300_000;

export interface ReportingRangeOption {
  key: ReportingRangeKey;
  label: string;
  shortLabel: string;
}

export interface ResolvedReportingRange {
  key: ReportingRangeSelectionKey;
  label: string;
  shortLabel: string;
  startDate?: string;
  endDate?: string;
  bucket: SpendBucket;
}

export interface ReportingSeriesRow {
  group_key: string;
  request_count: number;
  successful_requests: number;
  failed_requests: number;
  total_spend: number;
  total_tokens: number;
}

export interface ReportingAutoRefreshOption {
  value: ReportingAutoRefreshMs;
  label: string;
  statusLabel: string;
}

export const REPORTING_RANGE_OPTIONS: readonly ReportingRangeOption[] = [
  { key: '7d', label: 'Last 7 days', shortLabel: '7 days' },
  { key: '30d', label: 'Last 30 days', shortLabel: '30 days' },
  { key: '3m', label: 'Last 3 months', shortLabel: '3 months' },
  { key: '6m', label: 'Last 6 months', shortLabel: '6 months' },
  { key: 'ytd', label: 'Year to date', shortLabel: 'YTD' },
  { key: 'all', label: 'All time', shortLabel: 'All time' },
] as const;

const RANGE_KEYS = new Set<ReportingRangeKey>(REPORTING_RANGE_OPTIONS.map((option) => option.key));
const DAY_MILLISECONDS = 24 * 60 * 60 * 1000;
const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

const REPORTING_AUTO_REFRESH_OPTIONS: Record<ReportingAutoRefreshMs, ReportingAutoRefreshOption> = {
  0: { value: 0, label: 'Off', statusLabel: 'Manual refresh' },
  60000: { value: 60_000, label: '1 minute', statusLabel: 'Every 1m' },
  300000: { value: 300_000, label: '5 minutes', statusLabel: 'Every 5m' },
};

function utcDate(year: number, month: number, day: number): Date {
  return new Date(Date.UTC(year, month, day));
}

function utcDay(value: Date): Date {
  return utcDate(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate());
}

function addUtcDays(value: Date, days: number): Date {
  const result = new Date(value);
  result.setUTCDate(result.getUTCDate() + days);
  return result;
}

function subtractUtcMonthsClamped(value: Date, months: number): Date {
  const targetMonthIndex = value.getUTCFullYear() * 12 + value.getUTCMonth() - months;
  const targetYear = Math.floor(targetMonthIndex / 12);
  const targetMonth = targetMonthIndex - targetYear * 12;
  const lastDay = utcDate(targetYear, targetMonth + 1, 0).getUTCDate();
  return utcDate(targetYear, targetMonth, Math.min(value.getUTCDate(), lastDay));
}

function isoDate(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function parseIsoDate(value: string): Date | null {
  if (!ISO_DATE_PATTERN.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) || isoDate(parsed) !== value ? null : parsed;
}

function startOfBucket(value: Date, bucket: SpendBucket): Date {
  if (bucket === 'month') return utcDate(value.getUTCFullYear(), value.getUTCMonth(), 1);
  if (bucket === 'week') {
    const mondayOffset = (value.getUTCDay() + 6) % 7;
    return addUtcDays(value, -mondayOffset);
  }
  return utcDay(value);
}

function nextBucket(value: Date, bucket: SpendBucket): Date {
  if (bucket === 'month') return utcDate(value.getUTCFullYear(), value.getUTCMonth() + 1, 1);
  return addUtcDays(value, bucket === 'week' ? 7 : 1);
}

function customBucket(start: Date, end: Date): SpendBucket {
  const inclusiveDays = Math.floor((end.getTime() - start.getTime()) / DAY_MILLISECONDS) + 1;
  if (inclusiveDays <= 45) return 'day';
  if (inclusiveDays <= 210) return 'week';
  return 'month';
}

function formatCustomRangeLabel(start: Date, end: Date): string {
  const startLabel = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: start.getUTCFullYear() === end.getUTCFullYear() ? undefined : 'numeric',
    timeZone: 'UTC',
  }).format(start);
  const endLabel = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(end);
  return `${startLabel} – ${endLabel}`;
}

export function todayIsoDate(now = new Date()): string {
  return isoDate(utcDay(now));
}

export function millisecondsUntilNextUtcDay(now = new Date(), safetyDelayMs = 1_000): number {
  const nextUtcDay = Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate() + 1,
  );
  return Math.max(1, nextUtcDay - now.getTime() + Math.max(0, safetyDelayMs));
}

export function parseReportingRangeKey(value: string | null | undefined): ReportingRangeKey {
  return value && RANGE_KEYS.has(value as ReportingRangeKey) ? (value as ReportingRangeKey) : '30d';
}

export function compactReportingRangeSelection(
  value: ReportingRangeSelectionKey,
  customOpen: boolean,
): CompactReportingRangeSelection {
  if (customOpen) return 'custom-action';
  return value === 'custom' ? 'custom-current' : value;
}

export function resolveReportingRange(key: ReportingRangeKey, now = new Date()): ResolvedReportingRange {
  const option = REPORTING_RANGE_OPTIONS.find((candidate) => candidate.key === key) ?? REPORTING_RANGE_OPTIONS[1];
  const today = utcDay(now);

  if (key === 'all') return { ...option, bucket: 'month' };

  let start: Date;
  let bucket: SpendBucket;
  if (key === '7d') {
    start = addUtcDays(today, -6);
    bucket = 'day';
  } else if (key === '30d') {
    start = addUtcDays(today, -29);
    bucket = 'day';
  } else if (key === '3m') {
    start = subtractUtcMonthsClamped(today, 3);
    bucket = 'week';
  } else if (key === '6m') {
    start = subtractUtcMonthsClamped(today, 6);
    bucket = 'week';
  } else {
    start = utcDate(today.getUTCFullYear(), 0, 1);
    bucket = 'month';
  }

  return {
    ...option,
    startDate: isoDate(start),
    endDate: isoDate(today),
    bucket,
  };
}

export function validateCustomReportingRange(
  startDate: string,
  endDate: string,
  now = new Date(),
): string | null {
  if (!startDate || !endDate) return 'Choose both a start date and an end date.';

  const start = parseIsoDate(startDate);
  const end = parseIsoDate(endDate);
  if (!start || !end) return 'Enter valid reporting dates.';
  if (start > end) return 'Start date must be on or before end date.';

  const today = utcDay(now);
  if (start > today || end > today) return 'Reporting dates cannot be in the future.';
  return null;
}

export function resolveCustomReportingRange(
  startDate: string,
  endDate: string,
  now = new Date(),
): ResolvedReportingRange | null {
  if (validateCustomReportingRange(startDate, endDate, now)) return null;

  const start = parseIsoDate(startDate);
  const end = parseIsoDate(endDate);
  if (!start || !end) return null;

  return {
    key: 'custom',
    label: formatCustomRangeLabel(start, end),
    shortLabel: 'Custom',
    startDate,
    endDate,
    bucket: customBucket(start, end),
  };
}

export function resolveReportingRangeQuery(
  range: string | null | undefined,
  startDate: string | null | undefined,
  endDate: string | null | undefined,
  now = new Date(),
): ResolvedReportingRange {
  if (range === 'custom') {
    return resolveCustomReportingRange(startDate ?? '', endDate ?? '', now) ?? resolveReportingRange('30d', now);
  }
  return resolveReportingRange(parseReportingRangeKey(range), now);
}

export function reportingRangeInclusiveDays(range: ResolvedReportingRange): number | null {
  if (!range.startDate || !range.endDate) return null;
  const start = parseIsoDate(range.startDate);
  const end = parseIsoDate(range.endDate);
  if (!start || !end) return null;
  return Math.floor((end.getTime() - start.getTime()) / DAY_MILLISECONDS) + 1;
}

export function reportingAutoRefreshOptions(
  range: ResolvedReportingRange,
): readonly ReportingAutoRefreshOption[] {
  const off = REPORTING_AUTO_REFRESH_OPTIONS[0];
  if (range.key === 'all') return [off];

  const inclusiveDays = reportingRangeInclusiveDays(range);
  if (inclusiveDays !== null && inclusiveDays <= 45) {
    return [off, REPORTING_AUTO_REFRESH_OPTIONS[60_000], REPORTING_AUTO_REFRESH_OPTIONS[300_000]];
  }
  return [off, REPORTING_AUTO_REFRESH_OPTIONS[300_000]];
}

export function withReportingRangeQuery(
  current: URLSearchParams,
  range: ResolvedReportingRange,
): URLSearchParams {
  const next = new URLSearchParams(current);
  next.set('range', range.key);

  if (range.key === 'custom' && range.startDate && range.endDate) {
    next.set('start', range.startDate);
    next.set('end', range.endDate);
  } else {
    next.delete('start');
    next.delete('end');
  }

  return next;
}

export function normalizeReportingSeries(
  rows: readonly ReportingSeriesRow[],
  range: Pick<ResolvedReportingRange, 'startDate' | 'endDate' | 'bucket'>,
): ReportingSeriesRow[] {
  if (rows.length === 0) return [];

  const sortedRows = [...rows].sort((left, right) => left.group_key.localeCompare(right.group_key));
  const normalizedRows = new Map(sortedRows.map((row) => [row.group_key.slice(0, 10), row]));
  const firstKey = sortedRows[0].group_key.slice(0, 10);
  const lastKey = sortedRows[sortedRows.length - 1].group_key.slice(0, 10);
  const firstDate = parseIsoDate(range.startDate ?? firstKey);
  const lastDate = parseIsoDate(range.endDate ?? lastKey);
  if (!firstDate || !lastDate) return sortedRows;

  const start = startOfBucket(firstDate, range.bucket);
  const end = startOfBucket(lastDate, range.bucket);
  const result: ReportingSeriesRow[] = [];

  for (let cursor = start; cursor <= end; cursor = nextBucket(cursor, range.bucket)) {
    const key = isoDate(cursor);
    result.push(
      normalizedRows.get(key) ?? {
        group_key: key,
        request_count: 0,
        successful_requests: 0,
        failed_requests: 0,
        total_spend: 0,
        total_tokens: 0,
      },
    );
  }

  return result;
}

export function formatBucketTick(value: string, bucket: SpendBucket, includeYear = false): string {
  const date = parseIsoDate(value);
  if (!date) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: bucket === 'month' ? undefined : 'numeric',
    year: includeYear ? '2-digit' : undefined,
    timeZone: 'UTC',
  }).format(date);
}

export function formatBucketLabel(value: string, bucket: SpendBucket): string {
  const date = parseIsoDate(value);
  if (!date) return value;
  const formatted = new Intl.DateTimeFormat(undefined, {
    month: bucket === 'month' ? 'long' : 'short',
    day: bucket === 'month' ? undefined : 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);
  return bucket === 'week' ? `Week of ${formatted}` : formatted;
}

export function bucketCadence(bucket: SpendBucket): 'daily' | 'weekly' | 'monthly' {
  if (bucket === 'day') return 'daily';
  if (bucket === 'week') return 'weekly';
  return 'monthly';
}

export function failureRate(failed: number, total: number): number {
  return total > 0 ? (failed / total) * 100 : 0;
}
