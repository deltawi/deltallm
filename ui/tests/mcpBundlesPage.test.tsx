import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider } from '../src/lib/auth';
import { ToastProvider } from '../src/components/ToastProvider';
import MCPServers from '../src/pages/MCPServers';
import MCPBundleEditor from '../src/pages/MCPBundleEditor';

type Call = { url: URL; method: string; body: unknown };
type Member = { mcp_server_id: string; all_tools: boolean; tool_names?: string[]; name_prefix?: string | null };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const page = (data: unknown[], total = data.length) => ({ data, pagination: { total, limit: 20, offset: 0, has_more: false } });

const SERVER_TOOLS: Record<string, { server_key: string; name: string; tools: Array<{ name: string; needs_approval?: boolean; disabled_by_policy?: boolean }> }> = {
  'srv-gh': {
    server_key: 'github',
    name: 'GitHub',
    tools: [{ name: 'create_issue' }, { name: 'delete_branch', disabled_by_policy: true }, { name: 'merge_pull_request', needs_approval: true }],
  },
  'srv-jira': { server_key: 'jira', name: 'Jira', tools: [{ name: 'search' }, { name: 'create_issue' }] },
};

const SAVED = {
  mcp_bundle_id: 'b-1',
  bundle_key: 'eng',
  name: 'Engineering',
  description: null,
  owner_scope_type: 'global',
  owner_scope_id: null,
  enabled: true,
  tool_count: 4,
  capabilities: { can_mutate: true },
  members: [
    { mcp_server_id: 'srv-gh', server_key: 'github', server_name: 'GitHub', server_enabled: true, all_tools: true, tool_names: null, name_prefix: 'gh', tool_count: 3, server_tool_count: 3 },
    { mcp_server_id: 'srv-jira', server_key: 'jira', server_name: 'Jira', server_enabled: true, all_tools: false, tool_names: ['search'], name_prefix: null, tool_count: 1, server_tool_count: 2 },
  ],
};

function preview(body: { members?: Member[]; api_key?: string }) {
  const members = body.members ?? [];
  const tools = members.flatMap((member) => SERVER_TOOLS[member.mcp_server_id].tools
    .filter((tool) => member.all_tools || member.tool_names?.includes(tool.name))
    .map((tool) => ({ exposed_name: member.name_prefix ? `${member.name_prefix}_${tool.name}` : tool.name, tool_name: tool.name })));
  return {
    servers: members.map((member) => ({
      mcp_server_id: member.mcp_server_id,
      ...SERVER_TOOLS[member.mcp_server_id],
      enabled: true,
      tools: SERVER_TOOLS[member.mcp_server_id].tools.map((tool) => ({ description: null, needs_approval: false, disabled_by_policy: false, ...tool })),
    })),
    tools: tools.map((tool) => ({ ...tool, server_id: 'x', server_key: 'x', description: null })),
    errors: [],
    key: body.api_key ? {
      api_key: body.api_key,
      team_id: 'team-1',
      organization_id: 'org-1',
      sees_bundle: true,
      reason: null,
      usable_count: 2,
      tools: [
        { name: 'gh_create_issue', access: 'available' },
        { name: 'gh_delete_branch', access: 'disabled_by_policy' },
        { name: 'gh_merge_pull_request', access: 'needs_approval' },
        { name: 'search', access: 'no_access' },
      ],
    } : null,
  };
}

function Location() {
  const location = useLocation();
  return <output id="location">{`${location.pathname}${location.search}`}</output>;
}

