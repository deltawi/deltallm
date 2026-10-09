import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { MemoryRouter, useLocation } from 'react-router-dom';
import AssetList from '../src/components/admin/lists/AssetList';
import { CopyIdentifier, ListIdentity } from '../src/components/admin/lists/AssetListCells';
import { assetMetadataColumns } from '../src/components/admin/lists/assetMetadataColumns';
import DataTable, { type Column, type TableSort } from '../src/components/DataTable';
import { ApiError, models, routeGroups, promptRegistry } from '../src/lib/api';
import { AuthProvider } from '../src/lib/auth';
import { ToastProvider } from '../src/components/ToastProvider';
import { BrandingContext } from '../src/lib/brandingContext';
import { DEFAULT_BRANDING } from '../src/lib/branding';
import Models from '../src/pages/Models';
import RouteGroups from '../src/pages/RouteGroups';
import PromptRegistry from '../src/pages/PromptRegistry';

const USER_ID = 'e06d82a0-352d-46a1-bcc3-ffb3f9983eb1';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

function mountDom() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost' });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { configurable: true, value() {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { configurable: true, value() {} });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
    navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const root = createRoot(document.getElementById('root')!);
  return { dom, root, async close() {
    await act(async () => root.unmount());
    dom.window.close();
    for (const key of Object.keys(values)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  } };
}

test('shared list sorting, pagination, copy, error states, and row controls remain separate', async () => {
  const fixture = mountDom();
  const row = { id: 'item-1', name: 'Support', created_by_user_id: USER_ID,
    updated_at: '2026-10-08T12:34:00Z', visibility: 'team' };
  const sorts: TableSort[] = [];
  const pages: number[] = [];
  let opened = 0;
  let deleted = 0;
  let copied = '';
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { async writeText(value: string) { copied = value; } } });
  const columns: Column<typeof row>[] = [
    { key: 'name', header: 'Name', sortKey: 'name', render: (item) => <ListIdentity name={item.name} identifier={item.id} onOpen={() => opened++} actions={<button onClick={() => deleted++}>Delete</button>} /> },
    ...assetMetadataColumns<typeof row>(),
  ];
  const render = (error: unknown = null, loading = false, data = [row]) => fixture.root.render(<AssetList
    columns={columns} data={data} rowKey={(item) => item.id} loading={loading} error={error}
    onRetry={() => pages.push(-1)} emptyMessage="No items" search="" searchLabel="Search items"
    onSearchChange={() => {}} sort={{ key: 'updated_at', direction: 'desc' }} onSortChange={(sort) => sorts.push(sort)}
    pagination={{ total: 21, limit: 10, offset: 10, has_more: true }} onPageChange={(offset) => pages.push(offset)} onRowClick={() => opened++} />);
  try {
    await act(async () => render());
    const headers = [...document.querySelectorAll('th')];
    assert.equal(headers.at(-1)?.textContent, 'Visibility');
    assert.equal(headers[2].getAttribute('aria-sort'), 'descending');
    assert.equal(headers[0].getAttribute('aria-sort'), null);
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Sort by Updated at"]')!.click());
    assert.deepEqual(sorts.at(-1), { key: 'updated_at', direction: 'asc' });
    const select = document.querySelector<HTMLSelectElement>('[aria-label="Sort list"]')!;
    await act(async () => { select.value = 'name'; select.dispatchEvent(new fixture.dom.window.Event('change', { bubbles: true })); });
    assert.deepEqual(sorts.at(-1), { key: 'name', direction: 'asc' });
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Next page"]')!.click());
    assert.equal(pages.at(-1), 20);
    const copy = document.querySelector<HTMLButtonElement>(`button[aria-label="Copy user id ${USER_ID}"]`)!;
    await act(async () => { copy.dispatchEvent(new fixture.dom.window.KeyboardEvent('keydown', { bubbles: true, key: 'Enter' })); copy.click(); });
    assert.equal(copied, USER_ID);
    assert.equal(opened, 0);
    assert.ok(document.body.textContent?.includes('User ID copied'));
    await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent === 'Delete')!.click());
    assert.equal(deleted, 1);
    assert.equal(opened, 0);
    await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent === 'Support')!.click());
    assert.equal(opened, 1);
    await act(async () => render(new ApiError('Unavailable', 503)));
    assert.ok(document.body.textContent?.includes('The previous result is shown'));
    assert.ok(document.querySelector('table'));
    await act(async () => render(new ApiError('Denied', 403)));
    assert.ok(document.body.textContent?.includes('You do not have access'));
    assert.equal(document.querySelector('table'), null);
    assert.ok(!document.body.textContent?.includes(USER_ID));
    await act(async () => render(null, true));
    assert.equal(document.querySelector('[aria-busy]')?.getAttribute('aria-busy'), 'true');
    assert.ok(document.body.textContent?.includes('Updating the list'));
    await act(async () => render(null, false, []));
    assert.ok(document.body.textContent?.includes('No items'));
    await act(async () => render(new Error('failed'), false, []));
    assert.ok(document.body.textContent?.includes('Could not load the list'));
    await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent === 'Retry')!.click());
    assert.equal(pages.at(-1), -1);
    await act(async () => fixture.root.render(<DataTable columns={[{ key: 'name', header: 'Name' }]} data={[row]} onRowClick={() => opened++} />));
    assert.equal(document.querySelector('th')?.getAttribute('aria-sort'), null);
    await act(async () => document.querySelector('tbody tr')!.dispatchEvent(new fixture.dom.window.KeyboardEvent('keydown', { bubbles: true, key: 'Enter' })));
    assert.equal(opened, 2);
  } finally { await fixture.close(); }
});

