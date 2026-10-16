import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import { AuthProvider } from '../src/lib/auth';
import { ToastProvider } from '../src/components/ToastProvider';
import RouteGroupRoutingTab from '../src/components/route-groups/RouteGroupRoutingTab';
import { useRouteGroupMutationScope } from '../src/lib/useRouteGroupMutationScope';
import type { RouteGroup, RouteGroupMemberDetail, RoutePolicy } from '../src/lib/api';

const GROUP: RouteGroup = {
  route_group_id: 'g1', group_key: 'support', name: 'Support', mode: 'chat', routing_strategy: null, enabled: true, member_count: 2, metadata: null,
};
const MEMBERS: RouteGroupMemberDetail[] = [
  { membership_id: 'm1', route_group_id: 'g1', deployment_id: 'dep-a', enabled: true, weight: null, priority: null, model_name: 'gpt-4o', provider: 'openai', healthy: true },
  { membership_id: 'm2', route_group_id: 'g1', deployment_id: 'dep-b', enabled: true, weight: null, priority: null, model_name: 'claude', provider: 'anthropic', healthy: true },
];
const BASE = '/ui/api/route-groups/by-id/g1';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const policy = (version: number, status: string, policyJson: Record<string, unknown>): RoutePolicy => ({
  route_policy_id: `p${version}`, route_group_id: 'g1', version, semantics_version: 1, status, policy_json: policyJson,
  published_at: status === 'draft' ? null : '2026-10-16T10:00:00Z', published_by: status === 'draft' ? null : 'admin_api',
  created_at: '2026-10-16T10:00:00Z', updated_at: `2026-10-16T10:00:0${version}Z`,
});

interface Server {
  policies: RoutePolicy[];
  requests: Array<{ method: string; path: string; body: unknown }>;
  /** Number of version reads that fail next. */
  failLists?: number;
}

function setup(server: Server) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return this.parentNode; } });
  // Frames run when a test flushes them, after React has committed, like a browser paint.
  const frames: FrameRequestCallback[] = [];
  dom.window.requestAnimationFrame = (callback) => frames.push(callback);
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = {
    window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator,
    requestAnimationFrame: dom.window.requestAnimationFrame, IS_REACT_ACT_ENVIRONMENT: true,
  };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : null;
    server.requests.push({ method, path: url.pathname, body });
    if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'master_key' });
    if (url.pathname === `${BASE}/policies`) {
      if (server.failLists) { server.failLists -= 1; return json({ detail: 'Unavailable' }, 503); }
      return json({ group_key: 'support', policies: server.policies });
    }
    const next = Math.max(0, ...server.policies.map((item) => item.version)) + 1;
    const publish = (document: Record<string, unknown>, version = next) => {
      server.policies = [
        policy(version, 'published', document),
        ...server.policies.filter((item) => item.version !== version).map((item) => (item.status === 'published' ? { ...item, status: 'archived' } : item)),
      ];
      return json({ group_key: 'support', policy: server.policies[0], warnings: [] });
    };
    if (url.pathname === `${BASE}/policy/publish`) {
      const draft = server.policies.find((item) => item.status === 'draft');
      return body && Object.keys(body).length > 0 ? publish(body) : publish(draft!.policy_json, draft!.version);
    }
    if (url.pathname === `${BASE}/policy/rollback`) {
      const source = server.policies.find((item) => item.version === body.version)!;
      return publish(source.policy_json);
    }
    if (url.pathname === `${BASE}/policy/draft` && method === 'POST') {
      const draft = server.policies.find((item) => item.status === 'draft');
      const saved = { ...policy(draft?.version ?? next, 'draft', body), updated_at: `2026-10-16T11:00:0${next}Z` };
      server.policies = [saved, ...server.policies.filter((item) => item !== draft)];
      return json({ group_key: 'support', policy: saved, warnings: [] });
    }
    if (url.pathname === `${BASE}/policy/draft` && method === 'DELETE') {
      const draft = server.policies.find((item) => item.status === 'draft')!;
      server.policies = server.policies.filter((item) => item !== draft);
      return json({ group_key: 'support', discarded: true, discarded_version: draft.version });
    }
    return json({ detail: 'Unexpected request' }, 500);
  };
  const root = createRoot(document.getElementById('root')!);
  return {
    root,
    flushFrames: () => act(async () => { frames.splice(0).forEach((callback) => callback(0)); }),
    async close() {
      await act(async () => root.unmount());
      globalThis.fetch = previousFetch;
      dom.window.close();
      for (const key of Object.keys(values)) {
        if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
        else Reflect.deleteProperty(globalThis, key);
      }
    },
  };
}