async function withApp(entry: string, run: (ctx: { calls: Call[]; dom: JSDOM; copied: string[] }) => Promise<void>, saved = SAVED) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { configurable: true, value() {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { configurable: true, value() {} });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const copied: string[] = [];
  const values = {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    navigator: { userAgent: 'node', clipboard: { writeText: async (text: string) => { copied.push(text); } } },
    IS_REACT_ACT_ENVIRONMENT: true,
    requestAnimationFrame: (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0),
  };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const previousFetch = globalThis.fetch;
  const calls: Call[] = [];
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body });
    if (url.pathname === '/auth/me') return json({ authenticated: true, auth_mode: 'session', account_id: 'admin', role: 'platform_admin' });
    if (url.pathname === '/ui/api/mcp-bundles' && method === 'GET') return json(page([saved]));
    if (url.pathname === '/ui/api/mcp-bundles' && method === 'POST') return json({ ...saved, mcp_bundle_id: 'b-new' });
    if (url.pathname === '/ui/api/mcp-bundles/preview') return json(preview(body));
    if (url.pathname === '/ui/api/mcp-bundles/b-1' && method === 'GET') return json(saved);
    if (url.pathname === '/ui/api/mcp-bundles/b-1' && method === 'PATCH') return json(saved);
    if (url.pathname === '/ui/api/mcp-servers') {
      return json(page(Object.entries(SERVER_TOOLS).map(([id, item]) => ({
        mcp_server_id: id, server_key: item.server_key, name: item.name, enabled: true, tool_count: item.tools.length, owner_scope_type: 'global', owner_scope_id: null,
      }))));
    }
    if (url.pathname === '/ui/api/keys') return json(page([{ token: 'hash-1', key_name: 'ci-bot', team_id: 'team-1', team_alias: 'Platform' }]));
    return json(page([]));
  };
  const root = createRoot(document.getElementById('root')!);
  try {
    await act(async () => root.render(
      <MemoryRouter initialEntries={[entry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider><ToastProvider><Location />
          <Routes>
            <Route path="/mcp-servers" element={<MCPServers />} />
            <Route path="/mcp-servers/bundles/new" element={<MCPBundleEditor />} />
            <Route path="/mcp-servers/bundles/:bundleId" element={<MCPBundleEditor />} />
          </Routes>
        </ToastProvider></AuthProvider>
      </MemoryRouter>,
    ));
    await run({ calls, dom, copied });
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

const wait = (ms: number) => act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); });
const text = () => document.body.textContent ?? '';
const location = () => document.getElementById('location')?.textContent;
const byLabel = <T extends Element = HTMLElement>(label: string) => document.querySelector(`[aria-label="${label}"]`) as T | null;
const buttonWithText = (label: string) => [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === label) as HTMLButtonElement | undefined;
const click = (element: Element | null | undefined) => act(async () => { (element as HTMLElement).click(); });

