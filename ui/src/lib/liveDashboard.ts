import type {
  BudgetAlertItem,
  LiveDailyResponse,
  LiveHourlyResponse,
  LiveProvider,
} from './api/usageReports';
import { errorRate, percentDelta, pointDelta, type Delta } from './usageExplorer';

export interface LiveTiles {
  requests24h: number;
  requestsDelta: Delta | null;
  requestsSpark: number[];
  errorRate24h: number;
  previousErrorRate24h: number | null;
  errorRateDelta: Delta | null;
  errorSpark: number[];
  peakErrorHour: string | null;
  spend24h: number;
  spendToday: number;
  sevenDayAverage: number | null;
  spendDelta: Delta | null;
  spendSpark: number[];
  activeKeys24h: number;
}

export interface MonthToDate {
  spend: number;
  lastMonth: number | null;
  forecast: number;
  monthLabel: string;
}

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function utcHourLabel(iso: string): string {
  return `${iso.slice(11, 13)}:00`;
}

export function liveTiles(hourly: LiveHourlyResponse, daily: LiveDailyResponse | null): LiveTiles {
  const buckets = hourly.buckets;
  const current = buckets.slice(-24);
  const previous = buckets.slice(0, Math.max(buckets.length - 24, 0));
  const requests24h = sum(current.map((bucket) => bucket.request_count));
  const failed24h = sum(current.map((bucket) => bucket.failed_requests));
  const previousRequests = sum(previous.map((bucket) => bucket.request_count));
  const previousFailed = sum(previous.map((bucket) => bucket.failed_requests));
  const rate = errorRate({ request_count: requests24h, failed_requests: failed24h });
  const previousRate = previousRequests > 0
    ? errorRate({ request_count: previousRequests, failed_requests: previousFailed })
    : null;

  let peak: { hour: string; rate: number } | null = null;
  for (const bucket of current) {
    if (bucket.request_count < 10 || bucket.failed_requests === 0) continue;
    const bucketRate = bucket.failed_requests / bucket.request_count;
    if (!peak || bucketRate > peak.rate) peak = { hour: bucket.bucket, rate: bucketRate };
  }

  const today = (hourly.as_of || current[current.length - 1]?.bucket || '').slice(0, 10);
  const spendToday = sum(buckets.filter((bucket) => bucket.bucket.slice(0, 10) === today).map((bucket) => bucket.total_spend));
  const spend24h = sum(current.map((bucket) => bucket.total_spend));
  const lastWeek = (daily?.days ?? []).filter((day) => day.date < today).slice(-7);
  const weekDays = daily ? 7 : 0;
  const sevenDayAverage = weekDays > 0 ? sum(lastWeek.map((day) => day.total_spend)) / weekDays : null;

  return {
    requests24h,
    requestsDelta: percentDelta(requests24h, previousRequests > 0 ? previousRequests : null, { neutral: true }),
    requestsSpark: current.map((bucket) => bucket.request_count),
    errorRate24h: rate,
    previousErrorRate24h: previousRate,
    errorRateDelta: pointDelta(rate, previousRate),
    errorSpark: current.map((bucket) => (bucket.request_count > 0 ? bucket.failed_requests / bucket.request_count : 0)),
    peakErrorHour: peak && peak.rate > rate * 1.5 ? utcHourLabel(peak.hour) : null,
    spend24h,
    spendToday,
    sevenDayAverage,
    spendDelta: sevenDayAverage ? percentDelta(spend24h, sevenDayAverage, { higherIsBad: true }) : null,
    spendSpark: current.map((bucket) => bucket.total_spend),
    activeKeys24h: hourly.active_keys_24h,
  };
}

export function monthToDate(daily: LiveDailyResponse, spendToday: number, now = new Date()): MonthToDate {
  const monthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const monthPrefix = monthStart.toISOString().slice(0, 7);
  const thisMonth = daily.days.filter((day) => day.date.startsWith(monthPrefix));
  const earlier = daily.days.filter((day) => !day.date.startsWith(monthPrefix));
  const spend = sum(thisMonth.map((day) => day.total_spend)) + spendToday;
  const daysInMonth = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 0)).getUTCDate();
  const elapsedDays = Math.max((now.getTime() - monthStart.getTime()) / DAY, 1 / 24);
  return {
    spend,
    lastMonth: daily.start_date < monthPrefix ? sum(earlier.map((day) => day.total_spend)) : null,
    forecast: (spend / elapsedDays) * daysInMonth,
    monthLabel: new Intl.DateTimeFormat(undefined, { month: 'long', timeZone: 'UTC' }).format(monthStart),
  };
}

