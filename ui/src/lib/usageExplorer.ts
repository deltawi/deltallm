import type {
  UsageDimension,
  UsageInterval,
  UsageMetrics,
  UsageSeriesGroup,
  UsageSeriesResponse,
} from './api/usageReports';
import { groupLabel } from './usageFilters';

export type UsageMetric = 'spend' | 'requests' | 'tokens' | 'errors';

export const USAGE_METRICS: ReadonlyArray<{ key: UsageMetric; label: string }> = [
  { key: 'spend', label: 'Spend' },
  { key: 'requests', label: 'Requests' },
  { key: 'tokens', label: 'Tokens' },
  { key: 'errors', label: 'Errors' },
];

/** Ranked series colors, then the color for "Other" and unassigned groups. */
export const SERIES_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4'] as const;
export const OTHER_COLOR = '#a9abb8';
export const SPLIT_GROUPS = 4;

export type DeltaTone = 'bad' | 'good' | 'flat';

export interface Delta {
  label: string;
  tone: DeltaTone;
}

interface MetricValues {
  total_spend: number;
  total_tokens: number;
  request_count: number;
  failed_requests: number;
}

export function metricValue(values: MetricValues, metric: UsageMetric): number {
  if (metric === 'spend') return values.total_spend;
  if (metric === 'tokens') return values.total_tokens;
  if (metric === 'errors') return values.failed_requests;
  return values.request_count;
}

export function errorRate(values: Pick<MetricValues, 'request_count' | 'failed_requests'>): number {
  return values.request_count > 0 ? values.failed_requests / values.request_count : 0;
}

export function costPerThousandRequests(values: Pick<MetricValues, 'total_spend' | 'request_count'>): number {
  return values.request_count > 0 ? (values.total_spend / values.request_count) * 1000 : 0;
}

/**
 * Percent change against the previous window. Spend going up is shown as bad
 * (``higherIsBad``); request and token volume changes stay neutral.
 */
export function percentDelta(
  current: number,
  previous: number | null | undefined,
  { higherIsBad = false, neutral = false }: { higherIsBad?: boolean; neutral?: boolean } = {},
): Delta | null {
  if (previous === null || previous === undefined) return null;
  if (previous === 0) return current === 0 ? { label: '0%', tone: 'flat' } : { label: 'New', tone: 'flat' };
  const change = (current - previous) / previous;
  const rounded = Math.abs(change) >= 0.1 ? Math.round(change * 100) : Math.round(change * 1000) / 10;
  const label = `${rounded > 0 ? '+' : rounded < 0 ? '−' : ''}${Math.abs(rounded)}%`;
  if (neutral || Math.abs(change) < 0.02) return { label, tone: 'flat' };
  const worse = higherIsBad ? change > 0 : change < 0;
  return { label, tone: worse ? 'bad' : 'good' };
}

/** Change in percentage points, for rates such as the error rate. */
export function pointDelta(current: number, previous: number | null | undefined): Delta | null {
  if (previous === null || previous === undefined) return null;
  const points = Math.round((current - previous) * 1000) / 10;
  const label = `${points > 0 ? '+' : points < 0 ? '−' : ''}${Math.abs(points)} pt`;
  if (Math.abs(points) < 0.1) return { label, tone: 'flat' };
  return { label, tone: points > 0 ? 'bad' : 'good' };
}

export function summaryErrorRateDelta(current: UsageMetrics, previous: UsageMetrics | null): Delta | null {
  return previous ? pointDelta(errorRate(current), errorRate(previous)) : null;
}

function isoDay(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function parseDay(value: string): Date {
  return new Date(`${value.slice(0, 10)}T00:00:00Z`);
}

function bucketStart(value: Date, interval: UsageInterval): Date {
  if (interval === 'month') return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), 1));
  if (interval === 'week') {
    const result = new Date(value);
    result.setUTCDate(result.getUTCDate() - ((value.getUTCDay() + 6) % 7));
    return result;
  }
  return value;
}

