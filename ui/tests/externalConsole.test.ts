import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { apiFetch, ApiError, structuredApiErrorDetail } from '../src/lib/api/transport';
import { resolveSession } from '../src/lib/authController';
import { isValidSessionPayload } from '../src/lib/authSession';
import { sessionPrincipal } from '../src/lib/authTypes';
import { inferenceFetch } from '../src/lib/inferenceTransport';
import { mountedPath, parseUIMount } from '../src/lib/uiMount';

const config = { mount_path: '/gateway', external_console: true };
const session = { authenticated: true, auth_mode: 'session' as const,
  session_source: 'external_customer' as const, account_id: 'account',
  expires_at: '2026-10-06T12:00:00Z', workspace: { integration_id: 'console',
    binding_id: 'binding', organization_id: 'org', team_id: 'team', inference_user_id: 'user' } };

function mountConsole() {
  const dom = new JSDOM(`<script id="deltallm-ui-config" type="application/json">${JSON.stringify(config)}</script>`);
  const old = globalThis.document;
  Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
  return () => { dom.window.close(); Object.defineProperty(globalThis, 'document', { configurable: true, value: old }); };
}

test('mount configuration rejects foreign paths and incomplete Console settings', () => {
  for (const mount_path of ['//evil.test', '/gateway/', '/a/../b', '/a?b', '/a%2fb', '/a\\b']) {
    assert.throws(() => parseUIMount({ ...config, mount_path }));
  }
  assert.throws(() => parseUIMount({ ...config, mount_path: '' }));
  assert.throws(() => parseUIMount({ ...config, master_key: 'secret' }));
  assert.equal(mountedPath('/auth/me', config), '/gateway/auth/me');
  assert.throws(() => mountedPath('https://evil.test', config));
  assert.throws(() => mountedPath('//evil.test', config));
});

test('customer session contract rejects operator authentication and incomplete scope', () => {
  const restore = mountConsole();
  try {
    assert.equal(isValidSessionPayload(session), true);
    assert.equal(isValidSessionPayload({ authenticated: false }), true);
    for (const value of [ { authenticated: true, auth_mode: 'master_key' },
      { ...session, session_source: 'operator' }, { ...session, workspace: null },
      { ...session, expires_at: 'invalid' }, { ...session, account_id: null },
      { ...session, workspace: { ...session.workspace, team_id: '' } } ]) {
      assert.equal(isValidSessionPayload(value), false);
    }
    assert.notEqual(sessionPrincipal(session), sessionPrincipal({ ...session,
      workspace: { ...session.workspace, binding_id: 'another' } }));
  } finally { restore(); }
});

test('anonymous or unavailable Console session never attempts master login', async () => {
  const restore = mountConsole();
  let calls = 0;
  const masterLogin = async () => { calls += 1; return {}; };
  try {
    assert.deepEqual(await resolveSession({ me: async () => ({ authenticated: false }), masterLogin }, 'legacy', true), { authenticated: false });
    await assert.rejects(resolveSession({ me: async () => { throw new ApiError('unavailable', 503); }, masterLogin }, 'legacy', true));
    await assert.rejects(resolveSession({ me: async () => ({ authenticated: true, auth_mode: 'master_key' }), masterLogin }, 'legacy', true));
    assert.equal(calls, 0);
  } finally { restore(); }
});

test('Console traffic uses same-origin mount and never sends inference bearer from browser', async () => {
  const restore = mountConsole();
  const oldFetch = globalThis.fetch;
  const requests: Array<{ path: string; options: RequestInit }> = [];
  globalThis.fetch = async (input, options) => {
    requests.push({ path: String(input), options: options || {} });
    return new Response('{}', { headers: { 'content-type': 'application/json' } });
  };
  try {
    await apiFetch('/auth/me');
    assert.equal(requests[0].path, '/gateway/auth/me');
    await assert.rejects(apiFetch('/ui/api/models', { headers: { 'X-Master-Key': 'secret' } }));
    await inferenceFetch('/v1/chat/completions', 'console-selected-key', { method: 'POST', headers: { Authorization: 'Bearer forbidden' } });
    assert.equal(requests[1].path, '/gateway/v1/chat/completions');
    assert.equal(new Headers(requests[1].options.headers).has('Authorization'), false);
    assert.equal(requests[1].options.credentials, 'same-origin');
  } finally { globalThis.fetch = oldFetch; restore(); }
});

test('transport preserves structured external errors and Retry-After', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: 'external_auth_unavailable', message: 'Temporarily unavailable' } }),
    { status: 503, headers: { 'content-type': 'application/json', 'retry-after': '1' } });
  try {
    await assert.rejects(apiFetch('/auth/me'), (error: unknown) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.retryAfterSeconds, 1);
      assert.equal(error.message, 'Temporarily unavailable');
      assert.equal(structuredApiErrorDetail(error)?.code, 'external_auth_unavailable');
      return true;
    });
  } finally { globalThis.fetch = oldFetch; }
});
