import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { AuthProvider } from '../src/lib/auth';
import AuditLogs from '../src/pages/AuditLogs';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const labels = (overrides: Record<string, string | null>) => ({
  actor: null, key_name: null, team_id: null, team: null, organization: null, resource: null, ...overrides,
});

const base = {
  organization_id: null, actor_type: null, actor_id: null, api_key: null, resource_type: null, resource_id: null,
  request_id: null, correlation_id: null, ip: null, user_agent: null, status: 'success', latency_ms: null,
  input_tokens: null, output_tokens: null, error_type: null, error_code: null, metadata: {}, content_stored: false,
};

const now = Date.now();
const at = (minutes: number) => new Date(now - minutes * 60_000).toISOString();

const EVENTS = [
  {
    ...base,
    event_id: 'evt-1',
    occurred_at: at(1),
    action: 'ADMIN_MODEL_UPDATE',
    actor_type: 'platform_account',
    actor_id: 'acc-1',
    resource_type: 'model_deployment',
    resource_id: 'dep-1',
    ip: '10.0.1.4',
    user_agent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/129.0 Safari/537.36',
    latency_ms: 41,
    metadata: { changed_fields: ['rpm_limit', 'tpm_limit'] },
    labels: labels({ actor: 'mehdi@example.com', resource: 'gpt-4o' }),
  },
  {
    ...base,
    event_id: 'evt-2',
    occurred_at: at(3),
    action: 'CHAT_COMPLETION_REQUEST',
    organization_id: 'org-1',
    actor_type: 'api_key',
    actor_id: 'hash41ab',
    api_key: 'hash41ab',
    resource_type: 'model',
    resource_id: 'glm-4.6',
    request_id: 'req-2',
    correlation_id: 'req-2',
    status: 'error',
    error_code: '504',
    error_type: 'Timeout',
    latency_ms: 30_000,
    input_tokens: 1240,
    output_tokens: 0,
    content_stored: true,
    labels: labels({ actor: 'search-indexer', key_name: 'search-indexer', team_id: 'team-1', team: 'Search', organization: 'Acme Corp' }),
  },
  {
    ...base,
    event_id: 'evt-3',
    occurred_at: at(6),
    action: 'AUTH_INTERNAL_LOGIN',
    actor_type: 'master_key',
    resource_type: 'session',
  },
];

function Location() {
  const location = useLocation();
  return <output id="location">{`${location.pathname}${location.search}`}</output>;
}

