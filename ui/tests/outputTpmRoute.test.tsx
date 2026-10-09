import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { JSDOM } from 'jsdom';
import App from '../src/App';

const json = (body: unknown) => new Response(JSON.stringify(body), {
  headers: { 'content-type': 'application/json' },
});

for (const width of [375, 1024]) {
  test(`API-key route loads and the output control keeps keyboard focus at ${width}px`, async () => {
    const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/keys' });
    Object.defineProperty(dom.window, 'innerWidth', { value: width });
    for (const method of ['attachEvent', 'detachEvent']) {
      Object.defineProperty(dom.window.HTMLElement.prototype, method, {
        configurable: true, value: () => undefined,
      });
    }
    Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetParent', {
      configurable: true, get: () => dom.window.document.body,
    });
    const previous = Object.getOwnPropertyDescriptors(globalThis);
    for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document,
      HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true })) {
      Object.defineProperty(globalThis, key, { configurable: true, value });
    }
    const originalFetch = globalThis.fetch;
    let keyReads = 0;
    globalThis.fetch = async (input) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'session',
        role: 'platform_admin', account_id: 'test-account', ui_access: { keys: true } });
      if (url.pathname === '/ui/api/branding') return json({});
      if (url.pathname === '/ui/api/keys') keyReads += 1;
      return json({ data: [], pagination: { total: 0, limit: 10, offset: 0, has_more: false } });
    };
    const root = createRoot(document.getElementById('root')!);
    try {
      await act(async () => root.render(<MemoryRouter initialEntries={['/keys']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><App /></MemoryRouter>));
      assert.equal(keyReads, 1);
      const create = [...document.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Create Key');
      assert.ok(create);
      create.focus();
      await act(async () => create.click());
      const dialog = document.querySelector('[role="dialog"]');
      assert.ok(dialog);
      const label = [...dialog.querySelectorAll('label')].find((item) => item.textContent === 'Output TPM limit');
      assert.ok(label);
      const input = document.getElementById(label.htmlFor);
      assert.ok(input instanceof dom.window.HTMLInputElement);
      input.focus();
      assert.equal(document.activeElement, input);
      assert.ok(input.getAttribute('aria-describedby'));
      await act(async () => document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
      assert.equal(document.querySelector('[role="dialog"]'), null);
      assert.equal(document.activeElement, create);
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
