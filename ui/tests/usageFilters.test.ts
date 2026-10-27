import assert from 'node:assert/strict';
import test from 'node:test';

import type { SpendCapabilities } from '../src/lib/api';
import { requestInit, requestLogQuery, usageScopeQuery } from '../src/lib/api/usageReports';
import { BACKGROUND_REQUEST_HEADER } from '../src/lib/sessionActivity';
import { logDurationMs, logKeyName, logStatusCode, parseLogTime, relativeTime } from '../src/lib/requestLogs';
import {
  allowedUsageDimensions,
  chipDimensions,
  filterLabel,
  groupLabel,
  parseRequestWindow,
  parseRequestWindowSelection,
  parseUsageFilters,
  requestTimeRange,
  requestWindowSince,
  validateRequestRange,
  resolveUsageView,
  usageFiltersKey,
  withoutUsageFilter,
  withUsageFilter,
  writeUsageFilters,
} from '../src/lib/usageFilters';

const teamCapabilities: SpendCapabilities = {
  visibility_level: 'team',
  active_view: 'team',
  default_view: 'team',
  available_views: ['team', 'self'],
  self_scoped: false,
  allowed_dimensions: ['team', 'user'],
  request_logs: false,
  user_identity_labels: false,
};

test('usage view falls back to the default view the account may see', () => {
  assert.equal(resolveUsageView(undefined, 'self'), 'platform');
  assert.equal(resolveUsageView(teamCapabilities, 'self'), 'self');
  assert.equal(resolveUsageView(teamCapabilities, 'organization'), 'team');
});

test('team and self views can filter only by their allowed dimensions', () => {
  assert.deepEqual(allowedUsageDimensions('team', teamCapabilities), ['model', 'provider', 'team', 'user']);
  assert.deepEqual(allowedUsageDimensions('platform', teamCapabilities), [
    'model', 'provider', 'organization', 'team', 'user', 'api_key',
  ]);
});

test('usage filters survive a round trip through the URL', () => {
  const params = writeUsageFilters(new URLSearchParams('range=30d&model=old'), [
    { dimension: 'team', value: 'team-1', label: 'Core team' },
    { dimension: 'api_key', value: '', unassigned: true },
  ]);
  assert.equal(params.get('range'), '30d');
  assert.equal(params.get('model'), null);
  assert.equal(params.get('team_label'), 'Core team');
  assert.deepEqual(parseUsageFilters(params), [
    { dimension: 'team', value: 'team-1', label: 'Core team' },
    { dimension: 'api_key', value: '', unassigned: true },
  ]);
});

test('a dimension holds at most one filter', () => {
  const filters = withUsageFilter([{ dimension: 'model', value: 'a' }], { dimension: 'model', value: 'b' });
  assert.deepEqual(filters, [{ dimension: 'model', value: 'b' }]);
  assert.deepEqual(withoutUsageFilter(filters, 'model'), []);
  assert.equal(
    usageFiltersKey([{ dimension: 'team', value: 't' }, { dimension: 'model', value: '', unassigned: true }]),
    'model=∅&team=t',
  );
});

test('groups without a name get a readable label', () => {
  assert.equal(groupLabel('api_key', { group_key: 'sk-hash-abcd' }), 'Key …abcd');
  assert.equal(groupLabel('team', { group_key: null, is_unassigned: true }), 'No team');
  assert.equal(groupLabel('model', { group_key: 'gpt-4o', display_name: 'GPT-4o' }), 'GPT-4o');
  assert.equal(filterLabel({ dimension: 'organization', value: '', unassigned: true }), 'No organization');
});

test('request windows start on a whole minute', () => {
  assert.equal(parseRequestWindow('7d'), '7d');
  assert.equal(parseRequestWindow('2y'), '24h');
  assert.equal(
    requestWindowSince('1h', new Date('2026-10-14T10:42:37.512Z')),
    '2026-10-14T09:42:00.000Z',
  );
});

