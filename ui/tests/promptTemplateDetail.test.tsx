import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { ToastProvider } from '../src/components/ToastProvider';
import { AuthProvider } from '../src/lib/auth';
import { DEFAULT_BRANDING } from '../src/lib/branding';
import { BrandingContext } from '../src/lib/brandingContext';
import PromptTemplateDetail from '../src/pages/PromptTemplateDetail';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

const V2 = 'You are the triage assistant for {product_name}.\nClassify tickets from {customer_tier} customers.';
const V3 = `${V2}\nAnswer in {language}.`;

function version(number: number, content: string, required: string[]) {
  return {
    prompt_version_id: `pv${number}`, prompt_template_id: 't1', template_key: 'support-triage', version: number,
    status: number === 2 ? 'published' : 'draft', template_body: { messages: [{ role: 'system', content }] },
    variables_schema: { type: 'object', required, properties: Object.fromEntries(required.map((name) => [name, { type: 'string' }])) },
    model_hints: {}, route_preferences: {}, created_at: `2026-10-0${number}T10:00:00Z`,
  };
}

function label(name: string, number: number) {
  return { prompt_label_id: `l-${name}`, prompt_template_id: 't1', template_key: 'support-triage', label: name, prompt_version_id: `pv${number}`, version: number, updated_at: '2026-10-09T10:00:00Z' };
}

function backend({ admin = true, write = true } = {}) {
  const state = {
    versions: [version(2, V2, ['product_name', 'customer_tier']), version(3, V3, ['product_name', 'customer_tier', 'language'])],
    labels: [label('production', 2), label('staging', 3)],
  };
  const calls: Array<{ method: string; path: string; body: unknown }> = [];
  const fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : null;
    if (method !== 'GET') calls.push({ method, path: url.pathname, body });
    if (url.pathname === '/auth/me') {
      return json({ authenticated: true, auth_mode: 'session', account_id: 'acc-1', role: admin ? 'platform_admin' : 'org_user', ui_access: { prompts: true } });
    }
    if (url.pathname.startsWith('/ui/api/assets/audience-options')) return json({ data: [] });
    const base = '/ui/api/prompt-registry/templates/support-triage';
    if (url.pathname === `${base}/labels` && method === 'POST') {
      const next = label(body.label, body.version);
      state.labels = [...state.labels.filter((item) => item.label !== body.label), next];
      return json(next);
    }
    if (url.pathname.startsWith(`${base}/labels/`) && method === 'DELETE') {
      const name = decodeURIComponent(url.pathname.slice(`${base}/labels/`.length));
      state.labels = state.labels.filter((item) => item.label !== name);
      return json({ deleted: true });
    }
    if (url.pathname === `${base}/versions` && method === 'POST') {
      const created = { ...version(state.versions.length + 2, '', []), template_body: body.template_body, variables_schema: body.variables_schema };
      state.versions = [...state.versions, created];
      return json(created);
    }
    if (url.pathname === base) {
      return json({
        template: {
          prompt_template_id: 't1', template_key: 'support-triage', name: 'Support triage', description: 'Sorts tickets.', owner_scope: null,
          metadata: null, version_count: state.versions.length, label_count: state.labels.length, binding_count: 1,
          access: { managed_asset_id: 'ma1', asset_kind: 'prompt_template', governance_source: 'platform', owner_account_id: null, visibility: 'platform',
            subject_id: null, access_role: null, grants: [], effective_role: write ? 'owner' : 'reader', policy_version: 1,
            capabilities: { read: true, write, manage_access: write, delete: write, platform_admin_override: admin } },
        },
        versions: [...state.versions].reverse(),
        labels: state.labels,
        bindings: admin ? [{ prompt_binding_id: 'b1', scope_type: 'group', scope_id: 'support-chat', prompt_template_id: 't1', template_key: 'support-triage', label: 'production', priority: 100, enabled: true, metadata: null }] : [],
      });
    }
    return json({});
  };
  return { fetch, calls, state };
}

async function mount(options?: { admin?: boolean; write?: boolean }) {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const globals = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const server = backend(options);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = server.fetch as typeof fetch;
  const root = createRoot(document.getElementById('root')!);
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/prompts/support-triage']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider><ToastProvider><BrandingContext.Provider value={{ branding: DEFAULT_BRANDING, assetRevision: 0, refreshBranding: async () => DEFAULT_BRANDING, setBranding: () => {} }}>
        <Routes><Route path="/prompts/:templateKey" element={<PromptTemplateDetail />} /></Routes>
      </BrandingContext.Provider></ToastProvider></AuthProvider>
    </MemoryRouter>,
  ));
  for (let attempt = 0; attempt < 20 && !document.body.textContent?.includes('Support triage'); attempt += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)); });
  }
  const button = (name: string | RegExp) => {
    const match = [...document.querySelectorAll('button')].find((item) => (typeof name === 'string' ? item.textContent?.trim() === name : name.test(item.textContent ?? '')));
    assert.ok(match, `button ${String(name)} exists`);
    return match;
  };
  const click = async (name: string | RegExp) => { await act(async () => button(name).click()); await settle(); };
  const settle = async () => { for (let index = 0; index < 5; index += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); };
  return {
    server, button, click, settle,
    async close() {
      await act(async () => root.unmount());
      globalThis.fetch = originalFetch;
      dom.window.close();
      for (const key of Object.keys(globals)) {
        if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
        else Reflect.deleteProperty(globalThis, key);
      }
    },
  };
}

