import assert from 'node:assert/strict';
import test from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import RequestLogTable from '../src/components/usage/RequestLogTable';
import { requestLogQuery, type RequestLog } from '../src/lib/api/usageReports';
import {
  callTypeLabel,
  logDurationMs,
  parseDetailInput,
  parseRequestDetails,
  parseRequestSort,
  readRequestLogColumns,
  requestDetailsKey,
  requestDetailValue,
  saveRequestLogColumns,
  sortAllowed,
  writeRequestDetails,
} from '../src/lib/requestLogs';

test('detail filters round-trip through the page URL and drop invalid values', () => {
  const params = new URLSearchParams(
    'model=gpt-4o&tag=eval&end_user=%20cust-1%20&endpoint=embedding&deployment=azure-east'
    + '&http_status=429&error_type=RateLimitError&min_cost=0.5&min_ms=5000',
  );
  const details = parseRequestDetails(params);
  assert.deepEqual(details, {
    tag: 'eval',
    endUser: 'cust-1',
    callType: 'embedding',
    deployment: 'azure-east',
    httpStatus: 429,
    errorType: 'RateLimitError',
    minSpend: 0.5,
    minDurationMs: 5000,
  });

  const invalid = parseRequestDetails(new URLSearchParams('http_status=42&min_cost=-1&min_ms=1.5&tag=%20'));
  assert.deepEqual(invalid, {});

  const cleared = writeRequestDetails(params, { ...details, tag: undefined, minSpend: undefined });
  assert.equal(cleared.get('tag'), null);
  assert.equal(cleared.get('min_cost'), null);
  assert.equal(cleared.get('model'), 'gpt-4o');
  assert.equal(cleared.get('end_user'), 'cust-1');
  assert.equal(requestDetailsKey({ minSpend: 1, tag: 'a' }), 'tag=a&minSpend=1');
});

test('the log query sends detail filters and a value sort to the API', () => {
  const query = requestLogQuery({
    view: 'platform',
    since: '2026-10-01T00:00:00.000Z',
    filters: [],
    details: { tag: 'eval', callType: 'embedding', httpStatus: 429, minSpend: 0.25, minDurationMs: 1500, endUser: 'u', deployment: 'd', errorType: 'e' },
    sort: 'cost',
    limit: 25,
  });
  assert.equal(query.get('tag'), 'eval');
  assert.equal(query.get('call_type'), 'embedding');
  assert.equal(query.get('http_status'), '429');
  assert.equal(query.get('min_spend'), '0.25');
  assert.equal(query.get('min_duration_ms'), '1500');
  assert.equal(query.get('end_user'), 'u');
  assert.equal(query.get('deployment'), 'd');
  assert.equal(query.get('error_type'), 'e');
  assert.equal(query.get('sort'), 'cost');

  const newest = requestLogQuery({ view: 'platform', filters: [], sort: 'newest', limit: 25 });
  assert.equal(newest.get('sort'), null);
  assert.equal(newest.get('tag'), null);
});

test('typed filter values and labels', () => {
  assert.equal(parseDetailInput('httpStatus', '503'), 503);
  assert.equal(parseDetailInput('httpStatus', '5xx'), undefined);
  assert.equal(parseDetailInput('minSpend', '$1.50'), 1.5);
  assert.equal(parseDetailInput('minDurationMs', '2.5'), 2500);
  assert.equal(parseDetailInput('minDurationMs', '10s'), 10_000);
  assert.equal(parseDetailInput('tag', '  '), undefined);
  assert.equal(callTypeLabel('embedding'), 'Embeddings');
  assert.equal(callTypeLabel('custom_call'), 'custom_call');
  assert.equal(requestDetailValue('minDurationMs', { minDurationMs: 5000 }), '≥ 5.00 s');
  assert.equal(requestDetailValue('callType', { callType: 'audio_speech' }), 'Speech');
  assert.equal(parseRequestSort('duration'), 'duration');
  assert.equal(parseRequestSort('tokens'), 'newest');
});

test('value sorts are allowed only for windows of 31 days or less', () => {
  const now = Date.parse('2026-10-28T00:00:00Z');
  assert.equal(sortAllowed({ since: '2026-09-28T00:00:00Z' }, now), true);
  assert.equal(sortAllowed({ since: '2026-08-01T00:00:00Z', until: '2026-10-01T00:00:00Z' }, now), false);
});

test('column choices are saved per browser and keep table order', () => {
  const store = new Map<string, string>();
  const storage = { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => void store.set(key, value) };
  saveRequestLogColumns(['request_id', 'tags', 'bogus' as never], storage);
  assert.deepEqual(readRequestLogColumns(storage), ['tags', 'request_id']);
  assert.deepEqual(readRequestLogColumns({ getItem: () => '{bad json' }), []);
});

const LOG: RequestLog = {
  id: 'row-1',
  request_id: 'req-abc',
  call_type: 'embedding',
  model: 'text-embedding-3-small',
  deployment_model: 'azure-east',
  api_key: 'hash',
  spend: 0.002,
  total_tokens: 30,
  prompt_tokens: 30,
  completion_tokens: 0,
  prompt_tokens_cached: 12,
  start_time: '2026-10-27T10:00:00Z',
  end_time: '2026-10-27T10:00:02Z',
  duration_ms: 2000,
  cache_hit: false,
  request_tags: ['eval', 'batch'],
  end_user: 'customer-1',
  user_email: 'mehdi@example.com',
  error_type: null,
};

test('optional columns render in table order with their values', () => {
  const html = renderToStaticMarkup(
    <RequestLogTable
      logs={[LOG]}
      loading={false}
      refreshing={false}
      selectedId={null}
      columns={['request_id', 'endpoint', 'tags', 'cached', 'person']}
      onSelect={() => undefined}
    />,
  );
  const order = ['Person', 'Endpoint', 'Tags', 'Cached tokens', 'Request ID'].map((label) => html.indexOf(`>${label}</th>`));
  assert.ok(order.every((index) => index > 0));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  for (const text of ['Embeddings', 'eval, batch', 'req-abc', 'mehdi@example.com', '>12<']) assert.ok(html.includes(text), text);
  assert.equal(logDurationMs(LOG), 2000);

  const plain = renderToStaticMarkup(
    <RequestLogTable logs={[LOG]} loading={false} refreshing={false} selectedId={null} onSelect={() => undefined} />,
  );
  assert.ok(!plain.includes('Request ID</th>'));
});