function Harness({ canWrite = true, onPublished = () => {}, group = GROUP }: { canWrite?: boolean; onPublished?: () => void; group?: RouteGroup }) {
  const mutations = useRouteGroupMutationScope();
  return (
    <AuthProvider><ToastProvider>
      <RouteGroupRoutingTab group={group} members={MEMBERS} canWrite={canWrite} canSimulate mutations={mutations} promptRef={null}
        onPublished={onPublished} onAddModel={() => {}} />
    </ToastProvider></AuthProvider>
  );
}

const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === text);
const choice = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('[role="radio"]')].find((item) => item.textContent?.includes(text))!;
const click = async (element: HTMLElement | undefined) => {
  assert.ok(element, 'element to click');
  await act(async () => element.click());
};

test('simple routing saves a live version, then offers Undo only when a version came before', async () => {
  const server: Server = { policies: [], requests: [] };
  const env = setup(server);
  let published = 0;
  try {
    await act(async () => env.root.render(<Harness onPublished={() => { published += 1; }} />));
    assert.match(document.body.textContent ?? '', /How should requests be shared\?/);
    assert.match(document.body.textContent ?? '', /Using the default/);
    assert.equal(choice('Spread evenly').getAttribute('aria-checked'), 'true');

    await click(choice('Primary & backup'));
    assert.ok(document.querySelector('[aria-label="Unsaved routing changes"]'));
    await click(button('Save changes'));
    const first = server.requests.find((request) => request.path === `${BASE}/policy/publish`);
    assert.deepEqual(first?.body, {
      strategy: 'priority-based-routing',
      members: [{ deployment_id: 'dep-a', enabled: true, priority: 0 }, { deployment_id: 'dep-b', enabled: true, priority: 1 }],
    });
    assert.equal(published, 1);
    assert.match(document.body.textContent ?? '', /Routing saved as version 1/);
    assert.equal(button('Undo'), undefined, 'the first version has nothing to undo');
    assert.equal(document.querySelector('[aria-label="Unsaved routing changes"]'), null);

    await click(choice('Spread evenly'));
    await click(button('Save changes'));
    assert.match(document.body.textContent ?? '', /Routing saved as version 2/);
    await click(button('Undo'));
    const rollback = server.requests.find((request) => request.path === `${BASE}/policy/rollback`);
    assert.deepEqual(rollback?.body, { version: 1 });
    assert.equal(server.policies[0].version, 3);
    assert.equal(choice('Primary & backup').getAttribute('aria-checked'), 'true');
    assert.doesNotMatch(document.body.textContent ?? '', /Routing saved as version/);
    assert.equal(published, 3);
  } finally { await env.close(); }
});

test('a draft opens advanced routing, and discarding it keeps advanced open with the way back enabled', async () => {
  const server: Server = { policies: [policy(2, 'draft', { strategy: 'priority-based-routing' }), policy(1, 'published', { strategy: 'weighted' })], requests: [] };
  const env = setup(server);
  try {
    await act(async () => env.root.render(<Harness />));
    assert.match(document.body.textContent ?? '', /Draft v2/);
    assert.equal(button('Back to simple routing')?.disabled, true);
    await click(button('Discard draft'));
    const dialog = [...document.querySelectorAll('[role="dialog"]')].at(-1)!;
    assert.match(dialog.textContent ?? '', /Discard draft v2\?/);
    await click([...dialog.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === 'Discard'));
    assert.ok(server.requests.some((request) => request.method === 'DELETE' && request.path === `${BASE}/policy/draft`));
    assert.match(document.body.textContent ?? '', /No draft/);
    assert.equal(button('Back to simple routing')?.disabled, false);
    await click(button('Back to simple routing'));
    assert.equal(choice('Custom split').getAttribute('aria-checked'), 'true');
  } finally { await env.close(); }
});

