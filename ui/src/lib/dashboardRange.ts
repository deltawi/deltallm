export type DashboardRangeKey = '7d' | '30d' | '3m' | '6m' | 'ytd' | 'all';
export type SpendBucket = 'day' | 'week' | 'month';

export interface DashboardRangeOption {
  key: DashboardRangeKey;
  label: string;
  shortLabel: string;
}

export interface ResolvedDashboardRange extends DashboardRangeOption {
  startDate?: string;
  endDate?: string;
  bucket: SpendBucket;
}

export interface RequestSeriesRow {
  group_key: string;
  request_count: number;
  successful_requests: number;
  failed_requests: number;
  total_spend: number;
  total_tokens: number;
}

export const DASHBOARD_RANGE_OPTIONS: readonly DashboardRangeOption[] = [
  { key: '7d', label: 'Last 7 days', shortLabel: '7 days' },
  { key: '30d', label: 'Last 30 days', shortLabel: '30 days' },
  { key: '3m', label: 'Last 3 months', shortLabel: '3 months' },
  { key: '6m', label: 'Last 6 months', shortLabel: '6 months' },
  { key: 'ytd', label: 'Year to date', shortLabel: 'YTD' },
  { key: 'all', label: 'All time', shortLabel: 'All time' },
] as const;

const RANGE_KEYS = new Set<DashboardRangeKey>(DASHBOARD_RANGE_OPTIONS.map((option) => option.key));

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

function parseIsoDate(value: string): Date {
  return new Date(`${value.slice(0, 10)}T00:00:00Z`);
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

export function parseDashboardRangeKey(value: string | null | undefined): DashboardRangeKey {
  return value && RANGE_KEYS.has(value as DashboardRangeKey) ? (value as DashboardRangeKey) : '30d';
}

export function resolveDashboardRange(key: DashboardRangeKey, now = new Date()): ResolvedDashboardRange {
  const option = DASHBOARD_RANGE_OPTIONS.find((candidate) => candidate.key === key) ?? DASHBOARD_RANGE_OPTIONS[1];
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

export function normalizeRequestSeries(
  rows: readonly RequestSeriesRow[],
  range: Pick<ResolvedDashboardRange, 'startDate' | 'endDate' | 'bucket'>,
): RequestSeriesRow[] {
  if (rows.length === 0) return [];

  const sortedRows = [...rows].sort((left, right) => left.group_key.localeCompare(right.group_key));
  const normalizedRows = new Map(sortedRows.map((row) => [row.group_key.slice(0, 10), row]));
  const firstKey = sortedRows[0].group_key.slice(0, 10);
  const lastKey = sortedRows[sortedRows.length - 1].group_key.slice(0, 10);
  const start = startOfBucket(parseIsoDate(range.startDate ?? firstKey), range.bucket);
  const end = startOfBucket(parseIsoDate(range.endDate ?? lastKey), range.bucket);
  const result: RequestSeriesRow[] = [];

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
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: bucket === 'month' ? undefined : 'numeric',
    year: includeYear ? '2-digit' : undefined,
    timeZone: 'UTC',
  }).format(date);
}

export function formatBucketLabel(value: string, bucket: SpendBucket): string {
  const date = parseIsoDate(value);
  const formatted = new Intl.DateTimeFormat(undefined, {
    month: bucket === 'month' ? 'long' : 'short',
    day: bucket === 'month' ? undefined : 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);
  return bucket === 'week' ? `Week of ${formatted}` : formatted;
}

export function failureRate(failed: number, total: number): number {
  return total > 0 ? (failed / total) * 100 : 0;
}