async function withPage(entry: string, run: (ctx: { requests: URL[] }) => Promise<void>) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { configurable: true, value() {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { configurable: true, value() {} });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    KeyboardEvent: dom.window.KeyboardEvent,
    localStorage: dom.window.localStorage,
    IS_REACT_ACT_ENVIRONMENT: true,
    requestAnimationFrame: (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0),
  };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const previousFetch = globalThis.fetch;
  const requests: URL[] = [];
  globalThis.fetch = async (input) => {
    const url = new URL(String(input), 'http://localhost');
    requests.push(url);
    if (url.pathname === '/auth/me') {
      return json({ authenticated: true, auth_mode: 'session', account_id: 'admin', role: 'platform_admin' });
    }
    if (url.pathname === '/ui/api/audit/events') {
      const cursor = url.searchParams.get('cursor');
      return json({
        events: cursor ? [{ ...base, event_id: 'evt-4', occurred_at: at(9), action: 'ADMIN_TEAM_CREATE' }] : EVENTS,
        pagination: { limit: 50, has_more: !cursor, next_cursor: cursor ? null : 'cursor-1' },
      });
    }
    const detail = url.pathname.match(/^\/ui\/api\/audit\/events\/([^/]+)$/);
    if (detail) {
      const event = EVENTS.find((item) => item.event_id === detail[1]);
      return json({
        ...event,
        payload_summary: { count: event?.content_stored ? 2 : 0, redacted: false, kinds: [] },
        context: {
          labels: event?.labels ?? labels({}),
          actor: event?.actor_type === 'platform_account' ? { email: 'mehdi@example.com', platform_role: 'platform_admin', organization_role: null, is_active: true } : null,
          key: event?.api_key ? { name: 'search-indexer', tail: '41ab', team_id: 'team-1', team: 'Search', owner: null, state: 'active' } : null,
          resource: event?.resource_type === 'model_deployment' ? { label: 'gpt-4o', state: 'active' } : null,
        },
      });
    }
    if (url.pathname.endsWith('/related')) {
      return json({
        request: url.pathname.includes('evt-2')
          ? [
              { event_id: 'evt-0', occurred_at: at(4), action: 'PROMPT_RESOLUTION_REQUEST', status: 'success' },
              { event_id: 'evt-2', occurred_at: at(3), action: 'CHAT_COMPLETION_REQUEST', status: 'error' },
            ]
          : [],
        resource: [],
        actor_signins: [],
      });
    }
    if (url.pathname.endsWith('/payloads')) {
      return json({ payloads: [{ payload_id: 'p1', event_id: 'evt-2', kind: 'request', content_json: { model: 'glm-4.6' }, redacted: false }] });
    }
    return json({});
  };
  const root = createRoot(document.getElementById('root')!);
  try {
    await act(async () => root.render(
      <MemoryRouter initialEntries={[entry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider><Location /><AuditLogs /></AuthProvider>
      </MemoryRouter>,
    ));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
    await run({ requests });
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = previousFetch;
    dom.window.close();
    for (const key of Object.keys(values)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
}

const location = () => document.getElementById('location')?.textContent ?? '';
const rows = () => [...document.querySelectorAll('tbody tr')];
const settle = () => act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
const button = (name: string) => [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === name) as HTMLButtonElement;

test('the list loads one page with names and no total count, inside a 24 hour window', async () => {
  await withPage('/audit', async ({ requests }) => {
    const list = requests.find((url) => url.pathname === '/ui/api/audit/events');
    assert.equal(list?.searchParams.get('count'), 'false');
    assert.equal(list?.searchParams.get('enrich'), 'true');
    assert.equal(list?.searchParams.get('limit'), '50');
    const since = Date.parse(list?.searchParams.get('since') ?? '');
    assert.ok(Math.abs(now - since - 24 * 3_600_000) < 120_000);

    assert.equal(rows().length, 3);
    const first = rows()[0].textContent ?? '';
    assert.ok(first.includes('Model updated'));
    assert.ok(first.includes('mehdi@example.com'));
    assert.ok(first.includes('Model gpt-4o'));
    assert.ok(first.includes('Platform'));
    const second = rows()[1].textContent ?? '';
    assert.ok(second.includes('504'));
    assert.ok(second.includes('search-indexer'));
    assert.ok(second.includes('API key …41ab'));
    assert.ok(second.includes('Acme Corp'));
    assert.ok(second.includes('30.0 s'));
    assert.ok(rows()[2].textContent?.includes('Master key'));
    // Nothing for the panel, the action list or bodies loads before it is opened.
    assert.equal(requests.some((url) => url.pathname.startsWith('/ui/api/audit/events/')), false);
    assert.equal(requests.some((url) => url.pathname === '/ui/api/audit/actions'), false);

    await act(async () => button('Load 50 more').click());
    await settle();
    assert.equal(rows().length, 4);
    assert.equal(requests.filter((url) => url.searchParams.get('cursor') === 'cursor-1').length, 1);
    assert.ok(document.body.textContent?.includes('4 events loaded · last 24 h'));
  });
});

test('a request ID from Usage searches the last 30 days', async () => {
  await withPage('/audit?request_id=req-2', async ({ requests }) => {
    const list = requests.find((url) => url.pathname === '/ui/api/audit/events');
    assert.equal(list?.searchParams.get('request_id'), 'req-2');
    const since = Date.parse(list?.searchParams.get('since') ?? '');
    assert.ok(Math.abs(now - since - 30 * 24 * 3_600_000) < 120_000);
    assert.ok(document.body.textContent?.includes('Request is req-2'));
  });
});

test('a click on a name filters, and the filter lands in the URL and the query', async () => {
  await withPage('/audit?window=7d', async ({ requests }) => {
    const actor = [...rows()[1].querySelectorAll('button')].find((item) => item.textContent === 'search-indexer') as HTMLButtonElement;
    await act(async () => actor.click());
    await settle();
    assert.equal(location(), '/audit?window=7d&actor=hash41ab&actor_label=search-indexer');
    const list = requests.filter((url) => url.pathname === '/ui/api/audit/events').at(-1);
    assert.equal(list?.searchParams.get('actor'), 'hash41ab');
    assert.ok(document.body.textContent?.includes('Actor is search-indexer'));
    // The click filtered; it did not open the panel.
    assert.equal(document.querySelector('aside[aria-label="Event detail"]'), null);
  });
});

test('the side panel loads its lookups on open, steps with the arrow keys, and loads bodies on click', async () => {
  await withPage('/audit', async ({ requests }) => {
    await act(async () => (rows()[1] as HTMLElement).click());
    await settle();
    assert.ok(location().includes('event=evt-2'));
    const panel = document.querySelector('aside[aria-label="Event detail"]');
    assert.ok(panel);
    const detail = requests.find((url) => url.pathname === '/ui/api/audit/events/evt-2');
    assert.equal(detail?.searchParams.get('include_payloads'), 'false');
    assert.equal(detail?.searchParams.get('context'), 'true');
    const textContent = panel.textContent ?? '';
    assert.ok(textContent.includes('API key search-indexer called glm-4.6 and it failed (504).'));
    assert.ok(textContent.includes('Timeout'));
    assert.ok(textContent.includes('Bodies stored'));
    assert.ok(textContent.includes('Search'));
    assert.ok(textContent.includes('Prompt resolved'));
    assert.ok(textContent.includes('(this event)'));
    assert.ok(textContent.includes('Open in Usage requests'));
    assert.equal(requests.some((url) => url.pathname.endsWith('/payloads')), false);

    await act(async () => button('Show payloads').click());
    await settle();
    assert.ok(document.querySelector('aside')?.textContent?.includes('"model": "glm-4.6"'));

    await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));
    await settle();
    assert.ok(location().includes('event=evt-3'));
    await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true })));
    await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true })));
    await settle();
    assert.ok(location().includes('event=evt-1'));
    const adminPanel = document.querySelector('aside')?.textContent ?? '';
    assert.ok(adminPanel.includes('mehdi@example.com changed 2 settings on model gpt-4o.'));
    assert.ok(adminPanel.includes('Platform admin (current)'));
    assert.ok(adminPanel.includes('rpm_limit'));
    assert.ok(adminPanel.includes('Values were not captured for this change.'));
    assert.ok(adminPanel.includes('Chrome on macOS'));
    assert.ok(adminPanel.includes('Active'));

    await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(document.querySelector('aside[aria-label="Event detail"]'), null);
    assert.equal(location(), '/audit');
  });
});

test('optional columns are chosen in the Columns menu and kept in the browser', async () => {
  await withPage('/audit', async () => {
    await act(async () => button('Columns').click());
    const team = [...document.querySelectorAll('label')].find((item) => item.textContent?.trim() === 'Team')?.querySelector('input') as HTMLInputElement;
    await act(async () => team.click());
    assert.ok([...document.querySelectorAll('th')].some((item) => item.textContent === 'Team'));
    assert.ok(rows()[1].textContent?.includes('Search'));
    assert.equal(localStorage.getItem('deltallm.audit.columns'), '["team"]');
  });
});