test('report queries send filters with the names each endpoint expects', () => {
  const filters = [
    { dimension: 'team' as const, value: 'team-1' },
    { dimension: 'user' as const, value: '', unassigned: true },
  ];
  assert.equal(
    usageScopeQuery({ view: 'organization', startDate: '2026-10-01', endDate: '2026-10-14', filters }).toString(),
    'view=organization&start_date=2026-10-01&end_date=2026-10-14&team=team-1&unassigned=user',
  );
  const logs = requestLogQuery({ view: 'platform', since: '2026-10-14T09:00:00.000Z', filters, status: 'error', cacheHit: false, limit: 25 });
  assert.equal(logs.get('view'), null);
  assert.equal(logs.get('team_id'), 'team-1');
  assert.equal(logs.get('unassigned'), 'user');
  assert.equal(logs.get('status'), 'error');
  assert.equal(logs.get('cache_hit'), 'false');
  assert.equal(logs.get('pagination_mode'), 'cursor');
});

test('request log helpers read times without a zone as UTC', () => {
  assert.equal(parseLogTime('2026-10-14T10:00:00')?.toISOString(), '2026-10-14T10:00:00.000Z');
  assert.equal(relativeTime('2026-10-14T10:00:00Z', Date.parse('2026-10-14T10:05:00Z')), '5 min ago');
  const log = {
    id: '1', request_id: 'r', call_type: 'completion', model: 'm', api_key: 'hash-wxyz', spend: 0,
    total_tokens: 0, prompt_tokens: 0, completion_tokens: 0, cache_hit: false,
    start_time: '2026-10-14T10:00:00Z', end_time: '2026-10-14T10:00:01.250Z', status: 'error',
  };
  assert.equal(logDurationMs(log), 1250);
  assert.equal(logStatusCode(log), 'Error');
  assert.equal(logStatusCode({ ...log, http_status_code: 429 }), '429');
  assert.equal(logKeyName(log), 'Key …wxyz');
});

test('auto refresh report requests do not count as session activity', () => {
  assert.deepEqual(requestInit({}), { signal: undefined });
  const background = new Headers(requestInit({ background: true }).headers);
  assert.equal(background.get(BACKGROUND_REQUEST_HEADER), '1');
  assert.equal(background.get('Cache-Control'), null);
  const forced = new Headers(requestInit({ force: true }).headers);
  assert.equal(forced.get('Cache-Control'), 'no-cache');
  assert.equal(forced.get(BACKGROUND_REQUEST_HEADER), null);
});

test('an active filter always has a chip, even outside the usual set', () => {
  const allowed = ['model', 'provider', 'organization', 'team', 'user', 'api_key'] as const;
  assert.deepEqual(chipDimensions(['model', 'team'], [{ dimension: 'user', value: 'u1' }], allowed), ['model', 'team', 'user']);
  assert.deepEqual(chipDimensions(['model', 'api_key'], [], ['model', 'provider']), ['model']);
});

test('custom request windows send since and an exclusive until', () => {
  const now = new Date('2026-10-13T09:15:42Z');
  assert.equal(parseRequestWindowSelection('custom'), 'custom');
  assert.equal(parseRequestWindowSelection('7d'), '7d');
  assert.equal(parseRequestWindowSelection('nope'), '24h');

  assert.deepEqual(requestTimeRange('1h', {}, now), { since: '2026-10-13T08:15:00.000Z' });
  assert.deepEqual(requestTimeRange('custom', { from: '2026-10-01T08:00', to: '2026-10-02T17:30' }, now), {
    since: '2026-10-01T08:00:00.000Z',
    until: '2026-10-02T17:30:00.000Z',
  });
  assert.deepEqual(requestTimeRange('custom', { from: '2026-10-01T08:00' }, now), { since: '2026-10-01T08:00:00.000Z' });
  // A bad custom window falls back to the last 24 hours.
  assert.deepEqual(requestTimeRange('custom', { from: '2026-10-02T08:00', to: '2026-10-01T08:00' }, now), {
    since: '2026-10-12T09:15:00.000Z',
  });

  assert.equal(validateRequestRange(null, null), 'Choose a start time.');
  assert.equal(validateRequestRange('2026-10-01T08:00', 'tomorrow'), 'Enter a valid end time.');
  assert.equal(validateRequestRange('2026-10-01T08:00', '2026-10-01T08:00'), 'The start must be before the end.');
  assert.equal(validateRequestRange('2026-10-01T08:00', null), null);

  const query = requestLogQuery({
    view: 'platform',
    since: '2026-10-01T08:00:00.000Z',
    until: '2026-10-02T17:30:00.000Z',
    filters: [],
    limit: 25,
  });
  assert.equal(query.get('until'), '2026-10-02T17:30:00.000Z');
});
