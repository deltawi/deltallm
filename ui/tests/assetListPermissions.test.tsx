import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { AuthProvider } from '../src/lib/auth';
import { ToastProvider } from '../src/components/ToastProvider';
import { BrandingContext } from '../src/lib/brandingContext';
import { DEFAULT_BRANDING } from '../src/lib/branding';
import Models from '../src/pages/Models';
import RouteGroups from '../src/pages/RouteGroups';

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
function Location() { return <output id="location">{useLocation().pathname}</output>; }

test('reader rows hide model write controls and group creation keeps its payload and warnings', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { configurable: true, value() {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { configurable: true, value() {} });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const previousFetch = globalThis.fetch;
  let admin = false;
  let payload: unknown;
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'session', account_id: 'reader-id', role: admin ? 'platform_admin' : 'org_user', ui_access: { models: true, model_admin: admin } });
    if (init?.method === 'POST') { payload = JSON.parse(String(init.body)); return json({ route_group_id: 'created-id', group_key: 'support', warnings: ['Runtime refresh pending'] }); }
    if (url.pathname === '/ui/api/models') return json({ data: [{ deployment_id: 'dep-1', model_name: 'Support', provider: 'openai', mode: 'chat', model_info: {}, deltallm_params: {}, access: { visibility: 'team', capabilities: { edit: false, delete: false } } }], pagination: { total: 1, limit: 10, offset: 0, has_more: false } });
    return json({ data: [], pagination: { total: 0, limit: 20, offset: 0, has_more: false } });
  };
  const root = createRoot(document.getElementById('root')!);
  const render = (groups: boolean) => root.render(<MemoryRouter key={String(groups)} initialEntries={[groups ? '/route-groups' : '/models']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><AuthProvider><BrandingContext.Provider value={{ branding: DEFAULT_BRANDING, assetRevision: 0, setBranding() {}, async refreshBranding() { return DEFAULT_BRANDING; } }}><ToastProvider><Location />{groups ? <RouteGroups /> : <Models />}</ToastProvider></BrandingContext.Provider></AuthProvider></MemoryRouter>);
  const fill = async (id: string, value: string) => act(async () => {
    const input = document.getElementById(id) as HTMLInputElement;
    input.focus();
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    input.dispatchEvent(new dom.window.KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
  });
  try {
    await act(async () => render(false));
    assert.ok(document.body.textContent?.includes('Support'));
    assert.equal(document.querySelector('[aria-label="Delete Support"]'), null);
    assert.equal(document.querySelector('[aria-label="Edit Support"]'), null);
    assert.ok(!document.body.textContent?.includes('Add Model'));
    admin = true;
    await act(async () => render(true));
    await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Create Group')!.click());
    await fill('create-group-key', 'support');
    await fill('create-group-name', 'Support');
    await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent?.startsWith('Create and continue'))!.click());
    assert.deepEqual(payload, { group_key: 'support', name: 'Support', mode: 'chat', access: { grants: [], visibility: 'private', subject_id: null, access_role: null } });
    assert.equal(document.getElementById('location')?.textContent, '/route-groups/by-id/created-id');
    assert.ok(document.body.textContent?.includes('Runtime refresh pending'));
    assert.equal(document.querySelector('[role="dialog"]'), null);
  } finally {
    await act(async () => root.unmount()); globalThis.fetch = previousFetch; dom.window.close();
    for (const key of Object.keys(values)) { if (previous[key]) Object.defineProperty(globalThis, key, previous[key]); else Reflect.deleteProperty(globalThis, key); }
  }
});
