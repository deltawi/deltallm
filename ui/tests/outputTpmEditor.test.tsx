import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { AuthProvider, useAuth } from '../src/lib/auth';
import RuntimeOutputTpmEditor from '../src/components/admin/RuntimeOutputTpmEditor';
import type { RuntimeUserProfile } from '../src/lib/api';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

for (const width of [375, 1024]) {
  for (const allowed of [false, true]) {
    test(`output limit permission, pending state, and server denial at ${width}px, allowed=${allowed}`, async () => {
      const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
      Object.defineProperty(dom.window, 'innerWidth', { value: width });
      for (const method of ['attachEvent', 'detachEvent']) {
        Object.defineProperty(dom.window.HTMLElement.prototype, method, {
          configurable: true, value: () => undefined,
        });
      }
      const previous = Object.getOwnPropertyDescriptors(globalThis);
      for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document,
        HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true })) {
        Object.defineProperty(globalThis, key, { configurable: true, value });
      }
      const originalFetch = globalThis.fetch;
      const updates: unknown[] = [];
      const saved: Array<number | null> = [];
      let resolveUpdate: ((response: Response) => void) | undefined;
      globalThis.fetch = async (input, init) => {
        const url = new URL(String(input), 'http://localhost');
        if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'session',
          role: 'org_user', effective_permissions: allowed ? ['user.update'] : [] });
        assert.equal(url.pathname, '/ui/api/users/runtime-output');
        assert.equal(init?.method, 'PUT');
        updates.push(JSON.parse(String(init?.body)));
        return new Promise<Response>((resolve) => { resolveUpdate = resolve; });
      };
      const root = createRoot(document.getElementById('root')!);
      const user = { user_id: 'runtime-output', output_tpm_limit: 100 } as RuntimeUserProfile;
      try {
        await act(async () => root.render(<AuthProvider>
          <RuntimeOutputTpmEditor user={user} onSaved={(limit) => saved.push(limit)} />
        </AuthProvider>));
        const input = document.querySelector<HTMLInputElement>('input');
        const button = document.querySelector<HTMLButtonElement>('button');
        assert.equal(Boolean(input), allowed);
        assert.equal(Boolean(button), allowed);
        if (!input || !button) {
          assert.equal(updates.length, 0);
          return;
        }
        assert.equal(document.querySelector('label')?.htmlFor, input.id);
        assert.equal(input.value, '100');
        input.focus();
        assert.equal(document.activeElement, input);
        await act(async () => { button.click(); button.click(); });
        assert.deepEqual(updates, [{ output_tpm_limit: 100 }]);
        assert.equal(button.disabled, true);
        assert.equal(input.disabled, true);
        await act(async () => resolveUpdate?.(json({ detail: 'Insufficient permissions' }, 403)));
        assert.equal(button.disabled, false);
        assert.equal(input.disabled, false);
        const alert = document.querySelector('[role="alert"]');
        assert.ok(alert?.textContent?.includes('Insufficient permissions'));
        assert.ok(input.getAttribute('aria-describedby')?.split(' ').includes(alert.id));
        assert.equal(input.getAttribute('aria-invalid'), 'true');
        assert.deepEqual(saved, []);
        await act(async () => button.click());
        await act(async () => resolveUpdate?.(json({ user_id: user.user_id, output_tpm_limit: 100 })));
        assert.deepEqual(saved, [100]);
        assert.equal(document.querySelector('[role="alert"]'), null);
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

for (const width of [375, 1024]) {
  for (const change of ['unmount', 'user', 'principal', 'session'] as const) {
    for (const status of [200, 403]) {
      test(`late output save is ignored after ${change}, status=${status}, at ${width}px`, async () => {
        const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
        Object.defineProperty(dom.window, 'innerWidth', { value: width });
        const previous = Object.getOwnPropertyDescriptors(globalThis);
        const globals = { window: dom.window, document: dom.window.document,
          HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true };
        for (const [key, value] of Object.entries(globals)) {
          Object.defineProperty(globalThis, key, { configurable: true, value });
        }
        const originalFetch = globalThis.fetch;
        const requests: Array<{ signal: AbortSignal; resolve: (response: Response) => void }> = [];
        const saved: Array<number | null> = [];
        let accountId = 'principal-a';
        let user = { user_id: 'runtime-a', output_tpm_limit: 100 } as RuntimeUserProfile;
        let refreshSession = async () => {};
        function Editor() {
          const refresh = useAuth().refreshSession;
          useEffect(() => { refreshSession = refresh; }, [refresh]);
          return <RuntimeOutputTpmEditor user={user} onSaved={(limit) => saved.push(limit)} />;
        }
        globalThis.fetch = async (input, init) => {
          if (String(input).endsWith('/auth/me')) return json({ authenticated: true,
            auth_mode: 'session', account_id: accountId, effective_permissions: ['user.update'] });
          assert.ok(init?.signal instanceof AbortSignal);
          return new Promise<Response>((resolve) => requests.push({ signal: init.signal as AbortSignal, resolve }));
        };
        const root = createRoot(document.getElementById('root')!);
        let mounted = true;
        try {
          await act(async () => root.render(<AuthProvider><Editor /></AuthProvider>));
          await act(async () => document.querySelector<HTMLButtonElement>('button')!.click());
          assert.equal(requests.length, 1);
          if (change === 'unmount') {
            await act(async () => root.unmount());
            mounted = false;
          } else if (change === 'user') {
            user = { user_id: 'runtime-b', output_tpm_limit: 200 } as RuntimeUserProfile;
            await act(async () => root.render(<AuthProvider><Editor /></AuthProvider>));
            assert.equal(document.querySelector<HTMLInputElement>('input')?.value, '200');
          } else {
            if (change === 'principal') accountId = 'principal-b';
            await act(async () => refreshSession());
          }
          assert.equal(requests[0].signal.aborted, true);
          if (mounted) {
            await act(async () => document.querySelector<HTMLButtonElement>('button')!.click());
            assert.equal(requests.length, 2);
          }
          await act(async () => requests[0].resolve(json(status === 200
            ? { user_id: 'runtime-a', output_tpm_limit: 100 }
            : { detail: 'Stale permission error' }, status)));
          assert.deepEqual(saved, []);
          assert.equal(document.querySelector('[role="alert"]'), null);
          if (mounted) {
            assert.equal(document.querySelector<HTMLButtonElement>('button')?.disabled, true);
            const expected = change === 'user' ? 200 : 100;
            await act(async () => requests[1].resolve(json({ user_id: user.user_id, output_tpm_limit: expected })));
            assert.deepEqual(saved, [expected]);
            assert.equal(document.querySelector<HTMLButtonElement>('button')?.disabled, false);
          }
        } finally {
          if (mounted) await act(async () => root.unmount());
          globalThis.fetch = originalFetch;
          dom.window.close();
          for (const key of Object.keys(globals)) {
            if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
            else Reflect.deleteProperty(globalThis, key);
          }
        }
      });
    }
  }
}
