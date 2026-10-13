import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { AuthProvider } from '../src/lib/auth';
import { useAuth, type AuthContextValue } from '../src/lib/authContext';
import { apiFetch } from '../src/lib/api/transport';

const session = {
  authenticated: true,
  auth_mode: 'session',
  session_source: 'operator',
  account_id: 'operator-one',
  expires_at: '2030-10-06T12:00:00Z',
  idle_expires_at: '2030-10-06T01:00:00Z',
  idle_timeout_seconds: 3600,
  server_time: '2030-10-06T00:00:00Z',
};

function browser() {
  const dom = new JSDOM('<div id="root"></div>', { url: 'https://gateway.example.com/models', pretendToBeVisual: true });
  const old = { window: globalThis.window, document: globalThis.document,
    sessionStorage: globalThis.sessionStorage, fetch: globalThis.fetch,
    IS_REACT_ACT_ENVIRONMENT: (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT };
  Object.defineProperties(globalThis, { window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document }, sessionStorage: { configurable: true, value: dom.window.sessionStorage },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true } });
  const root = createRoot(document.getElementById('root')!);
  return { dom, root, async close() { await act(async () => root.unmount()); dom.window.close();
    for (const [key, value] of Object.entries(old)) Object.defineProperty(globalThis, key, { configurable: true, value }); } };
}

function json(value: object, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
}

type Call = { path: string; background: boolean };

async function mount(view: ReturnType<typeof browser>, respond: (call: Call, index: number) => Response | Promise<Response>) {
  const calls: Call[] = [];
  globalThis.fetch = async (input, init) => {
    const call = { path: String(input), background: new Headers(init?.headers).get('X-DeltaLLM-Background') === '1' };
    calls.push(call);
    return respond(call, calls.length - 1);
  };
  const state: { current: AuthContextValue | null } = { current: null };
  function Probe() {
    const auth = useAuth();
    useEffect(() => { state.current = auth; }, [auth]);
    return <div>{auth.authStatus}</div>;
  }
  await act(async () => view.root.render(createElement(AuthProvider, {}, createElement(Probe))));
  return { calls, auth: () => state.current! };
}

test('a 401 mid-page re-checks the session in the background and signs out', async () => {
  const view = browser();
  try {
    const { calls, auth } = await mount(view, ({ path }, index) => {
      if (path === '/auth/me') return json(index === 0 ? session : { authenticated: false });
      return json({ detail: 'Authentication required' }, 401);
    });
    assert.equal(auth().authStatus, 'authenticated');
    await act(async () => { await assert.rejects(apiFetch('/ui/api/models')); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.equal(auth().authStatus, 'anonymous');
    assert.equal(auth().signedOutReason, 'ended');
    assert.deepEqual(calls.map((call) => [call.path, call.background]), [
      ['/auth/me', false],
      ['/ui/api/models', false],
      ['/auth/me', true],
    ]);
  } finally { await view.close(); }
});

test('a 401 while the session is still valid keeps the user signed in', async () => {
  const view = browser();
  try {
    const { auth } = await mount(view, ({ path }) => (
      path === '/auth/me' ? json(session) : json({ detail: 'Authentication required' }, 401)
    ));
    await act(async () => { await assert.rejects(apiFetch('/ui/api/models')); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.equal(auth().authStatus, 'authenticated');
    assert.equal(auth().signedOutReason, null);
  } finally { await view.close(); }
});

test('returning to the tab checks the session; a failed check keeps the page', async () => {
  const view = browser();
  try {
    let meCalls = 0;
    const { calls, auth } = await mount(view, () => {
      meCalls += 1;
      if (meCalls === 1) return json(session);
      if (meCalls === 2) throw new TypeError('network down');
      return json({ authenticated: false });
    });
    await act(async () => {
      view.dom.window.dispatchEvent(new view.dom.window.Event('focus'));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.equal(auth().authStatus, 'authenticated');
    assert.equal(calls.length, 2);
    assert.equal(calls[1].background, true);

    // Checks triggered by focus are throttled.
    await act(async () => {
      view.dom.window.dispatchEvent(new view.dom.window.Event('focus'));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.equal(calls.length, 2);
  } finally { await view.close(); }
});

test('signing in again clears the sign-out reason', async () => {
  const view = browser();
  try {
    let signedIn = true;
    const { auth } = await mount(view, ({ path }) => {
      if (path === '/auth/internal/login') { signedIn = true; return json({ ok: true }); }
      if (path === '/auth/me') return json(signedIn ? session : { authenticated: false });
      signedIn = false;
      return json({ detail: 'Authentication required' }, 401);
    });
    await act(async () => { await assert.rejects(apiFetch('/ui/api/models')); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.equal(auth().signedOutReason, 'ended');
    await act(async () => { await auth().loginWithCredentials('operator@example.com', 'secret'); });
    assert.equal(auth().authStatus, 'authenticated');
    assert.equal(auth().signedOutReason, null);
  } finally { await view.close(); }
});

test('a renewal requested during a background check still renews', async () => {
  const view = browser();
  try {
    let releaseBackground!: () => void;
    let meCalls = 0;
    const { calls, auth } = await mount(view, ({ background }) => {
      meCalls += 1;
      if (meCalls === 2 && background) {
        return new Promise<Response>((resolve) => { releaseBackground = () => resolve(json(session)); });
      }
      return json(session);
    });
    let renewal!: Promise<void>;
    await act(async () => {
      view.dom.window.dispatchEvent(new view.dom.window.Event('focus'));
      await new Promise((resolve) => setTimeout(resolve, 0));
      renewal = auth().staySignedIn();
    });
    await act(async () => { releaseBackground(); await renewal; });
    assert.deepEqual(calls.map((call) => call.background), [false, true, false]);
  } finally { await view.close(); }
});

test('a routine check keeps the same session object when only deadlines move', async () => {
  const view = browser();
  try {
    let meCalls = 0;
    const { auth } = await mount(view, () => {
      meCalls += 1;
      return json(meCalls === 1 ? session : { ...session, idle_expires_at: '2030-10-06T01:30:00Z', server_time: '2030-10-06T00:30:00Z' });
    });
    const before = auth().session;
    await act(async () => {
      view.dom.window.dispatchEvent(new view.dom.window.Event('focus'));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.equal(meCalls, 2);
    assert.equal(auth().session, before);
  } finally { await view.close(); }
});
