import assert from 'node:assert/strict';
import test from 'node:test';

import type { AuditEvent } from '../src/lib/api/audit';
import {
  actionCategory,
  actionLabel,
  actorDisplay,
  actorRole,
  auditQuery,
  clientName,
  durationLabel,
  eventSummary,
  parseAuditFilters,
  parseAuditWindow,
  readAuditColumns,
  resourceLink,
  saveAuditColumns,
  stateDisplay,
  statusLabel,
  utcClock,
  writeAuditFilters,
} from '../src/lib/auditEvents';

function event(overrides: Partial<AuditEvent>): AuditEvent {
  return {
    event_id: 'evt-1',
    occurred_at: '2026-10-14T10:21:44Z',
    organization_id: null,
    actor_type: null,
    actor_id: null,
    api_key: null,
    action: 'CHAT_COMPLETION_REQUEST',
    resource_type: null,
    resource_id: null,
    request_id: null,
    correlation_id: null,
    ip: null,
    user_agent: null,
    status: 'success',
    latency_ms: null,
    input_tokens: null,
    output_tokens: null,
    error_type: null,
    error_code: null,
    metadata: null,
    content_stored: false,
    ...overrides,
  };
}

const text = (parts: ReturnType<typeof eventSummary>) => parts.map((part) => (typeof part === 'string' ? part : part.bold)).join('');

test('categories follow the server prefixes, with request actions outside the enum as API requests', () => {
  assert.equal(actionCategory('ADMIN_MODEL_UPDATE'), 'admin');
  assert.equal(actionCategory('MANAGED_ASSET_ACCESS_UPDATE'), 'admin');
  assert.equal(actionCategory('AUTH_INTERNAL_LOGIN'), 'auth');
  assert.equal(actionCategory('MCP_TOOL_CALL'), 'mcp');
  assert.equal(actionCategory('GLOBAL_SPEND_KEYS_READ'), 'spend');
  assert.equal(actionCategory('EXTERNAL_AUTH_DENIAL'), 'system');
  assert.equal(actionCategory('BATCH_WEBHOOK_FAILED'), 'system');
  assert.equal(actionCategory('CHAT_COMPLETION_REQUEST'), 'api');
  assert.equal(actionCategory('BATCH_CREATE_REQUEST'), 'api');
});

test('actions read as plain words', () => {
  assert.equal(actionLabel('ADMIN_MODEL_UPDATE'), 'Model updated');
  assert.equal(actionLabel('ADMIN_KEY_REVOKE'), 'Key revoked');
  assert.equal(actionLabel('ADMIN_ROUTE_GROUP_BINDING_UPSERT'), 'Route group binding saved');
  assert.equal(actionLabel('ADMIN_RBAC_ORG_MEMBERSHIP_UPSERT'), 'Organization member added or changed');
  assert.equal(actionLabel('CHAT_COMPLETION_REQUEST'), 'Chat completion');
  assert.equal(actionLabel('SYSTEM_BUDGET_NOTIFICATION_ENQUEUE'), 'Budget alert queued');
  assert.equal(actionLabel('ADMIN_MCP_SERVER_CREATE'), 'MCP server created');
  assert.equal(actionLabel('SOMETHING_NEW'), 'Something new');
});

test('status, duration and time', () => {
  assert.equal(statusLabel(event({ status: 'success' })), 'OK');
  assert.equal(statusLabel(event({ status: 'error', error_code: '504' })), '504');
  assert.equal(statusLabel(event({ status: 'error', error_code: 'invalid_credentials' })), 'Failed');
  assert.equal(durationLabel(41), '41 ms');
  assert.equal(durationLabel(30_000), '30.0 s');
  assert.equal(durationLabel(null), '—');
  const now = new Date('2026-10-14T12:00:00Z');
  assert.equal(utcClock('2026-10-14T10:21:44Z', now), '10:21:44');
  assert.equal(utcClock('2026-10-13T17:40:02Z', now), 'Oct 13 17:40:02');
});