test('prompt details lead with the live prompt and hide the publish status', async () => {
  const page = await mount();
  try {
    const text = document.body.textContent ?? '';
    assert.ok(text.includes('Classify tickets from {customer_tier} customers.'), 'production prompt text is visible');
    assert.ok(text.includes('production') && text.includes('Used by route group support-chat'));
    assert.equal([...document.querySelectorAll('button')].some((item) => /publish/i.test(item.textContent ?? '')), false);
    assert.equal(text.includes('Published'), false);
    assert.deepEqual([...document.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent?.replace(/\d+$/, '')), ['Prompt', 'Versions', 'Test', 'Used by', 'Settings']);
  } finally {
    await page.close();
  }
});

test('promoting staging shows the change and moves production', async () => {
  const page = await mount();
  try {
    await page.click('Promote v3 to production');
    const dialog = document.querySelector('[role="dialog"]');
    assert.ok(dialog?.textContent?.includes('Move production from v2 to v3?'));
    assert.ok(dialog?.textContent?.includes('Route group support-chat gets v3 on the next request.'));
    assert.ok(dialog?.querySelector('ins')?.textContent?.includes('{language}'));
    assert.ok(dialog?.textContent?.includes('v3 also requires language'));
    await page.click('Move production to v3');
    assert.deepEqual(page.server.calls, [{
      method: 'POST', path: '/ui/api/prompt-registry/templates/support-triage/labels',
      body: { label: 'production', version: 3, require_approval: false, approved_by: null },
    }]);
    assert.equal(document.querySelector('[role="dialog"]'), null);
  } finally {
    await page.close();
  }
});

test('a new version starts from the latest one and points staging at it', async () => {
  const page = await mount();
  try {
    await page.click('New version');
    const content = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message 1 content"]');
    assert.equal(content?.value, V3);
    await act(async () => {
      content!.focus();
      Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!.call(content, `${V3} Use a {tone} tone.`);
      content!.dispatchEvent(new window.Event('input', { bubbles: true }));
      // react-dom loads before the DOM globals exist, so it watches key events on the focused field.
      content!.dispatchEvent(new window.KeyboardEvent('keyup', { bubbles: true, key: 'e' }));
    });
    assert.ok(document.querySelector('[role="dialog"]')?.textContent?.includes('tone · new'));
    await page.click('Save v4');
    assert.equal(page.server.calls.length, 2);
    const [create, move] = page.server.calls;
    assert.equal(create.path, '/ui/api/prompt-registry/templates/support-triage/versions');
    assert.deepEqual((create.body as { variables_schema: { required: string[] } }).variables_schema.required, ['product_name', 'customer_tier', 'language', 'tone']);
    assert.equal('publish' in (create.body as object), false);
    assert.deepEqual(move.body, { label: 'staging', version: 4 });
    assert.equal(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.startsWith('Versions'), true);
  } finally {
    await page.close();
  }
});

test('removing a label asks first and names the bindings it affects', async () => {
  const page = await mount();
  try {
    const productionCard = [...document.querySelectorAll('article')].find((card) => card.textContent?.includes('production'));
    const change = [...productionCard!.querySelectorAll('button')].find((item) => item.textContent === 'Change version');
    await act(async () => change!.click());
    await page.click('Remove label');
    const dialog = document.querySelector('[role="dialog"]');
    assert.ok(dialog?.textContent?.includes('Remove production?'));
    assert.ok(dialog?.textContent?.includes('1 binding uses this label.'));
    assert.deepEqual(page.server.calls, []);
    const confirm = [...dialog!.querySelectorAll('button')].find((item) => item.textContent?.trim() === 'Remove label');
    await act(async () => confirm!.click());
    await page.settle();
    assert.deepEqual(page.server.calls, [{ method: 'DELETE', path: '/ui/api/prompt-registry/templates/support-triage/labels/production', body: null }]);
    assert.ok(document.body.textContent?.includes('Set up this prompt'));
  } finally {
    await page.close();
  }
});

test('readers see the prompt without edit controls or bindings', async () => {
  const page = await mount({ admin: false, write: false });
  try {
    const labels = [...document.querySelectorAll('button')].map((item) => item.textContent?.trim());
    for (const hidden of ['New version', 'Change version', 'Add label', 'Promote v3 to production']) assert.equal(labels.includes(hidden), false, hidden);
    assert.equal([...document.querySelectorAll('[role="tab"]')].some((tab) => tab.textContent?.startsWith('Used by')), false);
    assert.ok(document.body.textContent?.includes('Classify tickets'));
  } finally {
    await page.close();
  }
});
