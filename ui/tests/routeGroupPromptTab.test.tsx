import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { JSDOM } from 'jsdom';

import { AuthProvider } from '../src/lib/auth';
import { ToastProvider } from '../src/components/ToastProvider';
import RouteGroupPromptTab from '../src/components/route-groups/RouteGroupPromptTab';
import { useRouteGroupMutationScope } from '../src/lib/useRouteGroupMutationScope';
import type { PromptBinding } from '../src/lib/api';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const page = <T,>(data: T[], hasMore = false) => ({ data, pagination: { total: data.length, limit: 100, offset: 0, has_more: hasMore } });
const TEMPLATES = [
  { prompt_template_id: 't1', template_key: 'welcome', name: 'Welcome' },
  { prompt_template_id: 't2', template_key: 'faq', name: 'FAQ' },
];
const LABELS: Record<string, string[]> = { welcome: ['staging'], faq: ['blue', 'green'] };
const binding = (template_key: string, label: string): PromptBinding => ({
  prompt_binding_id: `b-${template_key}`, scope_type: 'group', scope_id: 'support', prompt_template_id: 't', template_key, label, priority: 100, enabled: true, metadata: null,
});

interface Server {
  bindings: PromptBinding[];
  requests: Array<{ method: string; path: string; body: unknown }>;
  failDelete?: boolean;
}

function setup(server: Server) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : null;
    server.requests.push({ method, path: url.pathname, body });
    if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'master_key' });
    if (url.pathname === '/ui/api/prompt-registry/templates') return json(page(TEMPLATES));
    if (url.pathname.startsWith('/ui/api/prompt-registry/templates/')) {
      const key = decodeURIComponent(url.pathname.split('/').at(-1)!);
      return json({ template: TEMPLATES.find((item) => item.template_key === key), versions: [], bindings: [], labels: LABELS[key].map((label) => ({ label, version: 1 })) });
    }
    if (url.pathname === '/ui/api/prompt-registry/bindings' && method === 'GET') return json(page(server.bindings));
    if (url.pathname === '/ui/api/prompt-registry/bindings' && method === 'POST') {
      const saved = binding(body.template_key, body.label);
      server.bindings = [...server.bindings.filter((item) => item.prompt_binding_id !== saved.prompt_binding_id), saved];
      return json(saved);
    }
    if (url.pathname.startsWith('/ui/api/prompt-registry/bindings/') && method === 'DELETE') {
      if (server.failDelete) return json({ detail: 'Binding is locked' }, 409);
      server.bindings = server.bindings.filter((item) => !url.pathname.endsWith(item.prompt_binding_id));
      return json({ deleted: true });
    }
    return json({ detail: 'Unexpected request' }, 500);
  };
  const root = createRoot(document.getElementById('root')!);
  return {
    root,
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

function Harness() {
  const mutations = useRouteGroupMutationScope();
  return (
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider><ToastProvider>
        <RouteGroupPromptTab groupKey="support" mutations={mutations} onChanged={() => {}} />
      </ToastProvider></AuthProvider>
    </MemoryRouter>
  );
}

const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === text);
const field = (label: string) => [...document.querySelectorAll('label')].find((item) => item.textContent?.startsWith(label))!.querySelector('select')!;
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
async function pick(select: HTMLSelectElement, value: string) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new window.Event('change', { bubbles: true }));
  });
  await settle();
}

test('the label must be one the prompt has: a single label is picked, several need a choice', async () => {
  const server: Server = { bindings: [], requests: [] };
  const env = setup(server);
  try {
    await act(async () => env.root.render(<Harness />));
    await settle();
    await pick(field('Prompt'), 'welcome');
    assert.equal(field('Version label').value, 'staging', 'the only label is used, not the default "production"');
    assert.equal(button('Attach prompt')?.disabled, false);

    await pick(field('Prompt'), 'faq');
    assert.equal(field('Version label').value, '');
    assert.equal(button('Attach prompt')?.disabled, true, 'a label the prompt does not have cannot be submitted');
    await pick(field('Version label'), 'green');
    await act(async () => button('Attach prompt')!.click());
    await settle();
    const write = server.requests.find((request) => request.method === 'POST');
    assert.deepEqual(write?.body, { scope_type: 'group', scope_id: 'support', template_key: 'faq', label: 'green', priority: 100, enabled: true });
  } finally { await env.close(); }
});

test('a change that attaches the new prompt but cannot remove the old one says so and shows both', async () => {
  const server: Server = { bindings: [binding('welcome', 'staging')], requests: [], failDelete: true };
  const env = setup(server);
  try {
    await act(async () => env.root.render(<Harness />));
    await settle();
    await act(async () => button('Change')!.click());
    await settle();
    await pick(field('Prompt'), 'faq');
    await pick(field('Version label'), 'blue');
    await act(async () => button('Save')!.click());
    await settle();
    const text = document.body.textContent ?? '';
    assert.match(text, /Prompt partly saved/);
    assert.match(text, /faq \(blue\) is attached, but welcome \(staging\) was not removed: Binding is locked\. Remove it below\./);
    assert.equal(document.querySelectorAll('a[href^="/prompts/"]').length, 2, 'the list shows both attached prompts');
  } finally { await env.close(); }
});
