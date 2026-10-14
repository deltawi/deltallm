import assert from 'node:assert/strict';
import test from 'node:test';

import type { LiveHourlyResponse, LiveProvider } from '../src/lib/api/usageReports';
import {
  attentionItems,
  DISMISSAL_TTL_MS,
  isDismissed,
  liveTiles,
  monthToDate,
  readDismissals,
  writeDismissals,
  type DismissalStorage,
  type LiveTiles,
} from '../src/lib/liveDashboard';

function hourly(previous: { requests: number; failed: number; spend: number }, current: { requests: number; failed: number; spend: number }): LiveHourlyResponse {
  const start = Date.parse('2026-10-12T11:00:00Z');
  const buckets = Array.from({ length: 48 }, (_, index) => {
    const values = index < 24 ? previous : current;
    return {
      bucket: new Date(start + index * 3_600_000).toISOString(),
      request_count: values.requests,
      failed_requests: values.failed,
      total_spend: values.spend,
    };
  });
  return { buckets, active_keys_24h: 7, as_of: '2026-10-14T10:30:00Z' };
}

const money = (value: number) => `$${value.toFixed(2)}`;

test('live tiles compare the last 24 hours with the 24 hours before', () => {
  const daily = {
    start_date: '2026-09-01',
    end_date: '2026-10-13',
    days: Array.from({ length: 7 }, (_, index) => ({
      date: `2026-10-${String(7 + index).padStart(2, '0')}`,
      total_spend: 10,
      request_count: 100,
      failed_requests: 1,
    })),
  };
  const tiles = liveTiles(hourly({ requests: 100, failed: 1, spend: 0.5 }, { requests: 110, failed: 3, spend: 1 }), daily);
  assert.equal(tiles.requests24h, 2640);
  assert.equal(tiles.requestsDelta?.label, '+10%');
  assert.equal(tiles.requestsDelta?.tone, 'flat');
  assert.ok(Math.abs(tiles.errorRate24h - 72 / 2640) < 1e-9);
  assert.equal(tiles.errorRateDelta?.tone, 'bad');
  assert.equal(tiles.spend24h, 24);
  assert.equal(tiles.sevenDayAverage, 10);
  assert.equal(tiles.spendDelta?.label, '+140%');
  // Today (UTC) holds the hours from 00:00 to 10:00.
  assert.equal(tiles.spendToday, 11);
  assert.equal(tiles.activeKeys24h, 7);
});

test('month to date adds today and forecasts the full month', () => {
  const daily = {
    start_date: '2026-09-01',
    end_date: '2026-10-13',
    days: [
      { date: '2026-09-15', total_spend: 300, request_count: 1, failed_requests: 0 },
      { date: '2026-10-05', total_spend: 40, request_count: 1, failed_requests: 0 },
    ],
  };
  const month = monthToDate(daily, 10, new Date('2026-10-11T00:00:00Z'));
  assert.equal(month.spend, 50);
  assert.equal(month.lastMonth, 300);
  assert.equal(month.forecast, 155);
});

const quietTiles: LiveTiles = {
  requests24h: 1000, requestsDelta: null, requestsSpark: [], errorRate24h: 0.01, previousErrorRate24h: 0.01,
  errorRateDelta: null, errorSpark: [], peakErrorHour: null, spend24h: 10, spendToday: 5, sevenDayAverage: 10,
  spendDelta: null, spendSpark: [], activeKeys24h: 3,
};

const failingProvider: LiveProvider = {
  provider: 'openai', request_count: 200, failed_requests: 40, top_failing_model: 'gpt-4o',
  top_failing_model_failures: 38, top_error: 'RateLimitError', first_error_at: '2026-10-14T09:12:00Z', last_error_at: '2026-10-14T09:40:00Z',
};

test('a quiet gateway needs no attention', () => {
  assert.deepEqual(attentionItems({ providers: [], liveProviders: [], tiles: quietTiles, budgets: [], formatMoney: money }), []);
});