test('actors show names, and a click filters by ID or by actor type', () => {
  const person = actorDisplay(event({ actor_type: 'platform_account', actor_id: 'acc-1', labels: { actor: 'mehdi@example.com', key_name: null, team_id: null, team: null, organization: null, resource: null } }));
  assert.deepEqual([person.name, person.kind], ['mehdi@example.com', 'person']);
  assert.deepEqual(person.filter, { value: 'acc-1', label: 'mehdi@example.com' });

  const hidden = actorDisplay(event({ actor_type: 'platform_account', actor_id: 'acc-1234567890' }));
  assert.equal(hidden.name, 'Account acc-1234…');

  const key = actorDisplay(event({ actor_type: 'api_key', actor_id: 'user-1', api_key: 'hash41ab', labels: { actor: 'search-indexer', key_name: 'search-indexer', team_id: null, team: null, organization: null, resource: null } }));
  assert.deepEqual([key.name, key.kind], ['search-indexer', 'API key …41ab']);
  assert.deepEqual(key.filter, { value: 'hash41ab', label: 'search-indexer' });

  const master = actorDisplay(event({ actor_type: 'master_key' }));
  assert.deepEqual(master.filter, { value: 'master_key', label: 'Master key', type: true });
});

test('summaries are one sentence about who did what', () => {
  assert.equal(
    text(eventSummary(event({
      action: 'ADMIN_MODEL_UPDATE',
      actor_type: 'platform_account',
      actor_id: 'acc-1',
      resource_type: 'model_deployment',
      resource_id: 'dep-1',
      metadata: { changed_fields: ['rpm_limit', 'tpm_limit'] },
      labels: { actor: 'mehdi@example.com', key_name: null, team_id: null, team: null, organization: null, resource: 'gpt-4o' },
    }))),
    'mehdi@example.com changed 2 settings on model gpt-4o.',
  );
  assert.equal(
    text(eventSummary(event({
      action: 'ADMIN_KEY_REVOKE',
      actor_type: 'platform_account',
      organization_id: 'org-1',
      resource_type: 'api_key',
      resource_id: 'hash0d77',
      labels: { actor: 'mehdi@example.com', key_name: null, team_id: null, team: null, organization: 'Globex', resource: 'batch-runner' },
    }))),
    'mehdi@example.com revoked API key batch-runner in Globex.',
  );
  assert.equal(
    text(eventSummary(event({
      action: 'CHAT_COMPLETION_REQUEST',
      actor_type: 'api_key',
      api_key: 'hash41ab',
      status: 'error',
      error_code: '504',
      resource_type: 'model',
      resource_id: 'glm-4.6',
      labels: { actor: 'search-indexer', key_name: 'search-indexer', team_id: null, team: null, organization: null, resource: null },
    }))),
    'API key search-indexer called glm-4.6 and it failed (504).',
  );
  assert.equal(
    text(eventSummary(event({ action: 'AUTH_INTERNAL_LOGIN', actor_type: 'platform_account', status: 'error', ip: '10.0.4.17', labels: { actor: 'carol@acme.com', key_name: null, team_id: null, team: null, organization: null, resource: null } }))),
    'carol@acme.com failed to sign in from 10.0.4.17.',
  );
  assert.equal(
    text(eventSummary(event({ action: 'MCP_TOOL_CALL', actor_type: 'api_key', api_key: 'h9f2c', resource_type: 'mcp_tool', resource_id: 'search_issues', metadata: { server_key: 'github' } }))),
    'API key Key …9f2c called tool search_issues on MCP server github.',
  );
});

test('client names come from the user agent', () => {
  assert.equal(clientName('OpenAI/Python 1.51.0'), 'openai-python 1.51');
  assert.equal(clientName('curl/8.5.0'), 'curl 8.5');
  assert.equal(
    clientName('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'),
    'Chrome on macOS',
  );
  assert.equal(clientName('Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0'), 'Firefox on Windows');
  assert.equal(clientName('mcp-client/0.9.2'), 'mcp-client 0.9');
  assert.equal(clientName(null), null);
});