test('review publishes the stored draft as it is, and read-only users get no write actions', async () => {
  const server: Server = { policies: [policy(2, 'draft', { strategy: 'priority-based-routing' }), policy(1, 'published', { strategy: 'weighted' })], requests: [] };
  const env = setup(server);
  try {
    await act(async () => env.root.render(<Harness canWrite={false} />));
    assert.equal(button('Discard draft'), undefined);
    assert.equal(button('Review & publish'), undefined);
    assert.ok([...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].every((input) => input.disabled));

    await act(async () => env.root.render(<Harness />));
    await click(button('Review & publish'));
    const drawer = [...document.querySelectorAll('[role="dialog"]')].at(-1)!;
    assert.match(drawer.textContent ?? '', /Publish draft v2\?/);
    await click([...drawer.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === 'Publish'));
    const publish = server.requests.find((request) => request.path === `${BASE}/policy/publish`);
    assert.deepEqual(publish?.body, {}, 'a saved draft is published without a body');
    assert.equal(server.policies[0].status, 'published');
    assert.equal(server.policies[0].version, 2);
    assert.equal(document.querySelector('[role="dialog"]'), null);
  } finally { await env.close(); }
});

const tab = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find((item) => item.textContent?.startsWith(text));
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

test('restoring a version over a draft discards the draft first, then focus moves to the new state', async () => {
  const server: Server = {
    policies: [
      policy(3, 'draft', { strategy: 'weighted', context: { mode: 'eligible-only' } }),
      policy(2, 'published', { strategy: 'priority-based-routing' }),
      policy(1, 'archived', { strategy: 'simple-shuffle' }),
    ],
    requests: [],
  };
  const env = setup(server);
  try {
    await act(async () => env.root.render(<Harness />));
    await click(tab('History'));
    assert.equal(tab('History')?.getAttribute('aria-selected'), 'true');
    assert.equal(document.getElementById('advanced-routing-panel-history')?.getAttribute('aria-labelledby'), 'advanced-routing-tab-history');
    await click(button('v1'));
    const restore = button('Restore as draft')!;
    restore.focus();
    await click(restore);
    const dialog = [...document.querySelectorAll('[role="dialog"]')].at(-1)!;
    assert.match(dialog.textContent ?? '', /Restore v1 as the draft\?/);
    await click([...dialog.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === 'Restore'));
    const writes = server.requests.filter((request) => request.method !== 'GET').map((request) => `${request.method} ${request.path.slice(BASE.length)}`);
    assert.deepEqual(writes, ['DELETE /policy/draft', 'POST /policy/draft'], 'the old draft is deleted, so none of its settings stay');
    assert.deepEqual(server.requests.at(-2)?.body, { strategy: 'simple-shuffle' });
    assert.match(document.body.textContent ?? '', /Draft v3/);
    assert.equal(tab('Configure')?.getAttribute('aria-selected'), 'true');
    await env.flushFrames();
    assert.equal(document.activeElement?.getAttribute('aria-label'), 'Live and draft', 'focus is not lost when the form is replaced');
  } finally { await env.close(); }
});

test('the view waits for the live policy, so a group default that simple routing cannot show does not force advanced', async () => {
  const server: Server = { policies: [policy(1, 'published', { strategy: 'simple-shuffle' })], requests: [] };
  const env = setup(server);
  try {
    await act(async () => env.root.render(<Harness group={{ ...GROUP, routing_strategy: 'least-busy' }} />));
    assert.match(document.body.textContent ?? '', /How should requests be shared\?/);
    assert.equal(choice('Spread evenly').getAttribute('aria-checked'), 'true');
  } finally { await env.close(); }
});

test('a failed reload after a change shows a warning, never the versions from before the change', async () => {
  const server: Server = { policies: [policy(2, 'draft', { strategy: 'priority-based-routing' }), policy(1, 'published', { strategy: 'weighted' })], requests: [] };
  const env = setup(server);
  try {
    await act(async () => env.root.render(<Harness />));
    server.failLists = 2;
    await click(button('Discard draft'));
    const dialog = [...document.querySelectorAll('[role="dialog"]')].at(-1)!;
    await click([...dialog.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === 'Discard'));
    await settle();
    assert.match(document.querySelector('[role="alert"]')?.textContent ?? '', /The change is saved, but the routing could not be loaded again/);
    assert.equal(document.querySelector('[aria-label="Live and draft"]'), null, 'the deleted draft is not shown again');
    await click(button('Retry'));
    await settle();
    assert.match(document.body.textContent ?? '', /No draft/);
  } finally { await env.close(); }
});
