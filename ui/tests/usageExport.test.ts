import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EXPORT_ROW_LIMIT,
  requestLogExportQuery,
  usageReportExportQuery,
} from '../src/lib/api/usageExports';
import { parseRequestFlags, parseUsageTab } from '../src/lib/usageParams';

test('report export query keeps the scope and adds the export options', () => {
  const query = usageReportExportQuery(
    {
      view: 'team',
      startDate: '2026-10-01',
      endDate: '2026-10-07',
      filters: [
        { dimension: 'model', value: 'gpt-4o' },
        { dimension: 'team', value: '', unassigned: true },
      ],
    },
    { groupBy: 'api_key', format: 'csv', interval: 'day' },
  );

  assert.equal(query.get('view'), 'team');
  assert.equal(query.get('start_date'), '2026-10-01');
  assert.equal(query.get('end_date'), '2026-10-07');
  assert.equal(query.get('model'), 'gpt-4o');
  assert.deepEqual(query.getAll('unassigned'), ['team']);
  assert.equal(query.get('group_by'), 'api_key');
  assert.equal(query.get('format'), 'csv');
  assert.equal(query.get('interval'), 'day');
  assert.equal(query.get('limit'), String(EXPORT_ROW_LIMIT));
});

test('report export without an interval sends one row per group', () => {
  const query = usageReportExportQuery({ view: 'platform', filters: [] }, { groupBy: 'model', format: 'json' });

  assert.equal(query.get('view'), null);
  assert.equal(query.get('interval'), null);
  assert.equal(query.get('format'), 'json');
});

test('request log export query uses the list filters without page options', () => {
  const query = requestLogExportQuery(
    {
      view: 'organization',
      since: '2026-10-20T00:00:00.000Z',
      until: '2026-10-21T00:00:00.000Z',
      filters: [{ dimension: 'team', value: 'team-1' }],
      status: 'error',
      cacheHit: false,
      requestId: 'req-1',
      details: { tag: 'batch', minDurationMs: 500 },
    },
    'jsonl',
  );

  assert.equal(query.get('view'), 'organization');
  assert.equal(query.get('since'), '2026-10-20T00:00:00.000Z');
  assert.equal(query.get('until'), '2026-10-21T00:00:00.000Z');
  assert.equal(query.get('team_id'), 'team-1');
  assert.equal(query.get('status'), 'error');
  assert.equal(query.get('cache_hit'), 'false');
  assert.equal(query.get('request_id'), 'req-1');
  assert.equal(query.get('tag'), 'batch');
  assert.equal(query.get('min_duration_ms'), '500');
  assert.equal(query.get('sort'), null);
  assert.equal(query.get('format'), 'jsonl');
  assert.equal(query.get('limit'), String(EXPORT_ROW_LIMIT));
  assert.equal(query.get('pagination_mode'), null);
  assert.equal(query.get('cursor'), null);
});

test('request flags and the overview tab come from the URL', () => {
  assert.deepEqual(parseRequestFlags(new URLSearchParams('status=error&cache=miss')), { status: 'error', cacheHit: false });
  assert.deepEqual(parseRequestFlags(new URLSearchParams('status=maybe&cache=x')), { status: undefined, cacheHit: undefined });
  assert.equal(parseUsageTab(new URLSearchParams('tab=team'), ['model', 'team']), 'team');
  assert.equal(parseUsageTab(new URLSearchParams('tab=organization'), ['model', 'team']), 'model');
  assert.equal(parseUsageTab(new URLSearchParams(''), ['model']), 'model');
});
