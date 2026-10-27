import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { JSDOM } from 'jsdom';

import { AuthProvider } from '../src/lib/auth';
import { BrandingContext } from '../src/lib/brandingContext';
import { DEFAULT_BRANDING } from '../src/lib/branding';
import { ToastProvider } from '../src/components/ToastProvider';
import ModelDetail from '../src/pages/ModelDetail';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

const MODEL = {
  deployment_id: 'dep-1', model_name: 'gpt-4o', api_model_id: 'gpt-4o', display_name: 'GPT-4o', provider: 'openai', mode: 'chat',
  healthy: false, health_status: 'unhealthy',
  health: { healthy: false, in_cooldown: false, consecutive_failures: 3, last_error: 'Upstream timed out', last_error_at: 1_700_000_000, last_success_at: null },
  connection_summary: { api_base: 'https://api.openai.com/v1' }, credential_source: 'named', named_credential_name: 'openai-prod',
  deltallm_params: { model: 'openai/gpt-4o', rpm: 5000, timeout: 60, override_params: { temperature: 0.2 }, forward_headers: ['x-trace'] },
  model_info: { mode: 'chat', max_tokens: 128000, input_cost_per_token: 0.0000025, output_cost_per_token: 0.00001, default_params: { top_p: 1 }, weight: 3 },
};

async function withPage(run: (root: HTMLElement) => Promise<void>) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document,
    HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true,
    requestAnimationFrame: (callback: () => void) => setTimeout(callback, 0), cancelAnimationFrame: clearTimeout })) {
    Object.defineProperty(globalThis, key, { configurable: true, value });
  }
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'session', role: 'platform_admin', ui_access: { models: true, model_admin: true } });
    if (url.pathname === '/ui/api/models/dep-1') return json(MODEL);
    return json({ data: [], pagination: { total: 0, limit: 50, offset: 0, has_more: false } });
  };
  const element = document.getElementById('root')!;
  const root = createRoot(element);
  try {
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/models/dep-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider><ToastProvider><BrandingContext.Provider value={{ branding: DEFAULT_BRANDING,
          assetRevision: 0, refreshBranding: async () => DEFAULT_BRANDING, setBranding: () => {} }}>
          <Routes><Route path="/models/:deploymentId" element={<ModelDetail />} /></Routes>
        </BrandingContext.Provider></ToastProvider></AuthProvider>
      </MemoryRouter>,
    ));
    await run(element);
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    dom.window.close();
    for (const key of ['window', 'document', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT', 'requestAnimationFrame', 'cancelAnimationFrame']) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
}

const byText = (text: string, selector = 'button') =>
  [...document.querySelectorAll<HTMLElement>(selector)].find((item) => item.textContent?.trim() === text);

test('model detail renders only the open tab and links the meta line to tabs', async () => {
  await withPage(async (root) => {
    const text = () => root.textContent ?? '';
    assert.ok(text().includes('GPT-4o'));
    assert.ok(text().includes('Unhealthy · 3 failed checks'));
    assert.ok(text().includes('$2.50 / $10.00 per 1M tokens'));
    // Connection is open: health error, credential and request shaping rules show; other tabs are not rendered yet.
    assert.ok(text().includes('Upstream timed out'));
    assert.ok(text().includes('openai-prod'));
    assert.ok(text().includes('x-trace'));
    assert.equal(document.getElementById('model-tabs-panel-pricing')?.childElementCount, 0);
    assert.equal(document.getElementById('model-tabs-panel-settings')?.childElementCount, 0);

    const priceLink = document.querySelector<HTMLButtonElement>('button[aria-label^="$2.50 / $10.00 per 1M tokens"]');
    assert.ok(priceLink);
    await act(async () => priceLink.click());
    const pricing = document.getElementById('model-tabs-panel-pricing')!;
    assert.equal(pricing.hidden, false);
    assert.ok(pricing.textContent?.includes('$10.00'));
    // The Connection panel stays mounted but hidden.
    assert.equal(document.getElementById('model-tabs-panel-connection')?.hidden, true);

    await act(async () => byText('Settings')!.click());
    const settings = document.getElementById('model-tabs-panel-settings')!;
    assert.ok(settings.textContent?.includes('Delete model'));
    assert.ok(settings.textContent?.includes('dep-1'));

    await act(async () => byText('Use this model')!.click());
    const dialog = document.querySelector('[role="dialog"]');
    assert.ok(dialog?.textContent?.includes('"model":"gpt-4o"'));
    assert.ok(dialog?.textContent?.includes('top_p 1'));
  });
});