export type AttentionLevel = 'critical' | 'warning';

export interface AttentionItem {
  /** The condition (for example one provider's error rate), stable while it lasts. */
  id: string;
  /** How bad the condition is now. A dismissal hides it until the rank goes up. */
  rank: number;
  level: AttentionLevel;
  title: string;
  detail: string;
  action: { label: string; to: string };
}

export interface ProviderHealthInput {
  provider: string;
  label: string;
  status: 'healthy' | 'degraded' | 'down';
  models: number;
  healthy_models: number;
}

export interface AttentionInput {
  providers: readonly ProviderHealthInput[];
  liveProviders: readonly LiveProvider[] | null;
  tiles: LiveTiles | null;
  budgets: readonly BudgetAlertItem[] | null;
  formatMoney: (value: number) => string;
}

const MIN_REQUESTS_FOR_RATE = 20;

function band(value: number, step: number): number {
  return Math.floor(value / step);
}

function timeRange(first: string | null, last: string | null): string {
  if (!first || !last) return 'in the last 24 hours';
  const from = first.slice(11, 16);
  const to = last.slice(11, 16);
  return from === to ? `at ${from} UTC` : `between ${from} and ${to} UTC`;
}

function failureDetail(provider: LiveProvider): string {
  if (!provider.failed_requests) return 'No failed requests in the last 24 hours.';
  const model = provider.top_failing_model ? `${provider.top_failing_model} ` : '';
  const error = provider.top_error ? ` (${provider.top_error})` : '';
  const count = provider.top_failing_model ? provider.top_failing_model_failures : provider.failed_requests;
  return `${model}failed ${count.toLocaleString()} request${count === 1 ? '' : 's'}${error} ${timeRange(provider.first_error_at, provider.last_error_at)}.`;
}

function requestsLink(params: Record<string, string>): string {
  return `/usage/requests?${new URLSearchParams({ window: '24h', ...params }).toString()}`;
}

function budgetLink(item: BudgetAlertItem): string {
  const dimension = item.kind === 'api_key' ? 'api_key' : item.kind;
  const query = new URLSearchParams({ range: '30d', [dimension]: item.group_key });
  if (item.display_name) query.set(`${dimension}_label`, item.display_name);
  return `/usage?${query.toString()}`;
}

const BUDGET_NOUNS: Record<BudgetAlertItem['kind'], string> = {
  organization: 'organization',
  team: 'team',
  api_key: 'API key',
};

function budgetPeriod(duration: string | null): string {
  if (!duration) return '';
  if (/^(30d|1mo|month)/i.test(duration)) return 'monthly ';
  if (/^(7d|1w|week)/i.test(duration)) return 'weekly ';
  if (/^(1d|24h|day)/i.test(duration)) return 'daily ';
  return '';
}

