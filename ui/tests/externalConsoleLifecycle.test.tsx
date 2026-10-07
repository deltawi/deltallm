import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { AuthProvider } from '../src/lib/auth';
import { useAuth, type AuthContextValue } from '../src/lib/authContext';
import { useApi } from '../src/lib/hooks';

const session = { authenticated: true, auth_mode: 'session', session_source: 'external_customer',
  account_id: 'account-one', expires_at: '2030-10-06T12:00:00Z', workspace: {
    integration_id: 'console', binding_id: 'binding', organization_id: 'org', team_id: 'team', inference_user_id: 'user' } };

function browser() {
  const dom = new JSDOM('<div id="root"></div><script id="deltallm-ui-config" type="application/json">{"mount_path":"/gateway","external_console":true}</script>', { url: 'https://console.example.com/gateway/' });
  const old = { window: globalThis.window, document: globalThis.document,
    sessionStorage: globalThis.sessionStorage, fetch: globalThis.fetch,
    IS_REACT_ACT_ENVIRONMENT: (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT };
  Object.defineProperties(globalThis, { window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document }, sessionStorage: { configurable: true, value: dom.window.sessionStorage },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true } });
  const root = createRoot(document.getElementById('root')!);
  return { root, async close() { await act(async () => root.unmount()); dom.window.close();
    for (const [key, value] of Object.entries(old)) Object.defineProperty(globalThis, key, { configurable: true, value }); } };
}

function json(value: object) { return new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } }); }

test('Console logout wins over a stale session check and clears saved master credentials', async () => {
  const view = browser();
  let context!: AuthContextValue;
  let completeRefresh!: (response: Response) => void;
  let checks = 0;
  const paths: string[] = [];
  globalThis.fetch = async (input) => {
    const path = String(input); paths.push(path);
    if (path.endsWith('/logout')) return json({ logged_out: true });
    if (++checks === 1) return json(session);
    return new Promise((resolve) => { completeRefresh = resolve; });
  };
  const captureAuth = (value: AuthContextValue) => { context = value; };
  function Probe() {
    const auth = useAuth();
    useEffect(() => captureAuth(auth), [auth]);
    return <div>{auth.authStatus}</div>;
  }
  try {
    sessionStorage.setItem('deltallm_master_key', 'legacy-master-secret');
    await act(async () => view.root.render(createElement(AuthProvider, {}, createElement(Probe))));
    assert.equal(context.authStatus, 'authenticated');
    assert.equal(sessionStorage.getItem('deltallm_master_key'), null);
    await assert.rejects(context.loginWithMasterKey('injected-master'));
    await assert.rejects(context.loginWithCredentials('user', 'password'));
    let refresh!: Promise<void>;
    await act(async () => { refresh = context.refreshSession(); });
    await act(async () => context.logout());
    await act(async () => { completeRefresh(json(session)); await refresh; });
    assert.equal(context.authStatus, 'anonymous');
    assert.equal(context.isAuthenticated, false);
    assert.deepEqual(paths, ['/gateway/auth/me', '/gateway/auth/me', '/gateway/auth/internal/logout']);
  } finally { await view.close(); }
});

test('Principal change hides previous data and ignores a read that completes after its abort', async () => {
  const view = browser();
  let context!: AuthContextValue;
  let account = 'account-one';
  const reads: Array<{ signal: AbortSignal; resolve: (value: string) => void }> = [];
  globalThis.fetch = async () => json({ ...session, account_id: account });
  const captureAuth = (value: AuthContextValue) => { context = value; };
  function Probe() {
    const auth = useAuth();
    useEffect(() => captureAuth(auth), [auth]);
    const result = useApi((signal) => new Promise<string>((resolve) => reads.push({ signal, resolve })), []);
    return <div id="value">{result.data || 'empty'}</div>;
  }
  try {
    await act(async () => view.root.render(createElement(AuthProvider, {}, createElement(Probe))));
    const old = reads.at(-1)!;
    await act(async () => { old.resolve('previous-account-data'); });
    assert.equal(document.getElementById('value')?.textContent, 'previous-account-data');
    account = 'account-two';
    await act(async () => context.refreshSession());
    assert.equal(old.signal.aborted, true);
    assert.equal(document.getElementById('value')?.textContent, 'empty');
    const next = reads.at(-1)!;
    await act(async () => { old.resolve('stale-private-data'); next.resolve('new-account-data'); });
    assert.equal(document.getElementById('value')?.textContent, 'new-account-data');
  } finally { await view.close(); }
});