test('blocked clipboard exposes the full selected ID and Escape restores focus', async () => {
  const fixture = mountDom();
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { async writeText() { throw new Error('denied'); } } });
  try {
    await act(async () => fixture.root.render(<CopyIdentifier value={USER_ID} />));
    const button = document.querySelector('button')!;
    await act(async () => button.click());
    const input = document.querySelector('input')!;
    assert.equal(input.value, USER_ID);
    assert.equal(document.activeElement, input);
    assert.equal(input.selectionStart, 0);
    assert.equal(input.selectionEnd, USER_ID.length);
    await act(async () => input.dispatchEvent(new fixture.dom.window.KeyboardEvent('keydown', { bubbles: true, key: 'Escape' })));
    assert.equal(document.querySelector('input'), null);
    assert.equal(document.activeElement, button);
    await act(async () => fixture.root.render(<CopyIdentifier />));
    assert.equal(document.body.textContent, 'Not recorded');
  } finally { await fixture.close(); }
});

test('an empty page keeps keyboard-accessible Previous controls and a valid count', async () => {
  const fixture = mountDom();
  const offsets: number[] = [];
  try {
    for (const total of [20, 0]) {
      await act(async () => fixture.root.render(<DataTable columns={[{ key: 'name', header: 'Group' }]} data={[]}
        pagination={{ total, limit: 20, offset: 20, has_more: false }} onPageChange={(offset) => offsets.push(offset)} responsive />));
      const previous = document.querySelector<HTMLButtonElement>('[aria-label="Previous page"]')!;
      assert.ok(previous);
      assert.equal(previous.disabled, false);
      previous.focus();
      assert.equal(document.activeElement, previous);
      assert.ok(document.body.textContent?.includes(`Showing 0 of ${total}`));
      assert.equal(document.querySelector<HTMLButtonElement>('[aria-label="Next page"]')?.disabled, true);
      await act(async () => previous.click());
      assert.equal(offsets.at(-1), 0);
    }
  } finally { await fixture.close(); }
});

test('page recovery waits for a successful current response', async () => {
  const fixture = mountDom();
  const offsets: number[] = [];
  const onPageChange = (offset: number) => offsets.push(offset);
  const render = (loading: boolean, error: unknown) => fixture.root.render(<AssetList
    columns={[{ key: 'name', header: 'Name' }]} data={[]} rowKey={(row: { name: string }) => row.name}
    loading={loading} error={error} onRetry={() => {}} emptyMessage="No items" search="" searchLabel="Search items"
    onSearchChange={() => {}} sort={{ key: 'updated_at', direction: 'desc' }} onSortChange={() => {}}
    pagination={{ total: 20, limit: 20, offset: 20, has_more: false }} onPageChange={onPageChange} onRowClick={() => {}} />);
  try {
    await act(async () => render(true, null));
    await act(async () => render(false, new ApiError('Unavailable', 503)));
    await act(async () => render(false, new ApiError('Denied', 403)));
    assert.deepEqual(offsets, []);
    await act(async () => render(false, null));
    assert.deepEqual(offsets, [0]);
  } finally { await fixture.close(); }
});