async function fill(dom: JSDOM, element: HTMLInputElement | null, value: string) {
  await act(async () => {
    element!.focus();
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value')!.set!.call(element, value);
    element!.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    // react-dom loads before the DOM globals, so it watches key events for value changes.
    element!.dispatchEvent(new dom.window.KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
  });
}

test('the bundles tab loads on click and lists bundles with servers and endpoint', async () => {
  await withApp('/mcp-servers', async ({ calls, copied }) => {
    assert.equal(calls.some((call) => call.url.pathname.startsWith('/ui/api/mcp-bundles')), false, 'the servers tab reads no bundles');
    const tab = [...document.querySelectorAll('[role="tab"]')].find((item) => item.textContent === 'Bundles');
    await click(tab);
    assert.equal(location(), '/mcp-servers?tab=bundles');
    assert.ok(text().includes('A bundle shows tools from several servers at one endpoint. It does not give access. A caller sees only the tools that their key can already use.'));
    assert.ok(document.querySelector('[data-testid="bundle-skeleton-row"]') || text().includes('Engineering'));
    await wait(0);
    assert.ok(text().includes('Engineering'));
    const row = [...document.querySelectorAll('tbody tr')].find((item) => item.textContent?.includes('Engineering'))!;
    assert.match(row.textContent ?? '', /github3/);
    assert.match(row.textContent ?? '', /jira1/);
    assert.ok(row.textContent?.includes('/mcp/eng'));
    await click(byLabel('Copy endpoint of eng'));
    assert.deepEqual(copied, ['http://localhost/mcp/eng']);
    assert.equal(location(), '/mcp-servers?tab=bundles', 'the copy button does not open the row');

    await click(buttonWithText('Create bundle'));
    assert.equal(location(), '/mcp-servers/bundles/new');
  });
});

test('create: add servers, see policy badges and conflicts, and save', async () => {
  await withApp('/mcp-servers/bundles/new', async ({ calls, dom }) => {
    await click(buttonWithText('Save bundle'));
    assert.ok(byLabel('Save problems')?.textContent?.includes('Name is required.'));

    await fill(dom, document.getElementById('bundle-name') as HTMLInputElement, 'Eng Assist');
    assert.equal((document.getElementById('bundle-key') as HTMLInputElement).value, 'eng-assist');
    assert.equal(document.querySelector('[data-testid="bundle-endpoint-url"]')?.textContent, 'http://localhost/mcp/eng-assist');

    for (const server of ['GitHub', 'Jira']) {
      await click(buttonWithText('Add server'));
      await wait(0);
      const option = [...document.querySelectorAll('button')].find((item) => item.textContent?.startsWith(server));
      await click(option);
    }
    await wait(450);
    const github = document.querySelector('[data-testid="bundle-member-github"]')!;
    assert.ok(github.textContent?.includes('Disabled by policy'));
    assert.ok(github.textContent?.includes('Needs approval'));
    assert.ok(text().includes('Name conflict'), 'create_issue is on both servers');

    await fill(dom, byLabel<HTMLInputElement>('Name prefix for jira'), 'jira');
    assert.equal(text().includes('Name conflict'), false);
    assert.ok(text().includes('as jira_create_issue'));

    // Clear the github tools, then select one tool.
    await click([...github.querySelectorAll('button')].find((item) => item.textContent === 'Clear'));
    assert.ok(byLabel('Bundle problems')?.textContent?.includes('Select at least one tool of "github".'));
    await click(byLabel('Include create_issue'));
    await wait(450);
    const lastPreview = calls.filter((call) => call.url.pathname === '/ui/api/mcp-bundles/preview').at(-1);
    assert.deepEqual(lastPreview?.body, {
      owner_scope_type: 'global',
      owner_scope_id: null,
      members: [
        { mcp_server_id: 'srv-gh', all_tools: false, tool_names: ['create_issue'], name_prefix: null },
        { mcp_server_id: 'srv-jira', all_tools: true, name_prefix: 'jira' },
      ],
    });

    await click(buttonWithText('Save bundle'));
    const created = calls.find((call) => call.method === 'POST' && call.url.pathname === '/ui/api/mcp-bundles');
    assert.deepEqual(created?.body, {
      bundle_key: 'eng-assist',
      name: 'Eng Assist',
      description: null,
      owner_scope_type: 'global',
      owner_scope_id: null,
      members: lastPreview?.body && (lastPreview.body as { members: Member[] }).members,
    });
    assert.equal(location(), '/mcp-servers?tab=bundles');
  });
});

test('edit: check a key and see why each tool is or is not usable', async () => {
  await withApp('/mcp-servers/bundles/b-1', async ({ calls }) => {
    await wait(0);
    assert.equal((document.getElementById('bundle-key') as HTMLInputElement).disabled, true);
    await click(buttonWithText('Check a key'));
    await wait(300);
    await click([...document.querySelectorAll('button')].find((item) => item.textContent?.startsWith('ci-bot')));
    await wait(450);
    const result = document.querySelector('[data-testid="bundle-key-preview"]')!;
    assert.ok(result.textContent?.includes('This key can use 2 of 4 tools.'));
    assert.ok(result.textContent?.includes('Disabled by policy'));
    assert.ok(result.textContent?.includes('Needs approval'));
    assert.ok(result.textContent?.includes('No access'));
    const keyPreview = calls.filter((call) => call.url.pathname === '/ui/api/mcp-bundles/preview').at(-1);
    assert.equal((keyPreview?.body as { api_key: string; mcp_bundle_id: string }).api_key, 'hash-1');
    assert.equal((keyPreview?.body as { mcp_bundle_id: string }).mcp_bundle_id, 'b-1');

    await click(buttonWithText('Save bundle'));
    const patch = calls.find((call) => call.method === 'PATCH');
    assert.deepEqual(Object.keys(patch?.body as object).sort(), ['description', 'members', 'name']);
  });
});

test('a bundle the user cannot change opens read only', async () => {
  await withApp('/mcp-servers/bundles/b-1', async () => {
    await wait(0);
    assert.ok(text().includes('You can see this bundle, but you cannot change it.'));
    assert.equal(buttonWithText('Save bundle'), undefined);
    assert.equal(buttonWithText('Delete'), undefined);
    assert.equal(buttonWithText('Add server'), undefined);
  }, { ...SAVED, capabilities: { can_mutate: false } });
});