function nextBucket(value: Date, interval: UsageInterval): Date {
  if (interval === 'month') return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth() + 1, 1));
  const result = new Date(value);
  result.setUTCDate(result.getUTCDate() + (interval === 'week' ? 7 : 1));
  return result;
}

/** Every bucket from the first to the last day, so days without traffic show as gaps. */
export function bucketKeys(
  startDate: string | null,
  endDate: string,
  interval: UsageInterval,
  observed: readonly string[],
): string[] {
  const firstObserved = [...observed].sort()[0];
  const first = startDate ?? firstObserved;
  if (!first) return [];
  const keys: string[] = [];
  const end = parseDay(endDate);
  for (let cursor = bucketStart(parseDay(first), interval); cursor <= end && keys.length < 400; cursor = nextBucket(cursor, interval)) {
    keys.push(isoDay(cursor));
  }
  return keys;
}

export interface ChartSeries {
  dataKey: string;
  seriesIndex: number;
  label: string;
  color: string;
  total: number;
}

export interface ChartRow {
  bucket: string;
  total: number;
  [dataKey: string]: number | string;
}

export interface UsageChartData {
  series: ChartSeries[];
  rows: ChartRow[];
}

function seriesColor(group: UsageSeriesGroup, rankedPosition: number): string {
  if (group.is_other || group.is_unassigned) return OTHER_COLOR;
  return SERIES_COLORS[rankedPosition % SERIES_COLORS.length];
}

function otherLabel(response: UsageSeriesResponse, split: UsageDimension): string {
  const ranked = response.groups.filter((group) => !group.is_other).length;
  return ranked > 0 ? `Other ${split === 'provider' ? 'providers' : split === 'team' ? 'teams' : 'models'}` : 'All';
}

export function usageChartData(response: UsageSeriesResponse, metric: UsageMetric): UsageChartData {
  const split = response.split_by ?? 'model';
  let ranked = 0;
  const series = response.groups.map((group) => {
    const position = group.is_other ? -1 : ranked++;
    return {
      dataKey: `s${group.series_index}`,
      seriesIndex: group.series_index,
      label: group.is_other ? otherLabel(response, split) : groupLabel(split, group),
      color: seriesColor(group, Math.max(position, 0)),
      total: metricValue(group, metric),
    };
  });
  const keys = bucketKeys(
    response.window.start_date,
    response.window.end_date,
    response.interval,
    response.buckets.map((bucket) => bucket.bucket),
  );
  const rows = new Map<string, ChartRow>();
  for (const key of keys) {
    const row: ChartRow = { bucket: key, total: 0 };
    for (const item of series) row[item.dataKey] = 0;
    rows.set(key, row);
  }
  for (const bucket of response.buckets) {
    const key = bucket.bucket.slice(0, 10);
    const row = rows.get(key) ?? { bucket: key, total: 0 };
    const value = metricValue(bucket, metric);
    const dataKey = `s${bucket.series_index}`;
    row[dataKey] = Number(row[dataKey] ?? 0) + value;
    row.total += value;
    rows.set(key, row);
  }
  return { series, rows: [...rows.values()].sort((a, b) => a.bucket.localeCompare(b.bucket)) };
}

/** Per-bucket totals for a KPI sparkline. */
export function sparklineValues(response: UsageSeriesResponse | null, pick: (values: MetricValues) => number): number[] {
  if (!response) return [];
  const keys = bucketKeys(
    response.window.start_date,
    response.window.end_date,
    response.interval,
    response.buckets.map((bucket) => bucket.bucket),
  );
  const totals = new Map<string, MetricValues>(
    keys.map((key) => [key, { total_spend: 0, total_tokens: 0, request_count: 0, failed_requests: 0 }]),
  );
  for (const bucket of response.buckets) {
    const key = bucket.bucket.slice(0, 10);
    const current = totals.get(key) ?? { total_spend: 0, total_tokens: 0, request_count: 0, failed_requests: 0 };
    current.total_spend += bucket.total_spend;
    current.total_tokens += bucket.total_tokens;
    current.request_count += bucket.request_count;
    current.failed_requests += bucket.failed_requests;
    totals.set(key, current);
  }
  return [...totals.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, values]) => pick(values));
}
