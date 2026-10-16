import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { MemoryRouter } from 'react-router-dom';
import Playground from '../src/pages/Playground';
import { BrandingContext } from '../src/lib/brandingContext';
import { DEFAULT_BRANDING } from '../src/lib/branding';
import { useChatEngine, type ChatEngine } from '../src/components/playground/useChatEngine';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const modelList = (healthy: boolean | null | undefined) => ({ data: [{
  deployment_id: 'dep-1', model_name: 'Support', provider: 'openai', mode: 'chat', healthy,
  model_info: {}, deltallm_params: {},
}], pagination: { total: 1, limit: 200, offset: 0, has_more: false } });

function setup(desktop = true, path = '/playground') {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  const timers = new Map<number, () => void>();
  let nextTimer = 0;
  dom.window.setTimeout = callback => { const timer = ++nextTimer; timers.set(timer, callback as () => void); return timer; };
  dom.window.clearTimeout = timer => { timers.delete(timer); };
  Object.defineProperty(dom.window, 'matchMedia', { value: () => ({ matches: desktop, addEventListener() {}, removeEventListener() {} }) });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'scrollIntoView', { value() {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetParent', { get() { return this.parentNode; } });
  dom.window.requestAnimationFrame = callback => { callback(0); return 1; };
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator, requestAnimationFrame: dom.window.requestAnimationFrame, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const originalFetch = globalThis.fetch;
  const root = createRoot(document.getElementById('root')!);
  const render = () => root.render(<MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><BrandingContext.Provider value={{ branding: DEFAULT_BRANDING, assetRevision: 0, setBranding() {}, async refreshBranding() { return DEFAULT_BRANDING; } }}><Playground /></BrandingContext.Provider></MemoryRouter>);
  return { dom, root, render, flushTimers() { const pending = [...timers.values()]; timers.clear(); pending.forEach(callback => callback()); }, async close() {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    dom.window.close();
    for (const key of Object.keys(values)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  } };
}

for (const desktop of [true, false]) {
  for (const healthy of [true, false, null, undefined]) {
    const status = healthy == null ? 'unknown' : healthy ? 'online' : 'offline';
    test(`${desktop ? 'desktop' : 'mobile'} Playground shows ${String(healthy)} health as ${status}`, async () => {
      const env = setup(desktop);
      globalThis.fetch = async () => json(modelList(healthy));
      try {
        await act(async () => env.render());
        const badge = document.querySelector(`[title="Status: ${status}"]`);
        assert.ok(badge);
        assert.equal(badge.textContent?.toLowerCase(), status);
        if (healthy == null) assert.equal(document.querySelector('[title="Status: offline"]'), null);
        if (!desktop) {
          const modelButton = [...document.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="dialog"]')].find(button => button.textContent?.includes('Support'))!;
          await act(async () => modelButton.click());
          const sheet = document.querySelector('[role="dialog"]')!;
          assert.ok(sheet.querySelector(`[title="Status: ${status}"]`));
          await act(async () => env.dom.window.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
          await act(async () => env.flushTimers());
          assert.ok(!document.querySelector('[role="dialog"]'));
          assert.ok(document.activeElement === modelButton, 'Escape must return focus to the model selector.');
        }
      } finally { await env.close(); }
    });
  }
}

test('a Playground link selects the model group it names, even when it is not a deployment', async () => {
  const env = setup(true, '/playground?model=support-group&mode=chat');
  globalThis.fetch = async () => json(modelList(true));
  try {
    await act(async () => env.render());
    assert.match(document.body.textContent || '', /support-group/);
    assert.ok(document.querySelector('[title="Status: unknown"]'), 'the linked group is selected');
    assert.equal(document.querySelector('[title="Status: online"]'), null);
  } finally { await env.close(); }
  const named = setup(true, '/playground?model=Support&mode=chat');
  globalThis.fetch = async () => json(modelList(true));
  try {
    await act(async () => named.render());
    assert.ok(document.querySelector('[title="Status: online"]'), 'a deployment with the same name is used as it is');
    assert.doesNotMatch(document.body.textContent || '', /Model group/);
  } finally { await named.close(); }
});

test('Playground model reads show loading and failure, support retry, and abort on unmount', async () => {
  const env = setup();
  const signals: (AbortSignal | null | undefined)[] = [];
  let finishRead!: (response: Response) => void;
  globalThis.fetch = async (_input, init) => {
    signals.push(init?.signal);
    return new Promise(resolve => { finishRead = resolve; });
  };
  try {
    await act(async () => env.render());
    assert.match(document.querySelector('[role="status"]')?.textContent || '', /Loading models/);
    assert.equal(signals[0]?.aborted, false);
    await act(async () => finishRead(json({ detail: 'Unavailable' }, 503)));
    assert.match(document.querySelector('[role="alert"]')?.textContent || '', /Could not load models/);
    const retry = [...document.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === 'Retry')!;
    await act(async () => retry.click());
    assert.equal(signals[0]?.aborted, true);
    await act(async () => finishRead(json(modelList(null))));
    assert.equal(document.querySelector('[role="alert"]'), null);
    assert.ok(document.querySelector('[title="Status: unknown"]'));
    await act(async () => env.root.render(null));
    assert.equal(signals[1]?.aborted, true);
  } finally { await env.close(); }
});

test('Playground permission denial stays visible and does not display models or retry', async () => {
  const env = setup();
  globalThis.fetch = async () => json({ detail: 'Permission denied' }, 403);
  try {
    await act(async () => env.render());
    assert.match(document.querySelector('[role="alert"]')?.textContent || '', /do not have access/);
    assert.equal(document.querySelector('[title^="Status:"]'), null);
    assert.ok(![...document.querySelectorAll('button')].some(button => button.textContent === 'Retry'));
  } finally { await env.close(); }
});

test('Playground requests streaming usage and keeps the complete reply and exact token counts', async () => {
  const env = setup();
  let engine!: ChatEngine;
  const requests: RequestInit[] = [];
  const bodies: Record<string, unknown>[] = [];
  globalThis.fetch = async (_input, init) => {
    assert.ok(init);
    requests.push(init);
    const body: unknown = JSON.parse(String(init.body));
    assert.ok(body && typeof body === 'object' && !Array.isArray(body));
    const payload = body as Record<string, unknown>;
    bodies.push(payload);
    const options = payload.stream_options as { include_usage?: boolean } | undefined;
    const chunks = [{ choices: [{ delta: { content: 'OK' } }] }] as unknown[];
    // A provider sends the usage chunk only when the client requests it.
    if (options?.include_usage === true) {
      chunks.push({ choices: [], usage: { prompt_tokens: 5, completion_tokens: 1, total_tokens: 6 } });
    }
    const stream = chunks.map(chunk => `data: ${JSON.stringify(chunk)}\n\n`).join('') + 'data: [DONE]\n\n';
    return new Response(stream, { headers: { 'content-type': 'text/event-stream' } });
  };
  function Probe() {
    const value = useChatEngine({
      apiKey: 'unit-test-only',
      selectedModel: { id: 'dep-1', name: 'Support', provider: 'openai', status: 'online', mode: 'chat' },
    });
    useEffect(() => { engine = value; }, [value]);
    return null;
  }
  try {
    await act(async () => env.root.render(<Probe />));
    await act(async () => engine.setInput('Reply with OK.'));
    await act(async () => engine.handleSend());
    assert.equal(engine.lastStats?.promptTokens, 5);
    assert.equal(engine.lastStats?.completionTokens, 1);
    assert.equal(engine.lastStats?.totalTokens, 6);
    assert.equal(engine.messages.at(-1)?.content, 'OK');
    assert.equal(engine.isStreaming, false);
    assert.equal(engine.error, null);
    assert.equal(requests.length, 1, 'Do not retry an inference POST.');
    assert.equal(requests[0].signal?.aborted, false);
    assert.equal(bodies[0].stream, true);
    assert.deepEqual(bodies[0].stream_options, { include_usage: true });
  } finally { await env.close(); }
});
