import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation, useNavigate, type NavigateFunction } from 'react-router-dom';
import { JSDOM } from 'jsdom';

import { AuthProvider } from '../src/lib/auth';
import { ToastProvider } from '../src/components/ToastProvider';
import RouteGroupRoute from '../src/pages/RouteGroupRoute';
import { legacyRouteGroupKey, routeGroupDetailPath } from '../src/lib/routeGroupRoutes';
import { routeGroups } from '../src/lib/api/routeGroups';

const GROUP_ID = '12f410b2-641f-40fb-9ba8-4281b70bc8ca';
const NEXT_ID = '12f410b2-641f-40fb-9ba8-4281b70bc8cb';
const KEYS = ['vendor/model', 'vendor/model/v2', 'vendor%2Fmodel', 'support / eu',
  'مجموعة/نموذج', 'model?variant#1', 'team/policy', 'team/members'];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

test('legacy names decode once and the query resolver preserves the exact key', async () => {
  const previousFetch = globalThis.fetch;
  try {
    for (const key of KEYS) {
      assert.equal(legacyRouteGroupKey('/route-groups/' + encodeURIComponent(key)), key);
      const controller = new AbortController();
      globalThis.fetch = async (input, init) => {
        const url = new URL(String(input), 'http://localhost');
        assert.equal(url.pathname, '/ui/api/route-groups/resolve/by-key');
        assert.equal(url.searchParams.get('group_key'), key);
        assert.equal(init?.signal, controller.signal);
        return json({ route_group_id: GROUP_ID, group_key: key });
      };
      await routeGroups.resolveKey(key, controller.signal);
    }
    assert.equal(legacyRouteGroupKey('/route-groups/vendor/model'), 'vendor/model');
    assert.equal(legacyRouteGroupKey('/route-groups/bad%2'), null);
    assert.equal(legacyRouteGroupKey('/route-groups/'), null);
    assert.equal(routeGroupDetailPath(GROUP_ID), '/route-groups/by-id/' + GROUP_ID);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('detail navigation preserves names, redirects old links, and ignores a stale delete', async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: 'http://localhost',
  });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  for (const [key, value] of Object.entries({
    window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) Object.defineProperty(globalThis, key, { configurable: true, value });
  const previousFetch = globalThis.fetch;
  const requests: Array<{ url: URL; init?: RequestInit }> = [];
  let key = KEYS[0];
  let detailStatus = 200;
  let deleteResponse: ((response: Response) => void) | undefined;
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    requests.push({ url, init });
    if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'master_key' });
    if (url.pathname.endsWith('/resolve/by-key')) return json({
      group_key: url.searchParams.get('group_key'), route_group_id: GROUP_ID,
    });
    if (init?.method === 'DELETE') return new Promise((resolve) => { deleteResponse = resolve; });
    if (url.pathname.endsWith('/policies')) return json({ group_key: key, policies: [] });
    if (url.pathname.includes('/route-groups/by-id/')) {
      if (detailStatus !== 200) return json({ detail: 'Unavailable' }, detailStatus);
      return json({
        group: { route_group_id: url.pathname.split('/').at(-1), group_key: key,
          name: null, mode: 'chat', routing_strategy: null, enabled: true,
          member_count: 0, metadata: null },
        members: [], policy: null, bindings: [],
      });
    }
    if (url.pathname.includes('/resolve')) return json({ winner: null, candidates: [] });
    return json({ data: [], pagination: { total: 0, limit: 20, offset: 0, has_more: false } });
  };
  let navigate: NavigateFunction;
  function Probe() {
    const routerNavigate = useNavigate();
    useEffect(() => { navigate = routerNavigate; }, [routerNavigate]);
    return <output id="location">{useLocation().pathname}</output>;
  }
  const root = createRoot(document.getElementById('root')!);
  const render = (initialPath: string) => root.render(
    <MemoryRouter initialEntries={[initialPath]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider><ToastProvider>
        <Probe />
        <Routes>
          <Route path="/route-groups/by-id/:routeGroupId" element={<RouteGroupRoute />} />
          <Route path="/route-groups/*" element={<RouteGroupRoute />} />
          <Route path="/route-groups" element={<p>Group list</p>} />
        </Routes>
      </ToastProvider></AuthProvider>
    </MemoryRouter>,
  );
  try {
    await act(async () => render('/route-groups/vendor/model'));
    assert.equal(document.getElementById('location')?.textContent, routeGroupDetailPath(GROUP_ID));
    assert.ok(document.body.textContent?.includes(key));
    for (key of KEYS.slice(1)) {
      await act(async () => navigate('/route-groups/' + encodeURIComponent(key)));
      assert.equal(document.getElementById('location')?.textContent, routeGroupDetailPath(GROUP_ID));
      assert.ok(document.body.textContent?.includes(key), key);
      const resolution = requests.filter((r) => r.url.pathname.endsWith('/resolve/by-key')).at(-1);
      assert.equal(resolution?.url.searchParams.get('group_key'), key);
      const binding = requests.filter((r) => r.url.searchParams.has('scope_id')).at(-1);
      assert.equal(binding?.url.searchParams.get('scope_id'), key);
    }
    await act(async () => document.querySelector<HTMLButtonElement>('button[title="Delete group"]')!.click());
    const confirm = [...document.querySelectorAll('button')].find((b) => b.textContent === 'Delete Group')!;
    assert.ok(confirm);
    await act(async () => { confirm.click(); confirm.click(); });
    const deletions = requests.filter((r) => r.init?.method === 'DELETE');
    assert.equal(deletions.length, 1);
    assert.equal(deletions[0].url.pathname, '/ui/api/route-groups/by-id/' + GROUP_ID);
    key = 'next/group';
    await act(async () => navigate(routeGroupDetailPath(NEXT_ID)));
    assert.equal(deletions[0].init?.signal?.aborted, true);
    assert.ok(document.body.textContent?.includes(key));
    await act(async () => deleteResponse!(json({ deleted: true, warnings: [] })));
    assert.equal(document.getElementById('location')?.textContent, routeGroupDetailPath(NEXT_ID));
    assert.ok(!document.body.textContent?.includes('Group deleted'));

    for (const status of [403, 404, 503]) {
      detailStatus = status;
      await act(async () => navigate(routeGroupDetailPath(GROUP_ID)));
      assert.ok(document.body.textContent?.includes(status === 403 ? 'permission' :
        status === 404 ? 'Model group not found.' : 'Failed to load route group details.'));
      detailStatus = 200;
      const retry = [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Retry')!;
      await act(async () => retry.click());
      assert.ok(document.body.textContent?.includes(key));
      await act(async () => navigate(routeGroupDetailPath(NEXT_ID)));
    }
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = previousFetch;
    dom.window.close();
    for (const name of ['window', 'document', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT']) {
      if (previous[name]) Object.defineProperty(globalThis, name, previous[name]);
      else Reflect.deleteProperty(globalThis, name);
    }
  }
});