test('roles, states and links', () => {
  assert.equal(actorRole({ platform_role: 'platform_admin' }, 'Acme'), 'Platform admin');
  assert.equal(actorRole({ platform_role: 'org_user', organization_role: 'org_member' }, 'Acme Corp'), 'Org member in Acme Corp');
  assert.equal(actorRole({ state: 'missing' }, null), null);
  assert.deepEqual(stateDisplay('api_key', { state: 'missing' }), { label: 'Revoked or deleted', tone: 'muted' });
  assert.deepEqual(stateDisplay('batch', { state: 'cancelled', progress: 61 }), { label: 'Cancelled at 61%', tone: 'warn' });
  assert.equal(resourceLink('model_deployment', 'dep 1', { models: true }), '/models/dep%201');
  assert.equal(resourceLink('team', 'team-1', { teams: false }), null);
  assert.equal(resourceLink('session', 'x', { models: true }), null);
});

test('filters round-trip through the URL and become the server query', () => {
  const params = writeAuditFilters(new URLSearchParams('window=7d&event=evt-9'), {
    category: 'admin',
    status: 'error',
    actor: { value: 'acc-1', label: 'mehdi@example.com' },
    organization: { value: 'org-1', label: 'Acme Corp' },
    resource: { type: 'model_deployment', id: 'dep-1', label: 'gpt-4o' },
    ip: '10.0.1.4',
    search: 'req_8f3a',
  });
  assert.equal(params.get('window'), '7d');
  assert.equal(params.get('event'), null, 'a new filter closes the panel');
  const filters = parseAuditFilters(params);
  assert.deepEqual(filters.actor, { value: 'acc-1', label: 'mehdi@example.com' });
  assert.deepEqual(filters.resource, { type: 'model_deployment', id: 'dep-1', label: 'gpt-4o' });

  const now = new Date('2026-10-14T12:00:30Z');
  const query = auditQuery(filters, parseAuditWindow(params.get('window')), {}, now);
  assert.deepEqual(query, {
    since: '2026-10-07T12:00:00.000Z',
    category: 'admin',
    status: 'error',
    actor: 'acc-1',
    organization_id: 'org-1',
    resource_type: 'model_deployment',
    resource_id: 'dep-1',
    ip: '10.0.1.4',
    search: 'req_8f3a',
  });

  const master = parseAuditFilters(new URLSearchParams('actor_type=master_key&request_id=req-1'));
  assert.deepEqual(auditQuery(master, '1h', {}, now), {
    since: '2026-10-14T11:00:00.000Z',
    actor_type: 'master_key',
    request_id: 'req-1',
  });

  assert.deepEqual(
    auditQuery({}, 'custom', { from: '2026-10-01T08:00', to: '2026-10-02T08:00' }, now),
    { since: '2026-10-01T08:00:00.000Z', until: '2026-10-02T08:00:00.000Z' },
  );
  assert.equal(parseAuditWindow('bogus'), '24h');
  assert.equal(parseAuditFilters(new URLSearchParams('category=bogus')).category, undefined);
});

test('optional columns are saved in the browser and survive broken storage', () => {
  const store = new Map<string, string>();
  const storage = { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => void store.set(key, value) };
  assert.deepEqual(readAuditColumns(storage), []);
  saveAuditColumns(['client', 'team'], storage);
  assert.deepEqual(readAuditColumns(storage), ['team', 'client']);
  store.set('deltallm.audit.columns', 'not json');
  assert.deepEqual(readAuditColumns(storage), []);
  const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  assert.deepEqual(readAuditColumns(broken), []);
  assert.doesNotThrow(() => saveAuditColumns(['ip'], broken));
});

test('API requests on a resource read as a verb', () => {
  assert.equal(
    text(eventSummary(event({ action: 'BATCH_CREATE_REQUEST', actor_type: 'api_key', api_key: 'h0d77', resource_type: 'batch', resource_id: 'batch_6e0f11c9', labels: { actor: 'batch-runner', key_name: 'batch-runner', team_id: null, team: null, organization: null, resource: null } }))),
    'API key batch-runner created batch batch_6e0f11c9.',
  );
});