test('attention items list critical problems first and link to filtered views', () => {
  const items = attentionItems({
    providers: [{ provider: 'anthropic', label: 'Anthropic', status: 'healthy', models: 2, healthy_models: 2 }],
    liveProviders: [failingProvider],
    tiles: { ...quietTiles, spend24h: 25 },
    budgets: [
      { kind: 'team', group_key: 'team-1', display_name: 'Search', spend: 92, max_budget: 100, budget_duration: '30d', budget_reset_at: null },
      { kind: 'api_key', group_key: 'hash-9f2c', display_name: null, spend: 101, max_budget: 100, budget_duration: null, budget_reset_at: '2026-11-01T00:00:00Z' },
    ],
    formatMoney: money,
  });
  assert.deepEqual(items.map((item) => [item.level, item.id, item.rank]), [
    ['critical', 'budget:api_key:hash-9f2c', 3],
    ['warning', 'provider-errors:openai', 2],
    ['warning', 'spend', 5],
    ['warning', 'budget:team:team-1', 2],
  ]);
  assert.equal(items[0].title, 'Key …9f2c used all of its budget');
  assert.match(items[1].detail, /gpt-4o failed 38 requests \(RateLimitError\) between 09:12 and 09:40 UTC/);
  assert.equal(items[1].action.to, '/usage/requests?window=24h&status=error&provider=openai');
  assert.equal(items[3].title, 'Search is at 92% of its monthly budget');
  assert.equal(items[3].action.to, '/usage?range=30d&team=team-1&team_label=Search');
});

test('a dismissed alert shows again only when it gets worse', () => {
  const at = (failed: number) => attentionItems({
    providers: [], liveProviders: [{ ...failingProvider, failed_requests: failed }], tiles: null, budgets: null, formatMoney: money,
  })[0];
  const dismissed = { [at(50).id]: { at: 0, rank: at(50).rank } };
  assert.equal(at(40).id, at(70).id);
  assert.equal(isDismissed(at(40), dismissed), true);
  assert.equal(isDismissed(at(50), dismissed), true);
  assert.equal(isDismissed(at(70), dismissed), false);
  assert.equal(at(10), undefined);
});

test('an error spike shows only when the rate at least doubles', () => {
  const spike = (previous: number) => attentionItems({
    providers: [], liveProviders: [], tiles: { ...quietTiles, errorRate24h: 0.08, previousErrorRate24h: previous }, budgets: [], formatMoney: money,
  });
  assert.equal(spike(0.03)[0]?.id, 'errors');
  assert.equal(spike(0.03)[0]?.rank, 1);
  assert.equal(spike(0.06).length, 0);
});

function memoryStorage(): DismissalStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
  };
}

test('dismissed alerts are kept per account and expire after seven days', () => {
  const storage = memoryStorage();
  const now = 10 * DISMISSAL_TTL_MS;
  writeDismissals(storage, 'acct-1', {
    old: { at: now - DISMISSAL_TTL_MS, rank: 1 },
    fresh: { at: now - 1000, rank: 2 },
  });
  assert.deepEqual(readDismissals(storage, 'acct-1', now), { fresh: { at: now - 1000, rank: 2 } });
  assert.deepEqual(readDismissals(storage, 'acct-2', now), {});
  writeDismissals(storage, 'acct-1', {});
  assert.equal(storage.data.size, 0);
});

test('dismissals keep only the newest fifty and ignore bad storage', () => {
  const storage = memoryStorage();
  const many = Object.fromEntries(Array.from({ length: 60 }, (_, index) => [`a${index}`, { at: 1_000 + index, rank: 1 }]));
  writeDismissals(storage, 'acct', many);
  const kept = readDismissals(storage, 'acct', 2_000);
  assert.equal(Object.keys(kept).length, 50);
  assert.equal(kept.a0, undefined);
  assert.deepEqual(kept.a59, { at: 1_059, rank: 1 });

  storage.setItem('deltallm.dashboard.dismissed.v2.acct', '{not json');
  assert.deepEqual(readDismissals(storage, 'acct', 2_000), {});
  assert.deepEqual(readDismissals(null, 'acct'), {});
  const throwing: DismissalStorage = {
    getItem: () => { throw new Error('blocked'); },
    setItem: () => { throw new Error('full'); },
    removeItem: () => undefined,
  };
  assert.deepEqual(readDismissals(throwing, 'acct'), {});
  assert.doesNotThrow(() => writeDismissals(throwing, 'acct', { a: { at: 1, rank: 1 } }));
});