test('all list clients send server sort, paging, search, and cancellation', async () => {
  const previousFetch = globalThis.fetch;
  const requests: Array<{ url: URL; signal?: AbortSignal | null }> = [];
  globalThis.fetch = async (input, init) => { requests.push({ url: new URL(String(input), 'http://localhost'), signal: init?.signal }); return json({ data: [], pagination: { total: 0, offset: 20, limit: 10, has_more: false } }); };
  const signal = new AbortController().signal;
  try {
    const params = { sort_by: 'updated_at', sort_direction: 'desc', offset: 20, limit: 10, search: 'support / eu' } as const;
    await models.list(params, signal);
    await routeGroups.list(params, signal);
    await promptRegistry.listTemplates(params, signal);
    assert.equal(requests.length, 3);
    for (const request of requests) {
      assert.equal(request.signal, signal);
      assert.equal(request.url.searchParams.get('sort_by'), 'updated_at');
      assert.equal(request.url.searchParams.get('sort_direction'), 'desc');
      assert.equal(request.url.searchParams.get('offset'), '20');
      assert.equal(request.url.searchParams.get('search'), 'support / eu');
    }
  } finally { globalThis.fetch = previousFetch; }
});

function Location() { return <output id="location">{useLocation().pathname}</output>; }

test('all three lists return to a valid page after deleting the final row on page two', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const previousFetch = globalThis.fetch;
  const scenarios = [
    { Page: Models, path: '/models', api: '/ui/api/models', limit: 10, confirm: 'Delete Model',
      row: (name: string) => ({ deployment_id: name, model_name: name, provider: 'openai', mode: 'chat', model_info: {}, deltallm_params: {} }) },
    { Page: RouteGroups, path: '/route-groups', api: '/ui/api/route-groups', limit: 20, confirm: 'Delete Group',
      row: (name: string) => ({ route_group_id: name, group_key: name, name, mode: 'chat', enabled: true, member_count: 1 }) },
    { Page: PromptRegistry, path: '/prompts', api: '/ui/api/prompt-registry/templates', limit: 10, confirm: 'Delete Template',
      row: (name: string) => ({ prompt_template_id: name, template_key: name, name, version_count: 1, label_count: 1, binding_count: 0 }) },
  ];
  try {
    for (const scenario of scenarios) {
      const fixture = mountDom();
      const offsets: number[] = [];
      let deleted = false;
      globalThis.fetch = async (input, init) => {
        const url = new URL(String(input), 'http://localhost');
        if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'master_key' });
        if (init?.method === 'DELETE') { deleted = true; return json({ deleted: true, warnings: [] }); }
        if (url.pathname === scenario.api) {
          const offset = Number(url.searchParams.get('offset') || 0);
          const total = scenario.limit + (deleted ? 0 : 1);
          offsets.push(offset);
          return json({ data: offset >= total ? [] : [scenario.row(offset ? 'Last' : 'First')],
            pagination: { total, offset, limit: scenario.limit, has_more: offset + scenario.limit < total } });
        }
        return json({ data: [], pagination: { total: 0, limit: 20, offset: 0, has_more: false } });
      };
      try {
        await act(async () => fixture.root.render(<MemoryRouter initialEntries={[scenario.path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><AuthProvider><BrandingContext.Provider value={{ branding: DEFAULT_BRANDING, assetRevision: 0, setBranding() {}, async refreshBranding() { return DEFAULT_BRANDING; } }}><ToastProvider><scenario.Page /></ToastProvider></BrandingContext.Provider></AuthProvider></MemoryRouter>));
        await act(async () => context.mock.timers.tick(300));
        await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Next page"]')!.click());
        assert.equal(offsets.at(-1), scenario.limit);
        await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Delete Last"]')!.click());
        await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent === scenario.confirm)!.click());
        assert.equal(deleted, true);
        assert.deepEqual(offsets.slice(-2), [scenario.limit, 0]);
        assert.ok(document.body.textContent?.includes('First'));
        assert.ok(!document.body.textContent?.includes('No model groups yet'));
        assert.ok(!document.body.textContent?.includes(`${scenario.limit + 1}–${scenario.limit}`));
        assert.equal(document.querySelector('[role="dialog"]'), null);
      } finally { await fixture.close(); }
    }
  } finally { globalThis.fetch = previousFetch; }
});

