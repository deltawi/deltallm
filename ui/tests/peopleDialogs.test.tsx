import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import ProvisionPersonModal from '../src/components/admin/ProvisionPersonModal';
import AccountEditorDialog from '../src/components/people/AccountEditorDialog';
import PersonScopeSelect from '../src/components/people/PersonScopeSelect';
import { ToastProvider } from '../src/components/ToastProvider';
import { rbac, type RBACAccount } from '../src/lib/api';

const account: RBACAccount = { account_id: 'account-1', email: 'alex@example.com', role: 'org_user', is_active: true, mfa_enabled: true, last_login_at: null, created_at: '2026-10-01T00:00:00Z' };
const team = { team_id: 'team-1', team_alias: 'Platform', organization_id: 'org-1', organization_name: 'Acme AI' };
const organization = { organization_id: 'org-1', organization_name: 'Acme AI' };
const pagination = { total: 1, limit: 50, offset: 0, has_more: false };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

function setup() {
  const dom = new JSDOM('<button id="trigger">Open editor</button><div id="root"></div>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const frames: FrameRequestCallback[] = [];
  dom.window.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
  const globals = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator, requestAnimationFrame: dom.window.requestAnimationFrame, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetParent', { configurable: true, get() {
    if (this.closest('[hidden]')) return null;
    if (this.closest('details:not([open])') && !this.closest('summary')) return null;
    return this.parentNode;
  } });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { value: () => {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { value: () => {} });
  const root = createRoot(document.getElementById('root')!);
  const originalFetch = globalThis.fetch;
  const originalProvision = rbac.provisionPerson;
  const originalUpsert = rbac.accounts.upsert;
  globalThis.fetch = async (input) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/ui/api/teams') return json({ data: [team], pagination });
    if (url.pathname === '/ui/api/teams/team-1') return json(team);
    if (url.pathname === '/ui/api/organizations') return json({ data: [organization], pagination });
    if (url.pathname === '/ui/api/organizations/org-1') return json(organization);
    throw new Error(`Unexpected request: ${url.pathname}`);
  };
  return { dom, root, frames: () => { frames.splice(0).forEach(callback => callback(0)); }, cleanup: async () => {
    await act(async () => root.unmount());
    rbac.provisionPerson = originalProvision;
    rbac.accounts.upsert = originalUpsert;
    globalThis.fetch = originalFetch;
    dom.window.close();
    for (const key of Object.keys(globals)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  } };
}