export function attentionItems(input: AttentionInput): AttentionItem[] {
  const items: AttentionItem[] = [];
  const live = new Map((input.liveProviders ?? []).map((provider) => [provider.provider.toLowerCase(), provider]));

  for (const provider of input.providers) {
    const traffic = live.get(provider.provider.toLowerCase());
    if (provider.status !== 'healthy') {
      items.push({
        id: `provider:${provider.provider}`,
        rank: provider.status === 'down' ? 2 : 1,
        level: provider.status === 'down' ? 'critical' : 'warning',
        title: `${provider.label} is ${provider.status === 'down' ? 'down' : 'degraded'}`,
        detail: traffic
          ? failureDetail(traffic)
          : `${provider.healthy_models} of ${provider.models} model${provider.models === 1 ? '' : 's'} pass health checks.`,
        action: {
          label: 'View failing requests',
          to: requestsLink({ status: 'error', provider: provider.provider }),
        },
      });
      live.delete(provider.provider.toLowerCase());
    }
  }

  for (const traffic of live.values()) {
    if (traffic.request_count < MIN_REQUESTS_FOR_RATE) continue;
    const rate = traffic.failed_requests / traffic.request_count;
    if (rate < 0.1) continue;
    items.push({
      id: `provider-errors:${traffic.provider}`,
      rank: band(rate, 0.1),
      level: rate >= 0.25 ? 'critical' : 'warning',
      title: `${Math.round(rate * 100)}% of ${traffic.provider} requests failed in the last 24 hours`,
      detail: failureDetail(traffic),
      action: { label: 'View failing requests', to: requestsLink({ status: 'error', provider: traffic.provider }) },
    });
  }

  const tiles = input.tiles;
  if (tiles && tiles.requests24h >= 50 && tiles.errorRate24h >= 0.05) {
    if (tiles.errorRate24h >= (tiles.previousErrorRate24h ?? 0) * 2) {
      items.push({
        id: 'errors',
        rank: band(tiles.errorRate24h, 0.05),
        level: tiles.errorRate24h >= 0.15 ? 'critical' : 'warning',
        title: `Error rate is ${(tiles.errorRate24h * 100).toFixed(1)}% in the last 24 hours`,
        detail: tiles.peakErrorHour
          ? `Most failures came around ${tiles.peakErrorHour} UTC.`
          : `That is ${tiles.errorRateDelta?.label ?? 'up'} compared with the 24 hours before.`,
        action: { label: 'View failing requests', to: requestsLink({ status: 'error' }) },
      });
    }
  }

  if (tiles && tiles.sevenDayAverage && tiles.sevenDayAverage >= 1 && tiles.spend24h >= tiles.sevenDayAverage * 1.3) {
    const ratio = tiles.spend24h / tiles.sevenDayAverage;
    items.push({
      id: 'spend',
      rank: band(ratio, 0.5),
      level: 'warning',
      title: `Spend in the last 24 hours is ${Math.round((ratio - 1) * 100)}% above the 7-day average`,
      detail: `${input.formatMoney(tiles.spend24h)} against an average of ${input.formatMoney(tiles.sevenDayAverage)} a day.`,
      action: { label: 'See what changed', to: '/usage?range=7d&split=model' },
    });
  }

  for (const budget of input.budgets ?? []) {
    if (budget.max_budget <= 0) continue;
    const share = budget.spend / budget.max_budget;
    const name = budget.display_name || (budget.kind === 'api_key' ? `Key …${budget.group_key.slice(-4)}` : budget.group_key);
    const over = share >= 1;
    const reset = budget.budget_reset_at ? ` It resets on ${budget.budget_reset_at.slice(0, 10)}.` : '';
    items.push({
      id: `budget:${budget.kind}:${budget.group_key}`,
      rank: over ? 3 : share >= 0.9 ? 2 : 1,
      level: over ? 'critical' : 'warning',
      title: over
        ? `${name} used all of its ${budgetPeriod(budget.budget_duration)}budget`
        : `${name} is at ${Math.floor(share * 100)}% of its ${budgetPeriod(budget.budget_duration)}budget`,
      detail: `${input.formatMoney(budget.spend)} of ${input.formatMoney(budget.max_budget)}.${over ? ` Requests from this ${BUDGET_NOUNS[budget.kind]} get 429 errors until the budget resets or goes up.` : ''}${reset}`,
      action: { label: `View ${BUDGET_NOUNS[budget.kind]} usage`, to: budgetLink(budget) },
    });
  }

  return items.sort((a, b) => (a.level === b.level ? 0 : a.level === 'critical' ? -1 : 1));
}

export interface DismissalStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export const DISMISSAL_TTL_MS = 7 * DAY;
const MAX_DISMISSALS = 50;

/** Dismissed conditions for one account: when, and how bad the condition was then. */
export type Dismissals = Record<string, { at: number; rank: number }>;

function dismissalKey(principal: string): string {
  return `deltallm.dashboard.dismissed.v2.${principal}`;
}

/** Hidden while the condition is no worse than when it was dismissed. */
export function isDismissed(item: AttentionItem, dismissals: Readonly<Dismissals>): boolean {
  const entry = dismissals[item.id];
  return entry !== undefined && item.rank <= entry.rank;
}

/** Dismissed alerts for this account, without expired ones. Never throws. */
export function readDismissals(storage: DismissalStorage | null, principal: string, now = Date.now()): Dismissals {
  if (!storage) return {};
  try {
    const raw = storage.getItem(dismissalKey(principal));
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const result: Dismissals = {};
    for (const [id, entry] of Object.entries(parsed as Record<string, unknown>)) {
      if (!entry || typeof entry !== 'object') continue;
      const { at, rank } = entry as { at?: unknown; rank?: unknown };
      if (typeof at === 'number' && typeof rank === 'number' && now - at < DISMISSAL_TTL_MS) {
        result[id] = { at, rank };
      }
    }
    return result;
  } catch {
    return {};
  }
}

export function writeDismissals(storage: DismissalStorage | null, principal: string, dismissals: Dismissals): void {
  if (!storage) return;
  const newest = Object.entries(dismissals).sort(([, a], [, b]) => b.at - a.at).slice(0, MAX_DISMISSALS);
  try {
    if (newest.length === 0) storage.removeItem(dismissalKey(principal));
    else storage.setItem(dismissalKey(principal), JSON.stringify(Object.fromEntries(newest)));
  } catch {
    // Storage can be full or blocked; dismissals then last for this page only.
  }
}
