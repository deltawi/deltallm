import assert from 'node:assert/strict';
import test from 'node:test';
import { act, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../src/lib/auth';
import { ToastProvider } from '../src/components/ToastProvider';
import ConnectedAccounts from '../src/pages/ConnectedAccounts';
import MCPServerForm from '../src/components/mcp/MCPServerForm';
import { EMPTY_MCP_SERVER_FORM } from '../src/lib/mcpServerForm';
import { useMCPConnectionAttention } from '../src/lib/useMCPConnectionAttention';
import { browserNavigation } from '../src/lib/mcpConnections';
import { notifyMCPConnectionsChanged } from '../src/lib/mcpConnectionAttention';
import type { MCPConnectionRow } from '../src/lib/api/mcpOAuth';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const ROWS: MCPConnectionRow[] = [
  {
    server_id: 'srv-1', server_key: 'github', server_name: 'GitHub', status: 'connected', status_reason: null,
    account_label: 'dana-k', scopes: ['repo'], expires_at: '2099-01-01T00:00:00Z', renews_automatically: true,
    connected_at: '2026-10-18T10:00:00Z', connect_available: true,
  },
  {
    server_id: 'srv-2', server_key: 'linear', server_name: 'Linear', status: 'needs_reconnect',
    status_reason: 'The server revoked the token.', account_label: 'dana@example.com', scopes: ['read'],
    expires_at: null, renews_automatically: false, connected_at: '2026-10-01T10:00:00Z', connect_available: true,
  },
  {
    server_id: 'srv-3', server_key: 'notion', server_name: 'Notion', status: 'not_connected', status_reason: null,
    account_label: null, scopes: [], expires_at: null, renews_automatically: false, connected_at: null,
    connect_available: true,
  },
];

type Request = { url: URL; method: string; body: unknown; background: boolean };

async function withDom(
  entry: string,
  element: ReactNode,
  run: (ctx: { requests: Request[]; assigned: string[] }) => Promise<void>,
  options: { rows?: MCPConnectionRow[] } = {},
) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
    requestAnimationFrame: (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0),
  };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { configurable: true, value() {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { configurable: true, value() {} });
  const assigned: string[] = [];
  // jsdom does not navigate; record the target instead.
  const previousAssign = browserNavigation.assign;
  browserNavigation.assign = (url: string) => { assigned.push(url); };
  const previousFetch = globalThis.fetch;
  const requests: Request[] = [];
  let rows = options.rows ?? ROWS;
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method || 'GET';
    requests.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
      background: new Headers(init?.headers).has('X-DeltaLLM-Background'),
    });
    if (url.pathname === '/auth/me') {
      return json({ authenticated: true, auth_mode: 'session', account_id: 'acct-1', role: 'platform_admin', email: 'dana@example.com' });
    }
    if (url.pathname === '/ui/api/mcp-connections' && method === 'GET') return json({ data: rows, account_connectable: true });
    if (url.pathname.endsWith('/connect') && method === 'POST') {
      return json({ authorization_url: 'https://auth.example.com/authorize?state=abc' });
    }
    if (url.pathname.startsWith('/ui/api/mcp-connections/') && method === 'DELETE') {
      rows = rows.map((row) => (row.server_key === 'github' ? { ...row, status: 'not_connected', account_label: null } : row));
      return json({ deleted: true, revoked: true, server_key: 'github' });
    }
    if (url.pathname === '/ui/api/mcp-oauth/settings') {
      return json({ redirect_url: 'https://gw.example.com/mcp/oauth/callback', public_base_url_configured: true, encryption_configured: true });
    }
    if (url.pathname === '/ui/api/mcp-servers/oauth/discover') {
      return json({ discovery: null, discovery_error: 'No OAuth metadata was found.' });
    }
    return json({ detail: 'not found' }, 404);
  };
  const root = createRoot(document.getElementById('root')!);
  try {
    await act(async () => root.render(
      <MemoryRouter initialEntries={[entry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider><ToastProvider>{element}</ToastProvider></AuthProvider>
      </MemoryRouter>,
    ));
    await run({ requests, assigned });
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = previousFetch;
    browserNavigation.assign = previousAssign;
    dom.window.close();
    for (const key of Object.keys(values)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
}

const pageRoutes = (
  <Routes>
    <Route path="/connected-accounts" element={<ConnectedAccounts />} />
    <Route path="/ui/connect/:serverKey" element={<ConnectedAccounts />} />
  </Routes>
);

const buttons = () => [...document.querySelectorAll('button')];
const buttonByText = (text: string) => buttons().find((button) => button.textContent?.trim() === text);
const text = () => document.body.textContent || '';

test('the page lists connected, not connected and connect-again rows', async () => {
  await withDom('/connected-accounts', pageRoutes, async () => {
    const headers = [...document.querySelectorAll('thead th')].map((cell) => cell.textContent);
    assert.deepEqual(headers, ['Server', 'Status', 'Account', 'Access', 'Expires', 'Actions']);
    assert.ok(text().includes('Your connection is used only by API keys that you own.'));
    assert.ok(text().includes('dana-k'));
    assert.ok(text().includes('The server revoked the token.'));
    assert.ok(text().includes('renews automatically'));
    assert.ok(buttonByText('Disconnect'));
    assert.ok(buttonByText('Connect again'));
    assert.ok(buttonByText('Connect'));
  });
});

test('connect opens the authorization URL of the server', async () => {
  await withDom('/connected-accounts', pageRoutes, async ({ requests, assigned }) => {
    await act(async () => buttonByText('Connect')!.click());
    const start = requests.find((request) => request.method === 'POST');
    assert.equal(start?.url.pathname, '/ui/api/mcp-connections/notion/connect');
    assert.deepEqual(assigned, ['https://auth.example.com/authorize?state=abc']);
  });
});

test('disconnect asks for confirmation, then reloads the list', async () => {
  await withDom('/connected-accounts', pageRoutes, async ({ requests }) => {
    const listReads = () => requests.filter((request) => request.url.pathname === '/ui/api/mcp-connections' && request.method === 'GET').length;
    const before = listReads();
    await act(async () => buttonByText('Disconnect')!.click());
    assert.ok(text().includes('revokes your tokens'));
    const confirm = [...document.querySelectorAll('[role="dialog"] button')].find((button) => button.textContent?.trim() === 'Disconnect') as HTMLButtonElement;
    await act(async () => confirm.click());
    assert.ok(requests.some((request) => request.method === 'DELETE' && request.url.pathname === '/ui/api/mcp-connections/github'));
    assert.ok(text().includes('Account disconnected'));
    assert.ok(listReads() > before, 'the list is read again');
    assert.equal(buttonByText('Disconnect'), undefined);
  });
});

test('the connect route marks the row and shows the callback result', async () => {
  await withDom('/ui/connect/github?result=connected', pageRoutes, async () => {
    assert.ok(text().includes('Account connected'));
    assert.ok(text().includes('Your GitHub account is connected.'));
    assert.ok(text().includes('Just connected'));
  });
  await withDom('/ui/connect/notion', pageRoutes, async () => {
    assert.ok(text().includes('Connect your Notion account to use its tools.'));
  });
  await withDom('/ui/connect/github?result=error&reason=wrong_account', pageRoutes, async () => {
    assert.ok(document.querySelector('[role="alert"]')?.textContent?.includes('different account'));
  });
});

function AttentionProbe() {
  const count = useMCPConnectionAttention(true);
  return <output id="attention">{count === null ? 'unknown' : String(count)}</output>;
}

test('the menu badge counts connections that need a new connection', async () => {
  await withDom('/', <AttentionProbe />, async ({ requests }) => {
    assert.equal(document.getElementById('attention')?.textContent, '1');
    const reads = () => requests.filter((request) => request.url.pathname === '/ui/api/mcp-connections');
    // A background read: it must not extend the session.
    assert.ok(reads().length > 0);
    assert.ok(reads().every((request) => request.background));
    const before = reads().length;
    await act(async () => notifyMCPConnectionsChanged());
    assert.equal(reads().length, before + 1);
  });
});

test('the server form shows the OAuth fields and the redirect URL', async () => {
  const form = { ...EMPTY_MCP_SERVER_FORM, auth_mode: 'oauth2' as const, base_url: 'https://mcp.example.com/mcp' };
  await withDom('/', <MCPServerForm value={form} onChange={() => undefined} />, async ({ requests }) => {
    assert.ok(document.querySelector('option[value="oauth2"]')?.textContent?.includes('OAuth 2.0 (per user)'));
    assert.equal((document.getElementById('mcp-oauth-redirect-url') as HTMLInputElement).value, 'https://gw.example.com/mcp/oauth/callback');
    assert.ok(document.querySelector('[aria-label="Copy redirect URL"]'));
    assert.ok(document.getElementById('mcp-oauth-client-id'));
    assert.ok(document.getElementById('mcp-oauth-client-secret'));
    await act(async () => buttonByText('Run discovery')!.click());
    const preview = requests.find((request) => request.url.pathname === '/ui/api/mcp-servers/oauth/discover');
    assert.deepEqual(preview?.body, { base_url: 'https://mcp.example.com/mcp' });
    assert.ok(text().includes('No OAuth metadata was found.'));
    assert.ok(buttonByText('Run again'));
  });
});
