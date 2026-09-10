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

for (const width of [375, 1024]) {
  for (const admin of [false, true]) {
    test(`model health permission and server denial at ${width}px, admin=${admin}`, async () => {
      const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
      Object.defineProperty(dom.window, 'innerWidth', { value: width });
      const previous = Object.getOwnPropertyDescriptors(globalThis);
      for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document,
        HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true })) {
        Object.defineProperty(globalThis, key, { configurable: true, value });
      }
      const originalFetch = globalThis.fetch;
      let checks = 0;
      globalThis.fetch = async (input) => {
        const url = new URL(String(input), 'http://localhost');
        if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'session',
          role: admin ? 'platform_admin' : 'org_user', ui_access: { models: true, model_admin: admin } });
        if (url.pathname.endsWith('/health-check')) {
          checks += 1;
          return json({ detail: 'Insufficient permissions' }, 403);
        }
        return json({ deployment_id: 'dep-1', model_name: 'Visible model', provider: 'deepseek',
          healthy: true, mode: 'chat', deltallm_params: { model: 'deepseek/example' }, model_info: {},
          health: { healthy: true, in_cooldown: false }, connection_summary: {} });
      };
      const root = createRoot(document.getElementById('root')!);
      try {
        await act(async () => root.render(
          <MemoryRouter initialEntries={['/models/dep-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <AuthProvider><ToastProvider><BrandingContext.Provider value={{ branding: DEFAULT_BRANDING,
              assetRevision: 0, refreshBranding: async () => DEFAULT_BRANDING, setBranding: () => {} }}>
              <Routes><Route path="/models/:deploymentId" element={<ModelDetail />} /></Routes>
            </BrandingContext.Provider></ToastProvider></AuthProvider>
          </MemoryRouter>,
        ));
        assert.ok(document.body.textContent?.includes('Visible model'));
        const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === 'Check Health');
        assert.equal(Boolean(button), admin);
        if (button) {
          button.focus();
          assert.equal(document.activeElement, button);
          await act(async () => button.click());
          assert.equal(checks, 1);
          assert.ok(document.body.textContent?.includes('Insufficient permissions'));
          assert.ok(document.body.textContent?.includes('Visible model'));
          assert.equal(button.disabled, false);
        } else assert.equal(checks, 0);
      } finally {
        await act(async () => root.unmount());
        globalThis.fetch = originalFetch;
        dom.window.close();
        for (const key of ['window', 'document', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT']) {
          if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
          else Reflect.deleteProperty(globalThis, key);
        }
      }
    });
  }
}