test('all three pages share metadata, reset the server page on sort, and retain detail actions', async () => {
  const previousFetch = globalThis.fetch;
  const scenarios = [
    { Page: Models, path: '/models', api: '/ui/api/models', limit: 10, detail: '/models/dep-1',
      row: { deployment_id: 'dep-1', model_name: 'support', display_name: 'Support', provider: 'openai', mode: 'chat', healthy: true,
        deltallm_params: { model: 'openai/gpt-4o-mini' }, credential_source: 'inline' } },
    { Page: RouteGroups, path: '/route-groups', api: '/ui/api/route-groups', limit: 20, detail: '/route-groups/by-id/group-1',
      row: { route_group_id: 'group-1', group_key: 'support', name: 'Support', mode: 'chat', enabled: true, member_count: 2,
        health_status: 'degraded', healthy_member_count: 1, active_member_count: 2 } },
    { Page: PromptRegistry, path: '/prompts', api: '/ui/api/prompt-registry/templates', limit: 10, detail: '/prompts/support%2Feu',
      row: { prompt_template_id: 'prompt-1', template_key: 'support/eu', name: 'Support', version_count: 2, label_count: 1, binding_count: 3 } },
  ];
  try {
    for (const scenario of scenarios) {
      const fixture = mountDom();
      const requests: Array<{ url: URL; init?: RequestInit }> = [];
      globalThis.fetch = async (input, init) => {
        const url = new URL(String(input), 'http://localhost'); requests.push({ url, init });
        if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'master_key' });
        if (init?.method === 'DELETE') return json({ deleted: true, warnings: [] });
        if (url.pathname === scenario.api) return json({ data: [{ ...scenario.row, created_by_user_id: USER_ID,
          updated_at: '2026-10-08T12:34:00Z', visibility: 'team' }], pagination: { total: 41, offset: Number(url.searchParams.get('offset') || 0), limit: scenario.limit, has_more: true } });
        return json({ data: [], pagination: { total: 0, offset: 0, limit: 20, has_more: false } });
      };
      try {
        await act(async () => fixture.root.render(<MemoryRouter initialEntries={[scenario.path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><AuthProvider><BrandingContext.Provider value={{ branding: DEFAULT_BRANDING, assetRevision: 0, setBranding() {}, async refreshBranding() { return DEFAULT_BRANDING; } }}><ToastProvider><Location /><scenario.Page /></ToastProvider></BrandingContext.Provider></AuthProvider></MemoryRouter>));
        const listRequests = () => requests.filter((request) => request.url.pathname === scenario.api);
        assert.equal(listRequests().at(-1)?.url.searchParams.get('sort_by'), 'updated_at');
        assert.equal(document.querySelectorAll('th').item(6).textContent, 'Visibility');
        assert.ok(document.querySelector(`button[aria-label="Copy user id ${USER_ID}"]`));
        if (scenario.Page === Models) {
          assert.ok(document.querySelector('img[alt="OpenAI logo"]'));
          assert.ok(!document.body.textContent?.includes('inline'));
          assert.ok(document.body.textContent?.includes('Chat'));
        }
        await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Next page"]')!.click());
        assert.equal(listRequests().at(-1)?.url.searchParams.get('offset'), String(scenario.limit));
        const previous = listRequests().at(-1)!;
        await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Sort by Created by"]')!.click());
        assert.equal(previous.init?.signal?.aborted, true);
        assert.equal(listRequests().at(-1)?.url.searchParams.get('offset'), '0');
        assert.equal(listRequests().at(-1)?.url.searchParams.get('sort_by'), 'created_by');
        assert.equal(listRequests().at(-1)?.url.searchParams.get('sort_direction'), 'asc');
        await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Delete Support"]')!.click());
        assert.ok(document.querySelector('[role="dialog"]'));
        assert.equal(document.getElementById('location')?.textContent, scenario.path);
        await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent === 'Cancel')!.click());
        const readsBeforeDelete = listRequests().filter((request) => !request.init?.method).length;
        await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Delete Support"]')!.click());
        const confirmLabel = scenario.Page === Models ? 'Delete Model' : scenario.Page === RouteGroups ? 'Delete Group' : 'Delete Template';
        await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent === confirmLabel)!.click());
        const deletion = requests.find((request) => request.init?.method === 'DELETE');
        const expectedDelete = scenario.Page === Models ? '/ui/api/models/dep-1' : scenario.Page === RouteGroups ? '/ui/api/route-groups/by-id/group-1' : '/ui/api/prompt-registry/templates/support%2Feu';
        assert.equal(deletion?.url.pathname, expectedDelete);
        assert.ok(listRequests().filter((request) => !request.init?.method).length > readsBeforeDelete);
        assert.equal(document.getElementById('location')?.textContent, scenario.path);
        await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent === 'Support')!.click());
        assert.equal(document.getElementById('location')?.textContent, scenario.detail);
      } finally { await fixture.close(); }
    }
  } finally { globalThis.fetch = previousFetch; }
});
