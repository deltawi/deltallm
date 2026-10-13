import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { AuthProvider } from '../src/lib/auth';
import { ToastProvider } from '../src/components/ToastProvider';
import MCPServers from '../src/pages/MCPServers';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const page = (data: unknown[], total: number) => ({ data, pagination: { total, limit: 20, offset: 0, has_more: false } });

function Location() {
  const location = useLocation();
  return <output id="location">{`${location.pathname}${location.search}`}</output>;
}

type Options = { approver: boolean; pendingCountFails?: boolean };

async function withPage(entry: string, options: Options, run: (ctx: { requests: URL[] }) => Promise<void>) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { configurable: true, value() {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { configurable: true, value() {} });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
    requestAnimationFrame: (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0),
  };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const previousFetch = globalThis.fetch;
  const requests: URL[] = [];
  globalThis.fetch = async (input) => {
    const url = new URL(String(input), 'http://localhost');
    requests.push(url);
    if (url.pathname === '/auth/me') {
      return json(options.approver
        ? { authenticated: true, auth_mode: 'session', account_id: 'admin', role: 'platform_admin' }
        : { authenticated: true, auth_mode: 'session', account_id: 'reader', role: 'org_user', ui_access: { mcp_servers: true, mcp_approvals: false } });
    }
    if (url.pathname === '/ui/api/mcp-approval-requests') {
      if (url.searchParams.get('limit') === '1') {
        if (options.pendingCountFails) return json({ detail: 'unavailable' }, 503);
        return json(page([], url.searchParams.get('server_id') ? 1 : 3));
      }
      return json(page([{
        mcp_approval_request_id: 'apr-1',
        mcp_server_id: 'srv-1',
        tool_name: 'github.create_issue',
        scope_type: 'team',
        scope_id: 'team-1',
        status: 'approved',
        consumed_at: '2026-10-01T10:02:00Z',
        request_fingerprint: 'fp',
        server: { mcp_server_id: 'srv-1', server_key: 'github', name: 'GitHub' },
        capabilities: { can_decide: false },
      }], 1));
    }
    if (url.pathname === '/ui/api/mcp-servers/srv-1') {
      return json({ server: { mcp_server_id: 'srv-1', server_key: 'github', name: 'GitHub Cloud' }, tools: [], bindings: [], tool_policies: [] });
    }
    return json(page([], 0));
  };
  const root = createRoot(document.getElementById('root')!);
  try {
    await act(async () => root.render(
      <MemoryRouter initialEntries={[entry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider><ToastProvider><Location /><MCPServers /></ToastProvider></AuthProvider>
      </MemoryRouter>,
    ));
    await run({ requests });
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = previousFetch;
    dom.window.close();
    for (const key of Object.keys(values)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
}

const tabs = (label: string) => [...document.querySelectorAll(`[role="tablist"][aria-label="${label}"] [role="tab"]`)];
const location = () => document.getElementById('location')?.textContent;

test('the approvals tab opens from the URL, shows the pending count, and filters by server', async () => {
  await withPage('/mcp-servers?tab=approvals&server_id=srv-1', { approver: true }, async ({ requests }) => {
    const pageTabs = tabs('MCP sections');
    assert.deepEqual(pageTabs.map((tab) => tab.textContent), ['Servers', 'Approvals3']);
    assert.equal(pageTabs[1].getAttribute('aria-selected'), 'true');
    assert.ok(document.getElementById('mcp-panel-approvals'));

    const statusTabs = tabs('Approval status');
    assert.deepEqual(statusTabs.map((tab) => tab.textContent), ['Pending', 'Approved', 'Rejected', 'Expired', 'All']);
    assert.equal(statusTabs[0].getAttribute('aria-selected'), 'true');

    const list = requests.find((url) => url.pathname === '/ui/api/mcp-approval-requests' && url.searchParams.get('limit') === '20');
    assert.equal(list?.searchParams.get('status'), 'pending');
    assert.equal(list?.searchParams.get('server_id'), 'srv-1');

    // The tab badge counts all servers. The notice counts the filtered server only.
    assert.ok(document.body.textContent?.includes('1 request waits for a decision.'));
    assert.ok(document.body.textContent?.includes('Approved · used'));
    assert.ok(document.body.textContent?.includes('Server: GitHub Cloud'));
    assert.equal(document.querySelectorAll('.bg-amber-50').length, 0, 'no second pending banner');

    await act(async () => (document.querySelector('[aria-label="Remove server filter"]') as HTMLButtonElement).click());
    assert.equal(location(), '/mcp-servers?tab=approvals');
    assert.ok(document.body.textContent?.includes('3 requests wait for a decision.'));

    const before = requests.filter((url) => url.searchParams.get('limit') === '1').length;
    await act(async () => (document.querySelector('[aria-label="Refresh approval requests"]') as HTMLButtonElement).click());
    assert.ok(requests.filter((url) => url.searchParams.get('limit') === '1').length > before, 'refresh reloads the pending count');

    await act(async () => (statusTabs[4] as HTMLButtonElement).click());
    assert.equal(location(), '/mcp-servers?tab=approvals&status=all');

    await act(async () => (pageTabs[0] as HTMLButtonElement).click());
    assert.equal(location(), '/mcp-servers');
    assert.ok(document.querySelector('[aria-label="Search servers"]'));
  });
});

test('a failed pending count shows no number on the tab', async () => {
  await withPage('/mcp-servers', { approver: true, pendingCountFails: true }, async () => {
    assert.deepEqual(tabs('MCP sections').map((tab) => tab.textContent), ['Servers', 'Approvals']);
    assert.ok(document.querySelector('[aria-label="Search servers"]'));
  });
});

test('a user without approval access sees the servers tab only', async () => {
  await withPage('/mcp-servers?tab=approvals', { approver: false }, async ({ requests }) => {
    assert.equal(tabs('MCP sections').length, 0);
    assert.ok(document.querySelector('[aria-label="Search servers"]'));
    assert.equal(requests.some((url) => url.pathname === '/ui/api/mcp-approval-requests'), false);
  });
});