const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === text)!;
const field = (name: string) => document.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-person-field="${name}"]`)!;
function changeValue(element: HTMLInputElement | HTMLSelectElement, value: string) {
  if (element instanceof window.HTMLInputElement) {
    element.focus();
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set?.call(element, value);
    element.dispatchEvent(new window.Event('input', { bubbles: true }));
    element.dispatchEvent(new window.KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
  } else {
    element.value = value;
    element.dispatchEvent(new window.Event('change', { bubbles: true }));
  }
}
const createProps = { open: true, orgList: [organization], teamList: [team], onClose: () => {}, onSuccess: () => {} };

test('creation keeps edits across keyboard tabs and reference refresh and clears secrets on close', async () => {
  const env = setup();
  const trigger = document.getElementById('trigger')!;
  trigger.focus();
  try {
    await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} /></ToastProvider>));
    assert.equal(document.activeElement, field('email'));
    await act(async () => changeValue(field('email'), 'alex@example.com'));
    await act(async () => document.querySelector<HTMLInputElement>('input[value="create_account"]')!.click());
    await act(async () => changeValue(field('password'), 'sample-password-only'));
    await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} orgList={[{ organization_id: 'org-2' }]} teamList={[{ team_id: 'team-2' }]} /></ToastProvider>));
    assert.equal(field('email').value, 'alex@example.com');
    assert.equal(field('password').value, 'sample-password-only');
    const details = document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')!;
    await act(async () => details.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'End', bubbles: true })));
    env.frames();
    assert.equal(document.activeElement?.textContent, 'Access');
    assert.equal(document.querySelector('[role="tabpanel"]:not([hidden])')?.getAttribute('aria-labelledby'), document.activeElement?.id);
    await act(async () => button('Details').click());
    assert.equal(field('password').value, 'sample-password-only');
    await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} open={false} /></ToastProvider>));
    assert.equal(document.activeElement, trigger);
    await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} /></ToastProvider>));
    assert.equal(field('email').value, '');
    assert.equal(document.querySelector('input[type="password"]'), null);
  } finally { await env.cleanup(); }
});

test('invitations validate hidden access, preserve team prefill, prevent duplicate submit, and lock focus while saving', async () => {
  const env = setup();
  let closeCount = 0;
  const requests: Parameters<typeof rbac.provisionPerson>[0][] = [];
  let resolveSave!: (value: Awaited<ReturnType<typeof rbac.provisionPerson>>) => void;
  rbac.provisionPerson = payload => { requests.push(payload); return new Promise(resolve => { resolveSave = resolve; }); };
  try {
    await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} onClose={() => closeCount++} /></ToastProvider>));
    await act(async () => changeValue(field('email'), 'alex@example.com'));
    await act(async () => button('Send invitation').click());
    env.frames();
    assert.equal(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent, 'Access');
    assert.equal(document.activeElement, field('scope'));
    assert.equal(requests.length, 0);
    await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} initialTeamId="team-1" onClose={() => closeCount++} onSuccess={async () => { throw new Error('Refresh failed'); }} /></ToastProvider>));
    await act(async () => changeValue(field('email'), 'alex@example.com'));
    await act(async () => { button('Send invitation').click(); button('Send invitation').click(); });
    assert.deepEqual(requests, [{ email: 'alex@example.com', mode: 'invite_email', platform_role: 'org_user', team_id: 'team-1', team_role: 'team_viewer' }]);
    assert.equal(button('Cancel').disabled, true);
    await act(async () => document.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(closeCount, 0);
    const activeTab = document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')!;
    activeTab.focus();
    await act(async () => activeTab.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })));
    assert.equal(document.activeElement?.getAttribute('aria-label'), 'Close dialog');
    await act(async () => resolveSave({ mode: 'invite_email', email: 'alex@example.com', invitation_id: 'inv-1' }));
    assert.equal(closeCount, 1);
    assert.match(document.body.textContent || '', /Invitation queued for delivery/);
    assert.match(document.body.textContent || '', /list could not be refreshed/);
  } finally { await env.cleanup(); }
});

test('manual creation clears incompatible password and membership when changing method or role', async () => {
  const env = setup();
  const requests: Parameters<typeof rbac.provisionPerson>[0][] = [];
  rbac.provisionPerson = async payload => { requests.push(payload); return { mode: payload.mode, email: payload.email }; };
  try {
    await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} initialOrganizationId="org-1" /></ToastProvider>));
    await act(async () => changeValue(field('email'), 'alex@example.com'));
    await act(async () => document.querySelector<HTMLInputElement>('input[value="create_account"]')!.click());
    await act(async () => changeValue(field('password'), 'short'));
    await act(async () => button('Create account').click());
    assert.equal(requests.length, 0);
    assert.match(document.querySelector('[role="alert"]')?.textContent || '', /12 characters/);
    await act(async () => changeValue(field('password'), 'sample-password-only'));
    await act(async () => document.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await act(async () => button('Access').click());
    await act(async () => changeValue(field('platformRole'), 'platform_admin'));
    assert.equal(document.querySelector('[data-person-field="scope"]'), null);
    await act(async () => button('Create account').click());
    assert.deepEqual(requests[0], { email: 'alex@example.com', mode: 'create_account', platform_role: 'platform_admin', password: 'sample-password-only', is_active: false });
    await act(async () => button('Details').click());
    await act(async () => document.querySelector<HTMLInputElement>('input[value="invite_email"]')!.click());
    assert.equal(document.querySelector('input[type="password"]'), null);
    await act(async () => button('Access').click());
    assert.equal(field('scope').value, 'organization');
    assert.match(document.body.textContent || '', /Email invitations use the Organization User role/);
  } finally { await env.cleanup(); }
});

test('scope errors stay visible, block submission, and can be retried without losing identity', async () => {
  const env = setup();
  let failed = true;
  let requests = 0;
  globalThis.fetch = async input => failed ? json({ detail: 'Permission denied' }, 403) : json(String(input).includes('/team-1') ? team : { data: [team], pagination });
  rbac.provisionPerson = async payload => { requests++; return { mode: payload.mode, email: payload.email }; };
  try {
    await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} initialTeamId="team-1" /></ToastProvider>));
    await act(async () => changeValue(field('email'), 'alex@example.com'));
    await act(async () => button('Send invitation').click());
    assert.equal(requests, 0);
    assert.match(document.body.textContent || '', /Permission denied/);
    assert.equal(field('teamId').disabled, true);
    failed = false;
    await act(async () => button('Retry').click());
    assert.equal(field('email').value, 'alex@example.com');
    await act(async () => button('Send invitation').click());
    assert.equal(requests, 1);
  } finally { await env.cleanup(); }
});

for (const kind of ['team', 'organization'] as const) {
  test(`a missing ${kind} blocks submission but permits a valid replacement`, async () => {
    const env = setup();
    const requests: Parameters<typeof rbac.provisionPerson>[0][] = [];
    const validId = kind === 'team' ? team.team_id : organization.organization_id;
    const prefill = kind === 'team' ? { initialTeamId: 'deleted-team' } : { initialOrganizationId: 'deleted-organization' };
    globalThis.fetch = async (input) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname.endsWith(`/deleted-${kind}`)) return json({ detail: `${kind} not found` }, 404);
      if (url.pathname === '/ui/api/teams') return json({ data: [team], pagination });
      if (url.pathname === '/ui/api/organizations') return json({ data: [organization], pagination });
      throw new Error(`Unexpected request: ${url.pathname}`);
    };
    rbac.provisionPerson = async payload => { requests.push(payload); return { mode: payload.mode, email: payload.email }; };
    try {
      await act(async () => env.root.render(<ToastProvider><ProvisionPersonModal {...createProps} {...prefill} /></ToastProvider>));
      await act(async () => changeValue(field('email'), 'alex@example.com'));
      await act(async () => button('Send invitation').click());
      env.frames();
      assert.equal(requests.length, 0);
      const select = field(kind === 'team' ? 'teamId' : 'organizationId');
      assert.equal(select.disabled, false);
      assert.equal(document.activeElement, select);
      assert.ok(select.querySelector(`option[value="${validId}"]`));
      assert.match(document.body.textContent || '', /not found/);
      await act(async () => changeValue(select, validId));
      assert.equal(field('email').value, 'alex@example.com');
      assert.doesNotMatch(document.body.textContent || '', /not found/);
      await act(async () => button('Send invitation').click());
      assert.deepEqual(requests, [{ email: 'alex@example.com', mode: 'invite_email', platform_role: 'org_user',
        ...(kind === 'team' ? { team_id: validId, team_role: 'team_viewer' } : { organization_id: validId, organization_role: 'org_member' }),
      }]);
    } finally { await env.cleanup(); }
  });
}

test('edit keeps immutable email and sends role and active state without an empty password', async () => {
  const env = setup();
  const requests: object[] = [];
  rbac.accounts.upsert = async payload => { requests.push(payload); return account; };
  try {
    await act(async () => env.root.render(<ToastProvider><AccountEditorDialog account={account} onClose={() => {}} onSuccess={() => {}} /></ToastProvider>));
    assert.equal(document.querySelector<HTMLInputElement>('input[type="email"]')?.disabled, true);
    assert.equal(document.querySelector('details')?.open, false);
    await act(async () => changeValue(field('platformRole'), 'platform_admin'));
    await act(async () => document.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await act(async () => button('Save changes').click());
    assert.deepEqual(requests, [{ email: account.email, role: 'platform_admin', is_active: false }]);
    assert.match(document.body.textContent || '', /Account updated/);
  } finally { await env.cleanup(); }
});

test('edit validates optional password, sends it only when set, and guards pending close and duplicate submit', async () => {
  const env = setup();
  const requests: object[] = [];
  let closes = 0;
  let resolveSave!: (value: unknown) => void;
  rbac.accounts.upsert = payload => { requests.push(payload); return new Promise(resolve => { resolveSave = resolve; }); };
  try {
    await act(async () => env.root.render(<ToastProvider><AccountEditorDialog account={account} onClose={() => closes++} onSuccess={() => {}} /></ToastProvider>));
    await act(async () => document.querySelector('summary')!.click());
    const password = document.querySelector<HTMLInputElement>('input[type="password"]')!;
    await act(async () => changeValue(password, 'short'));
    await act(async () => button('Save changes').click());
    env.frames();
    assert.equal(requests.length, 0);
    assert.equal(document.activeElement, password);
    assert.equal(password.getAttribute('aria-invalid'), 'true');
    await act(async () => changeValue(password, 'sample-password-only'));
    await act(async () => { button('Save changes').click(); button('Save changes').click(); });
    assert.deepEqual(requests, [{ email: account.email, role: 'org_user', is_active: true, password: 'sample-password-only' }]);
    await act(async () => document.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(closes, 0);
    assert.equal(button('Cancel').disabled, true);
    await act(async () => resolveSave(account));
    assert.equal(closes, 1);
    assert.equal(password.value, '');
  } finally { await env.cleanup(); }
});

test('edit rejects stale completion after a different account opens and does not carry password state', async () => {
  const env = setup();
  let closes = 0;
  let resolveSave!: (value: unknown) => void;
  rbac.accounts.upsert = () => new Promise(resolve => { resolveSave = resolve; });
  const other = { ...account, account_id: 'account-2', email: 'other@example.com', is_active: false };
  try {
    await act(async () => env.root.render(<ToastProvider><AccountEditorDialog key={account.account_id} account={account} onClose={() => closes++} onSuccess={() => {}} /></ToastProvider>));
    await act(async () => button('Save changes').click());
    await act(async () => env.root.render(<ToastProvider><AccountEditorDialog key={other.account_id} account={other} onClose={() => closes++} onSuccess={() => {}} /></ToastProvider>));
    await act(async () => resolveSave(account));
    assert.equal(closes, 0);
    assert.equal(document.querySelector<HTMLInputElement>('input[type="email"]')?.value, other.email);
    assert.equal(document.querySelector<HTMLInputElement>('input[type="password"]')?.value, '');
    assert.equal(document.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked, false);
    assert.doesNotMatch(document.body.textContent || '', /Account updated/);
  } finally { await env.cleanup(); }
});

test('scope reads are bounded, paged, and aborted on unmount', async () => {
  const env = setup();
  const requests: { url: URL; signal: AbortSignal | null | undefined }[] = [];
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    requests.push({ url, signal: init?.signal });
    if (url.searchParams.get('offset') === '50') return new Promise(() => {});
    return json({ data: [team], pagination: { ...pagination, total: 60, has_more: true } });
  };
  try {
    await act(async () => env.root.render(<PersonScopeSelect kind="team" value="" onChange={() => {}} onStatusChange={() => {}} />));
    assert.equal(requests[0].url.searchParams.get('limit'), '50');
    assert.ok(document.querySelector('input[type="search"]'));
    await act(async () => button('Next').click());
    assert.equal(requests[1].url.searchParams.get('offset'), '50');
    assert.equal(requests[1].signal?.aborted, false);
    await act(async () => env.root.render(null));
    assert.equal(requests[1].signal?.aborted, true);
  } finally { await env.cleanup(); }
});
